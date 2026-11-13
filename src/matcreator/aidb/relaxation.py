"""Pause a validated Batch Job for a durable, session-owned relaxation choice."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
import sqlite3
from unicodedata import normalize
from uuid import uuid4

from matcreator.aidb.aimd import MD_TYPE
from matcreator.aidb.exports import export_record
from matcreator.aidb.relaxation_inputs import RelaxationInputs, has_vasp_inputs
from matcreator.aidb.service import AIDBError, coverage
from matcreator.tools.aidb_tools import session_service
from matcreator.workspace import get_session_workdir


logger = logging.getLogger(__name__)
PASSIVE_QUERY_SECONDS = 8
FORMATS = ("extxyz", "poscar")
INTERNAL_SPEC_FIELDS = {"required_artifacts", "temperature_requested", "requested_outputs"}
_OUTPUT_NAMES = re.compile(
    r"(?<![A-Za-z0-9_.])(?:vasprun\.xml|OUTCAR|OSZICAR|CONTCAR|CHGCAR|WAVECAR|DOSCAR|"
    r"EIGENVAL|PROCAR|XDATCAR|stdout\.log|stderr\.log)(?![A-Za-z0-9_.])", re.IGNORECASE,
)
_ORIGINAL_POSCAR_REQUEST = re.compile(
    r"(?:original|input)\s+POSCAR|POSCAR\s+(?:input|original)|(?:原始|输入)(?:的)?\s*POSCAR|POSCAR\s*(?:原件|输入文件)",
    re.IGNORECASE,
)
_LOG_REQUEST = re.compile(r"计算日志|运行日志|标准输出|标准错误|(?:calculation|run|vasp) logs?|stdout|stderr",
                          re.IGNORECASE)
_CHOICE_MESSAGE = "有可直接复用的 VASP 结果。请按本轮序号选用、查看详情、继续计算、修改条件或取消。"


def user_id(context):
    return str(getattr(getattr(context, "_invocation_context", None), "user_id", "")
               or context.state.get("user_id") or "default")


def turn_id(context):
    return context.state.get("_vasp_parent_turn") or getattr(context, "invocation_id", None) or getattr(
        getattr(context, "_invocation_context", None), "invocation_id", None
    )


def current_message(context):
    content = getattr(context, "user_content", None)
    if getattr(content, "role", None) != "user":
        return ""
    return "\n".join(part.text for part in (content.parts or []) if getattr(part, "text", None)).strip()


def _human_decision(context):
    message = current_message(context)
    return bool(message) and not message.startswith("REMOTE JOB STATUS UPDATE from the harness.\n")


def _choice_path(context):
    state = context.state
    if not state.get("session_id") or state["session_id"] == "default":
        return None
    workdir = state.get("workdir") or state.get("workspace_dir") or get_session_workdir(state["session_id"])
    # The workspace is shared by some sessions. Keep the journal keyed by both owner and session.
    key = sha256(json.dumps([user_id(context), state["session_id"]]).encode()).hexdigest()
    return Path(workdir) / ".aidb-sessions" / "relaxation-choices" / (key + ".sqlite")


@contextmanager
def choice_transaction(context, *, create=False):
    path = _choice_path(context)
    if path is None or (not create and not path.exists()):
        yield None
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS choice (id INTEGER PRIMARY KEY CHECK (id = 1), value TEXT)")
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            yield connection
    finally:
        connection.close()


def load_choice(connection):
    row = connection.execute("SELECT value FROM choice WHERE id = 1").fetchone() if connection else None
    return json.loads(row[0]) if row else None


def save_choice(connection, choice):
    connection.execute("INSERT OR REPLACE INTO choice (id, value) VALUES (1, ?)", (json.dumps(choice),))


def current_choice(context):
    with choice_transaction(context) as connection:
        return load_choice(connection)


_SELECTION_REPLY = re.compile(
    r"(?:请\s*)?(?:就\s*)?(?:选用|选|用|复用)\s*(?:第\s*)?"
    r"(?:[1-9][0-9]*|[一二两三四五六七八九十百]+)\s*(?:条|行)(?:吧)?"
    r"|(?:please\s+)?(?:use|select|pick|reuse)\s+(?:the\s+)?"
    r"(?:(?:first|second|third|[1-9][0-9]*(?:st|nd|rd|th))\s+(?:row|record|one)|"
    r"(?:row|record)\s+[1-9][0-9]*)",
    re.IGNORECASE,
)


def references_closed_choice(message, choice):
    if not choice or choice["status"] in {"waiting", "continue"}:
        return False
    message = normalize("NFKC", message).strip().rstrip("。.!！").strip()
    return _SELECTION_REPLY.fullmatch(message) is not None


def cancel_pending_choice(workdir, owner_id, session_id, *, step_number=None):
    """Apply the existing session/step cancellation actions to a waiting choice."""
    from types import SimpleNamespace

    context = SimpleNamespace(state={"workdir": str(workdir), "user_id": owner_id, "session_id": session_id})
    with choice_transaction(context) as connection:
        choice = load_choice(connection)
        if (choice and choice["status"] in {"waiting", "continue"}
                and (step_number is None or choice["step_number"] == step_number)):
            save_choice(connection, {**choice, "status": "cancel"})
            return choice
    return None


def reuse_delivery_for_turn(context, record_id, formats):
    choice = current_choice(context)
    if not choice or choice["status"] != "reused" or choice["decision_turn"] != turn_id(context):
        return None
    receipt = choice["delivery"]
    if receipt["record_id"] == record_id and set(formats) <= receipt["files"].keys():
        return receipt
    return None


def _scope(context):
    state = context.state
    node_id = str(state.get("_graph_exec_node_id") or "step").rsplit("__node_", 1)[-1]
    return {"graph_id": state.get("_vasp_graph_id"), "node_id": node_id}


def read_spec_inputs(spec):
    return RelaxationInputs.from_batch_spec(spec)


def _prepared_revision(context, spec, inputs):
    if inputs.calculation_type != "nscf":
        if {"upstream_record_id", "upstream_job_id"} & spec.keys():
            raise AIDBError("上游 SCF 记录仅用于 NSCF 能带计算", "invalid_input")
        return inputs.revision

    from matcreator.aidb.managed_vasp import resolve_band_dependency, resolve_band_record_dependency
    from matcreator.agents.execution_agent.remote_job_tools import _service

    job_id, record_id = spec.get("upstream_job_id"), spec.get("upstream_record_id")
    if bool(job_id) == bool(record_id):
        raise AIDBError("NSCF 需要上游 SCF 的记录 ID 或作业 ID，以及已核实的 CHGCAR", "invalid_input")
    store = _service().store
    if job_id:
        dependency = resolve_band_dependency(context.state.get("session_id"), inputs, user_id(context),
                                             job_id=job_id, store=store)
    else:
        dependency = resolve_band_record_dependency(session_service(context), record_id, inputs, user_id(context),
                                                    store=store)
    return {**inputs.revision, "upstream_scf": dependency}


def choice_receipt(choice):
    return {"status": "waiting_choice", "choice_id": choice["id"], "candidates": choice["candidates"],
            "message": _CHOICE_MESSAGE}


def _required_restart_outputs(context, spec, inputs):
    from matcreator.aidb.managed_vasp import RESTART_OUTPUTS

    if inputs.calculation_type != "static":
        return []
    text = context.state.get("_vasp_requested_output_text") or current_message(context)
    required = set(spec.get("out_files") or []) & RESTART_OUTPUTS
    if re.search(r"CHGCAR|电荷密度|charge[- ]density", text, re.IGNORECASE):
        required.add("CHGCAR")
    if re.search(r"能带|band[- ]?(?:structure|path)|NSCF", text, re.IGNORECASE):
        required.add("CHGCAR")
    if re.search(r"WAVECAR|波函数文件|wavefunction file", text, re.IGNORECASE):
        required.add("WAVECAR")
    return sorted(required)


def _formats(spec):
    return (*FORMATS, "temperature") if spec.get("temperature_requested") else FORMATS


def _record_artifacts(inputs, revision, record, required, owner):
    from matcreator.aidb.managed_vasp import resolve_band_artifacts, resolve_restart_artifacts
    from matcreator.agents.execution_agent.remote_job_tools import _service

    if inputs.calculation_type == "nscf":
        return resolve_band_artifacts(record, revision["upstream_scf"], owner, store=_service().store)
    return resolve_restart_artifacts(record, required, owner, store=_service().store) if required else {}


def _verify_requested_outputs(spec, artifacts):
    # out_files names are case-sensitive. Natural-language mentions are not.
    missing = set(spec.get("out_files") or ()) - artifacts.keys()
    available = {name.upper() for name in artifacts}
    missing.update(name for name in spec.get("requested_outputs", ()) if name.upper() not in available)
    if missing:
        raise AIDBError("记录不能交付本次需要的 VASP 产物：" + "、".join(sorted(missing)), "not_ready")


def _deliverable(inputs, revision, record, spec, owner):
    try:
        if not inputs.matches(record) or export_record(record, _formats(spec))[1]:
            return False
        artifacts = _record_artifacts(inputs, revision, record, spec.get("required_artifacts", ()), owner)
        _verify_requested_outputs(spec, artifacts)
        return True
    except (AIDBError, OSError, AttributeError, KeyError, IndexError, OverflowError, TypeError, ValueError):
        return False


def _continue_result(context, connection, choice, spec, submit, inputs):
    revision = _prepared_revision(context, spec, inputs) if inputs else None
    if not revision or revision != choice["revision"] or spec != choice["spec"]:
        return {"status": "error", "message": "VASP 输入或提交配置已改变。请重新准备输入并查找候选。"}
    if turn_id(context) != choice["decision_turn"]:
        return {"status": "error", "message": "请在当前对话明确继续本次计算。"}
    result = submit(choice["id"], revision, choice["candidates"]["binding_id"])
    if result.get("job_id"):
        save_choice(connection, {**choice, "status": "submitted", "submission": result})
    return result


def guard_relaxation_submission(context, spec, submit):
    """Call the existing submission function or return the pending choice receipt."""
    scope = _scope(context)
    with choice_transaction(context) as connection:
        choice = load_choice(connection)
        if choice and choice["status"] in {"waiting", "continue"} and choice["scope"] != scope:
            return {"status": "error", "message": "请先处理当前 VASP 候选，再提交其他计算。"}
        if choice and choice["scope"] == scope:
            if choice["status"] == "waiting":
                return choice_receipt(choice)
            if choice["status"] == "continue":
                try:
                    inputs = read_spec_inputs(spec)
                except (AIDBError, OSError, ValueError, TypeError):
                    inputs = None
                spec.update({key: choice["spec"][key] for key in INTERNAL_SPEC_FIELDS if key in choice["spec"]})
                try:
                    return _continue_result(context, connection, choice, spec, submit, inputs)
                except (AIDBError, OSError, ValueError) as exc:
                    return {"status": "error", "message": str(exc)}
            if choice["status"] == "submitted":
                if choice.get("decision_turn") == turn_id(context):
                    return choice["submission"]
                prior = choice["spec"]
                if {key: value for key, value in prior.items() if key not in INTERNAL_SPEC_FIELDS} == spec:
                    try:
                        inputs = read_spec_inputs(spec)
                        revision = _prepared_revision(context, spec, inputs) if inputs else None
                    except (AIDBError, OSError, ValueError, TypeError):
                        revision = None
                    if revision == choice["revision"]:
                        return choice["submission"]
            if choice["created_turn"] == turn_id(context) or choice.get("decision_turn") == turn_id(context):
                if choice["status"] != "revise" or choice.get("decision_turn") != turn_id(context):
                    return {"status": "error", "message": "该次 VASP 选择已经结束。"}

    try:
        inputs = read_spec_inputs(spec)
    except AIDBError as exc:
        return {"status": "error", "message": str(exc)}
    except (OSError, ValueError, TypeError):
        inputs = None
    if inputs is None:
        if {"upstream_record_id", "upstream_job_id"} & spec.keys():
            return {"status": "error", "message": "无法核实 NSCF 输入或上游 SCF 的 CHGCAR。"}
        if has_vasp_inputs(spec):
            return {"status": "error", "message": "无法核实准备好的 VASP 输入或启动命令。请使用受托管的输入目录及启动方式。"}
        return submit(None)
    try:
        revision = _prepared_revision(context, spec, inputs)
    except (AIDBError, OSError, ValueError) as exc:
        return {"status": "error", "message": str(exc)}

    human_message = context.state.get("_vasp_human_message", current_message(context))
    if references_closed_choice(human_message, choice):
        return {"status": "closed", "message": "上一次的 VASP 候选已关闭，不能再按原序号选用。"}

    required = _required_restart_outputs(context, spec, inputs)
    if required:
        spec["required_artifacts"] = required
    request_text = context.state.get("_vasp_requested_output_text") or current_message(context)
    canonical_names = {"VASPRUN.XML": "vasprun.xml", "STDOUT.LOG": "stdout.log", "STDERR.LOG": "stderr.log"}
    requested_outputs = {canonical_names.get(name.upper(), name.upper()) for name in _OUTPUT_NAMES.findall(request_text)}
    if _ORIGINAL_POSCAR_REQUEST.search(request_text):
        requested_outputs.add("POSCAR")
    if inputs.calculation_type == "nscf":
        # The SCF step supplies these files. Explicit NSCF out_files still need delivery.
        requested_outputs.difference_update(revision["upstream_scf"]["sha256"])
    if _LOG_REQUEST.search(request_text) and not requested_outputs & {"stdout.log", "stderr.log"}:
        explicit_logs = set(spec.get("out_files") or ()) & {"log", "err", "stdout.log", "stderr.log"}
        if not explicit_logs:
            requested_outputs.add("log")
    if requested_outputs:
        spec["requested_outputs"] = sorted(requested_outputs)
    if inputs.calculation_type == MD_TYPE:
        if re.search(r"温度\s*(?:曲线|图|随时间|变化|历程|数据|序列|记录)|"
                     r"(?:每步|逐步)(?:的)?温度|(?:temperature|thermal)[ -]+"
                     r"(?:plot|curve|profile|history|trace|series|trajectory|vs\.?\s*time|data)|"
                     r"(?:plot|graph|chart)\b[^.。\n]{0,45}\btemperature", request_text, re.I):
            spec["temperature_requested"] = True

    if not turn_id(context):
        try:
            binding_id = session_service(context).binding()["binding_id"]
        except (AIDBError, OSError, ValueError):
            binding_id = None
        revision_key = sha256(json.dumps(revision, sort_keys=True).encode()).hexdigest()
        return submit(revision_key, revision, binding_id)

    with choice_transaction(context, create=True) as connection:
        latest = load_choice(connection)
        if latest != choice:
            if latest and latest["scope"] == scope and latest["status"] == "waiting":
                return choice_receipt(latest)
            return {"status": "error", "message": "VASP 步骤状态已经改变，请核对本轮选择。"}
        binding_id = None
        try:
            service = session_service(context, query_timeout=PASSIVE_QUERY_SECONDS)
            binding_id = service.binding()["binding_id"]
            result = service.query({"formula": inputs.formula, "calculation_type": inputs.calculation_type},
                                   deduplicate=False)
            service.binding(binding_id)
            records = [record for record in result["records"]
                       if _deliverable(inputs, revision, record, spec, user_id(context))]
            logger.info("VASP lookup session=%s scope=%s outcome=%s native_count=%s",
                        service.session_id, scope, "matches" if records else "no_match", result["report"]["match_count"])
            refreshed = read_spec_inputs(spec)
            if not refreshed or refreshed.revision != inputs.revision:
                return {"status": "error", "message": "VASP 输入在检查期间发生改变，请核对后重新提交。"}
            if inputs.calculation_type == "nscf":
                try:
                    refreshed_revision = _prepared_revision(context, spec, refreshed)
                except (AIDBError, OSError, ValueError) as exc:
                    return {"status": "error", "message": str(exc)}
                if refreshed_revision != revision:
                    return {"status": "error", "message": "NSCF 的上游 SCF 文件在检查期间发生改变。"}
            if records:
                filtered = {**result, "records": records, "excluded": [], "matched_count": len(records),
                            "report": {**result["report"], "match_count": len(records)}, "coverage": coverage(records),
                            "operation": "relaxation_candidates"}
                reference = service.publish(filtered)
                reference["user_id"] = user_id(context)
                choice = {"id": uuid4().hex, "status": "waiting", "created_turn": turn_id(context), "scope": scope,
                          "revision": revision, "spec": spec, "candidates": reference,
                          "action": context.state.get("_vasp_step_action"),
                          "skills": context.state.get("_vasp_step_skills", []),
                          "step_id": context.state.get("_graph_exec_node_id"),
                          "step_number": context.state.get("step_number")}
                save_choice(connection, choice)
                return choice_receipt(choice)
        except (AIDBError, OSError, KeyError, ValueError, TypeError) as exc:
            logger.info("VASP lookup session=%s scope=%s outcome=%s reason=%s",
                        context.state.get("session_id"), scope, getattr(exc, "status", "failed"), exc)
        if latest and latest["status"] in {"revise", "submitted"} and latest["scope"] == scope:
            revision_id = latest["id"] if latest["status"] == "revise" else uuid4().hex
            result = submit(revision_id, revision, binding_id)
            if result.get("job_id"):
                save_choice(connection, {**latest, "id": revision_id, "status": "submitted", "submission": result,
                                         "spec": spec, "revision": revision, "decision_turn": turn_id(context)})
            return result
        revision_key = sha256(json.dumps(
            {"graph": scope["graph_id"], "inputs": revision}, sort_keys=True
        ).encode()).hexdigest()
        return submit(revision_key, revision, binding_id)


def _candidate_id(choice, ordinal, record_id):
    ordered = [item["record_id"] for item in choice["candidates"]["records"]]
    from_ordinal = ordered[ordinal - 1] if type(ordinal) is int and 1 <= ordinal <= len(ordered) else None
    if ordinal is not None:
        return from_ordinal if not record_id or from_ordinal == record_id else None
    if record_id:
        return record_id if record_id in ordered else None
    return ordered[0] if len(ordered) == 1 else None


def resolve_choice(context, action, choice_id, ordinal=None, record_id=None):
    """Fresh-read a record or make one decision against the current durable choice."""
    with choice_transaction(context) as connection:
        choice = load_choice(connection)
        closed = {"status": "closed", "message": "本轮没有有效的 VASP 候选选择。"}
        if not choice or choice["id"] != choice_id or choice["status"] not in {"waiting", "continue"}:
            return closed
        graph_id = choice["scope"]["graph_id"]
        if graph_id is not None:
            from matcreator.agents.execution_graph_state import get_execution_graph

            graph = get_execution_graph(context.state)
            if not graph or graph.get("graph_id") != graph_id:
                save_choice(connection, {**choice, "status": "cancel"})
                return closed
        decision_turn = turn_id(context)
        if not _human_decision(context) or not decision_turn or choice["created_turn"] == decision_turn:
            return {"status": "waiting_choice", "message": "请等用户在下一轮明确选择。"}

        if action in {"cancel", "revise"}:
            choice = {**choice, "status": action, "decision_turn": decision_turn}
            save_choice(connection, choice)
            return {"status": "cancelled" if action == "cancel" else "revise"}

        try:
            reference = choice["candidates"]
            if action == "detail":
                service = session_service(context)
                service.binding(reference["binding_id"])
                selected = _candidate_id(choice, ordinal, record_id)
                if selected is None:
                    return {"status": "clarify", "message": "请明确要查看本轮哪一条记录。"}
                detail = service.publish(service.detail(selected, binding_id=reference["binding_id"]))
                detail["user_id"] = user_id(context)
                return {"status": "waiting_choice", "detail": detail}

            inputs = read_spec_inputs(choice["spec"])
            if not inputs or _prepared_revision(context, choice["spec"], inputs) != choice["revision"]:
                save_choice(connection, {**choice, "status": "revise", "decision_turn": decision_turn})
                return {"status": "stale_input", "message": "工作区输入已改变，请重新准备并核对。"}

            if action == "continue":
                save_choice(connection, {**choice, "status": "continue", "decision_turn": decision_turn})
                return {"status": "ready_to_continue",
                        "message": "已保留本次输入。请按原有计划和提交授权继续同一步骤。"}
            if action != "select":
                return {"status": "clarify", "message": "请明确是查看、选用、继续计算、修改条件还是取消。"}

            service = session_service(context)
            service.binding(reference["binding_id"])
            selected = _candidate_id(choice, ordinal, record_id)
            if selected is None:
                return {"status": "clarify", "message": "请明确选用本轮哪一条记录。"}
            energy = None
            required = choice["spec"].get("required_artifacts", [])

            def verify(record):
                nonlocal energy
                staged = read_spec_inputs(choice["spec"])
                if not staged or _prepared_revision(context, choice["spec"], staged) != choice["revision"]:
                    raise AIDBError("工作区输入在读取期间发生改变", "stale_input")
                if not staged.matches(record):
                    raise AIDBError("读回记录已无法核实为完全匹配", "not_ready")
                energy = record["energy"]
                artifacts = _record_artifacts(staged, choice["revision"], record, required, user_id(context))
                _verify_requested_outputs(choice["spec"], artifacts)
                return artifacts

            export = service.export(selected, _formats(choice["spec"]), result_id=reference["result_id"],
                                    binding_id=reference["binding_id"], verify=verify)
            export["user_id"] = user_id(context)
            save_choice(connection, {**choice, "status": "reused", "decision_turn": decision_turn, "delivery": export})
            return {"status": "reused", "delivery": export, "energy_ev_per_cell": energy,
                    "artifact_paths": list(export.get("artifact_paths", {}).values()),
                    "calculation_type": inputs.calculation_type,
                    **({f"upstream_{key}": value for key, value in choice["revision"]["upstream_scf"].items()
                        if key in {"record_id", "job_id"}} if inputs.calculation_type == "nscf" else {})}
        except (AIDBError, OSError, AttributeError, IndexError, KeyError, TypeError, ValueError) as exc:
            status = getattr(exc, "status", "failed")
            if action != "detail" and status in {"stale_binding", "stale_input"}:
                save_choice(connection, {**choice, "status": "revise", "decision_turn": decision_turn})
            return {"status": status, "message": str(exc)}
