"""Tracked VASP EOS workflow: one relaxation followed by independent SCF volume points."""
from __future__ import annotations

import csv
from hashlib import sha256
import json
from math import isclose, isfinite
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from google.adk.tools.tool_context import ToolContext
from pymatgen.core import Structure
from pymatgen.io.vasp import Poscar

from ...aidb.managed_vasp import _completion_lock
from ...aidb.relaxation import (
    PASSIVE_QUERY_SECONDS,
    _deliverable,
    _formats,
    _record_artifacts,
    _required_restart_outputs,
    current_message,
    turn_id,
)
from ...aidb.relaxation_inputs import RelaxationInputs, vasp_launch
from ...aidb.service import AIDBError, _save
from ...aidb.vasp_archive import file_digest
from ...tools.aidb_tools import session_service
from . import remote_job_tools as jobs
from .matpes_tools import (
    _BATCH_ERRORS,
    _async_tool,
    _collect_member,
    _launch_member,
    _lookup_member,
    _notification,
    _query_binding,
    _workspace,
    _workspace_child,
)

_PENDING = {"ready", "created", "queued", "running", "succeeded", "collecting"}
_RECEIPT_FIELDS = ("member_id", "status", "reason", "revision", "record_id", "source_id",
                   "job_id", "external_id", "archive_status", "candidates", "volume_factor",
                   "target_volume_angstrom3", "result", "delivery")


def _path(context, eos_id):
    if not re.fullmatch(r"[a-f0-9]{32}", eos_id):
        raise ValueError("无效的 EOS 标识")
    owner = json.dumps([jobs._owner_id(context), context.state.get("session_id")])
    return jobs.ADK_DIR / "eos" / sha256(owner.encode()).hexdigest() / (eos_id + ".json")


def _inputs(spec, kind):
    inputs = RelaxationInputs.from_batch_spec(spec)
    if inputs is None or inputs.calculation_type != kind:
        raise ValueError(f"EOS 需要可核实的 VASP {kind} 输入")
    if (inputs.file_hashes.keys() & {"CHGCAR", "WAVECAR"} or inputs.incar.get("ICHARG", 2) != 2
            or inputs.incar.get("ISTART", 0) != 0):
        raise ValueError("EOS 输入需要从各自的结构启动，无须提供 CHGCAR 或 WAVECAR")
    if kind == "relaxation" and inputs.incar.get("ISIF") not in {3, 6, 7, 8}:
        raise ValueError("EOS 初始弛豫需要允许晶胞体积变化的 ISIF")
    return inputs


def _readback(record):
    source = record["source_metadata"]["vasp_facts"]["source"]
    labels = record["source_metadata"]["vasp_facts"]["labels"]
    submitted = source["submitted_inputs"]
    return {
        "structure": record["structure"], "energy_ev_per_cell": record["energy"],
        "energy_definition": labels["energy_definition"],
        "source_id": record["source_id"], "provider": submitted["provider"],
        "run_id": submitted["run_id"],
        "input_revision": {key: submitted.get(key) for key in ("sha256", "executable", "launcher_sha256")},
    }


def _deliver_result(member, result):
    structure = Structure.from_dict(result["structure"])
    volume, energy = structure.volume, result["energy_ev_per_cell"]
    if (not isfinite(volume) or volume <= 0 or not isfinite(energy)
            or result["energy_definition"] != "e_0_energy"
            or result["input_revision"] != member["revision"]):
        raise ValueError("EOS 成员的输入版本、结构体积或能量无法核实")
    if member["member_id"] != "relaxation":
        if not isclose(volume, member["target_volume_angstrom3"], rel_tol=1e-7, abs_tol=1e-7):
            raise ValueError("EOS 静态结果体积与本点的目标体积不一致")
    member["result"] = {**result, "volume_angstrom3": volume}


def _candidate(context, member, inputs, record):
    if not _deliverable(inputs, member["revision"], record, member["spec"], jobs._owner_id(context)):
        return False
    try:
        _deliver_result({**member}, _readback(record))
        return True
    except _BATCH_ERRORS:
        return False


def _prepare(context, state, member, inputs, service, queries):
    member.update(status="ready", revision=inputs.revision, prepared_turn=turn_id(context))
    reference = _lookup_member(
        context, service, state["binding_id"], queries, inputs,
        lambda record: _candidate(context, member, inputs, record),
        operation="eos_candidates", eos_member=member["member_id"],
    )
    if reference:
        member.update(status="waiting_choice", candidates=reference)


def _launch(context, state, member):
    index = member["member_id"]
    group = state["group_id"] if index != "relaxation" and not member.get("recalculate") else None
    try:
        inputs = _inputs(member["spec"], "relaxation" if index == "relaxation" else "static")
        if inputs.revision != member["revision"]:
            raise ValueError("EOS 成员的已核实输入发生变化")
    except _BATCH_ERRORS as exc:
        member.update(status="failed", reason=str(exc))
        return
    _launch_member(context, member, state["binding_id"], group, state["node_id"],
                   f'{state["eos_id"]}:{index}', eos_member={
                       "eos_id": state["eos_id"], "member_id": index,
                       **{key: member[key] for key in ("volume_factor", "target_volume_angstrom3") if key in member},
                   })


def _receipt(state):
    def summarize(member):
        fields = {key: member[key] for key in _RECEIPT_FIELDS if key in member}
        if "result" in fields:
            fields["result"] = {key: value for key, value in fields["result"].items() if key != "structure"}
        return fields

    relaxation = summarize(state["relaxation"])
    points = [summarize(member) for member in state["points"]]
    statuses = {point["status"] for point in points}
    if not relaxation.get("result"):
        if relaxation["status"] == "waiting_choice":
            status = "awaiting_choice"
        elif relaxation["status"] in _PENDING:
            status = "running"
        else:
            status = "blocked_relaxation"
    elif not state.get("points_prepared"):
        status = "ready_for_volume_points"
    elif statuses & _PENDING:
        status = "running"
    elif "waiting_choice" in statuses:
        status = "awaiting_choice"
    elif all(point.get("result") for point in points):
        status = "complete_with_archive_issues" if "computed_archive_failed" in statuses else "complete"
    else:
        status = "incomplete"
    return {"status": status, "eos_id": state["eos_id"], "group_id": state["group_id"],
            "relaxation": relaxation, "points": points,
            "available_points": sum("result" in point for point in points),
            "missing_points": [point["member_id"] for point in points if "result" not in point]}


def submit_eos(
    tool_context: ToolContext, *, relaxation_path: str, static_template_path: str,
    name: str, image: str, command: str, project_id: int | None = None,
    machine_type: str | None = None, sku_id: int | None = None,
    volume_percent: float = 5.0, point_count: int = 11,
    max_run_time: str = "24h", max_wait_time: str = "30m",
) -> dict:
    """Start a VASP EOS using prepared relaxation and static template directories.

    Follow the existing submission authorization for the relaxation and the
    requested number of static points. Paths are relative to the workspace;
    static INCAR, KPOINTS (or KSPACING), POTCAR and optional launch script form
    the template. The template POSCAR is never used as a relaxed structure.
    Defaults sample 11 uniformly spaced volumes from -5 to +5 percent.
    Report eos_id and any exact match. Collect using this same eos_id.
    """
    try:
        if _notification(tool_context):
            raise ValueError("作业通知只能回收已有 EOS")
        if not (3 <= point_count <= 49 and isfinite(volume_percent) and 0 < volume_percent < 100):
            raise ValueError("EOS 需要 3 至 49 个体积点，体积变化百分比须大于 0 且小于 100")
        project_id = project_id or os.environ.get("BOHRIUM_PROJECT_ID")
        if (not (tool_context.state.get("session_id") and project_id and name and image and command)
                or bool(machine_type) == (sku_id is not None)):
            raise ValueError("EOS 需要会话、项目、名称、镜像、命令及一种机型标识")
        workspace = _workspace(tool_context)
        base = {"input_root": str(workspace), "name": name, "image": image, "command": command,
                "project_id": project_id, "machine_type": machine_type, "sku_id": sku_id, "out_files": None,
                "max_run_time": max_run_time, "max_wait_time": max_wait_time}
        relax_spec = {**base, "name": name + "-relaxation",
                      "input_path": str(_workspace_child(workspace, workspace / relaxation_path).relative_to(workspace))}
        static_spec = {**base,
                       "input_path": str(_workspace_child(workspace, workspace / static_template_path).relative_to(workspace))}
        relaxation, static = _inputs(relax_spec, "relaxation"), _inputs(static_spec, "static")
        required = _required_restart_outputs(tool_context, static_spec, static)
        if required:
            static_spec["required_artifacts"] = required
        if relaxation.structure.species != static.structure.species:
            raise ValueError("EOS 弛豫与静态模板的元素和原子顺序必须相同")
        node_id = jobs._node_id(tool_context)
        identity = [tool_context.state.get("_vasp_graph_id"), node_id, relax_spec, relaxation.revision,
                    static_spec, static.revision, volume_percent, point_count]
        eos_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        path = _path(tool_context, eos_id)
        with _completion_lock(path):
            if path.is_file():
                state = json.loads(path.read_text(encoding="utf-8"))
            else:
                service, binding_id = _query_binding(tool_context)
                member = {"member_id": "relaxation", "spec": relax_spec}
                span = volume_percent / 100
                state = {"eos_id": eos_id, "node_id": node_id, "group_id": None,
                         "binding_id": binding_id, "relaxation": member, "static_spec": static_spec,
                         "static_revision": static.revision,
                         "points": [{"member_id": str(index + 1), "status": "blocked",
                                     "volume_factor": 1 + span * (2 * index / (point_count - 1) - 1)}
                                    for index in range(point_count)]}
                _prepare(tool_context, state, member, relaxation, service, {})
                _save(path, state)
            if state["relaxation"]["status"] == "ready":
                _launch(tool_context, state, state["relaxation"])
                _save(path, state)
            return _receipt(state)
    except _BATCH_ERRORS as exc:
        return {"status": getattr(exc, "status", "error"), "message": str(exc)}


def _point_inputs(context, state, path):
    workspace = _workspace(context)
    spec = state["static_spec"]
    template = workspace / spec["input_path"]
    static = _inputs(spec, "static")
    if static.revision != state["static_revision"]:
        raise ValueError("EOS 静态输入模板已改变，请重新核对")
    relaxed = Structure.from_dict(state["relaxation"]["result"]["structure"])
    if relaxed.species != static.structure.species:
        raise ValueError("EOS 弛豫结果与静态模板的元素或原子顺序不一致")
    parent = workspace / f'eos-{state["eos_id"]}'
    parent.mkdir(exist_ok=True)
    if parent.is_symlink() or parent.resolve() != parent:
        raise ValueError("EOS 目录必须为工作区内的普通目录")
    service = None
    if state["binding_id"]:
        try:
            service = session_service(context, query_timeout=PASSIVE_QUERY_SECONDS)
            service.binding(state["binding_id"])
        except _BATCH_ERRORS:
            pass
    queries = {}
    filenames = set(static.file_hashes) - {"POSCAR"}
    _, launcher = vasp_launch(template, spec["command"])
    if launcher is not None:
        filenames.add(launcher.name)
    for member in state["points"]:
        point_id = member["member_id"]
        folder = parent / f"point-{point_id}-input"
        point_spec = {**spec, "input_path": str(folder.relative_to(workspace)),
                      "name": f'{spec["name"]}-point-{point_id}'}
        structure = relaxed.copy()
        structure.scale_lattice(relaxed.volume * member["volume_factor"])
        member.update(spec=point_spec, target_volume_angstrom3=structure.volume)
        try:
            inputs = _stage_point(template, parent, filenames, folder, static, point_spec, structure)
        except _BATCH_ERRORS as exc:
            member.update(status="failed", reason=str(exc))
            continue
        _prepare(context, state, member, inputs, service, queries)
    ready = [point for point in state["points"] if point["status"] == "ready"]
    if len(ready) > 1:
        group = jobs.create_remote_job_group(context, name=f'eos:{state["eos_id"]}', expected_jobs=len(ready))
        if group["status"] != "ready":
            raise ValueError(group.get("message") or "无法创建 EOS 作业组")
        state["group_id"] = group["group_id"]
    state["points_prepared"] = True
    _save(path, state)


def _stage_point(template, parent, filenames, folder, static, spec, structure):
    # A crash before the first launch can leave a fully staged directory behind.
    # Verify the existing bytes against the intended input before reusing it.
    with TemporaryDirectory(dir=parent) as temporary:
        staged = Path(temporary) / "inputs"
        staged.mkdir()
        for filename in sorted(filenames):
            shutil.copyfile(template / filename, staged / filename)
        Poscar(structure).write_file(staged / "POSCAR")
        intended = RelaxationInputs.read(staged, spec["command"])
        if (intended is None or intended.launcher_hash != static.launcher_hash
                or any(intended.file_hashes[key] != static.file_hashes[key] for key in filenames
                       if key in static.file_hashes)):
            raise ValueError("EOS 静态模板在准备期间改变")
        if folder.is_symlink():
            raise ValueError("EOS 体积点输入目录不能为符号链接")
        if not folder.exists():
            staged.rename(folder)
        inputs = _inputs(spec, "static")
        if inputs.revision != intended.revision:
            raise ValueError("EOS 体积点的已准备输入与目标结构不一致")
        return inputs


def _collect(context, state):
    store = jobs._service().store
    tracked = {}
    for job in store.list_jobs(owner_id=jobs._owner_id(context), session_id=context.state["session_id"]):
        member = job["specification"].get("managed_vasp", {}).get("eos_member")
        if member and member["eos_id"] == state["eos_id"]:
            tracked[member["member_id"]] = job
    references = []
    for member in (state["relaxation"], *state["points"]):
        point_id = member["member_id"]
        if not member.get("job_id") and point_id in tracked:
            job = tracked[point_id]
            member.update(job_id=job["job_id"], external_id=job["external_id"])
        if not member.get("job_id"):
            continue
        try:
            completion, reference = _collect_member(
                context, member, f'eos-{state["eos_id"]}/{point_id}-output', store,
            )
            if reference:
                references.append(reference)
            if completion and completion.get("eos_result"):
                _deliver_result(member, completion["eos_result"])
                if completion["status"] != "verified":
                    member["status"] = "computed_archive_failed"
        except _BATCH_ERRORS as exc:
            member.update(status="unavailable", reason=str(exc))
    return references


def _artifacts(context, receipt):
    """Publish the measured samples and their provenance, including missing rows."""
    workspace = _workspace(context)
    output = Path(context.state.get("output_dir") or workspace).resolve()
    if not output.is_relative_to(workspace):
        raise ValueError("EOS 输出目录必须位于工作区内")
    destination = output / f'eos-{receipt["eos_id"]}' / "results"
    if destination.resolve() != destination:
        raise ValueError("EOS 汇总目录必须为工作区内的普通目录")
    destination.mkdir(parents=True, exist_ok=True)
    manifest = destination / "eos-members.json"
    _save(manifest, receipt)
    points = [point for point in receipt["points"] if "result" in point]
    artifacts = {"artifact_paths": [str(manifest)]}
    if not points:
        return artifacts

    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    with TemporaryDirectory(dir=destination) as temporary:
        temporary = Path(temporary)
        data = temporary / "energy-volume.csv"
        with data.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("point", "volume_angstrom3", "energy_ev_per_cell", "energy_definition",
                             "record_id", "source_id", "provider", "run_id", "job_id", "input_revision"))
            for point in points:
                result = point["result"]
                writer.writerow((point["member_id"], result["volume_angstrom3"], result["energy_ev_per_cell"],
                                 result["energy_definition"], point.get("record_id"), result["source_id"],
                                 result["provider"], result["run_id"], point.get("job_id"),
                                 json.dumps(point["revision"], sort_keys=True)))
        figure = Figure(figsize=(7, 4.5), layout="constrained")
        FigureCanvasAgg(figure)
        axes = figure.subplots()
        # NaNs break the line across any missing sample.
        axes.plot([point.get("result", {}).get("volume_angstrom3", point["target_volume_angstrom3"])
                   for point in receipt["points"]],
                  [point.get("result", {}).get("energy_ev_per_cell", float("nan"))
                   for point in receipt["points"]], "o-", color="#256d85")
        axes.set(xlabel="Volume (Å³/cell)", ylabel="Energy (eV/cell)",
                 title=f'E–V samples ({len(points)}/{len(receipt["points"])} points)')
        plot = temporary / "energy-volume.png"
        figure.savefig(plot, dpi=160)
        final_plot = plot.replace(destination / plot.name)
        final_data = data.replace(destination / data.name)
    artifacts["artifact_paths"].extend((str(final_data), str(final_plot)))
    return {**artifacts, "plot_path": str(final_plot)}


def collect_eos(tool_context: ToolContext, eos_id: str, advance: bool = False) -> dict:
    """Collect or resume the existing EOS by ID. Return verified data and a curve.

    Set advance=true only during a human turn when the volume jobs were authorized.
    After the verified relaxation arrives, this prepares and starts unmatched
    volume points. Status notifications use advance=false and only collect.
    Preserve the ID across turns. Report every missing point and archive failure.
    """
    try:
        path = _path(tool_context, eos_id)
        with _completion_lock(path):
            state = json.loads(path.read_text(encoding="utf-8"))
            completions = _collect(tool_context, state)
            _save(path, state)
            message = tool_context.state.get("_vasp_human_message") or current_message(tool_context)
            if advance and message and not _notification(tool_context):
                if state["relaxation"].get("result") and not state.get("points_prepared"):
                    _point_inputs(tool_context, state, path)
                for member in (state["relaxation"], *state["points"]):
                    if member["status"] == "ready":
                        _launch(tool_context, state, member)
                        _save(path, state)
            receipt = _receipt(state)
            return {**receipt, **_artifacts(tool_context, receipt), "aidb_completions": completions}
    except _BATCH_ERRORS as exc:
        return {"status": getattr(exc, "status", "error"), "message": str(exc)}


def _select_member(context, member, binding_id, record_id):
    reference = member["candidates"]
    allowed = [row["record_id"] for row in reference["records"]]
    record_id = record_id or (allowed[0] if len(allowed) == 1 else None)
    if record_id not in allowed:
        raise ValueError("请明确该 EOS 成员选用的候选记录")
    spec = member["spec"]
    owner = jobs._owner_id(context)
    selected = dict(member)

    def verify(record):
        staged = _inputs(spec, "relaxation" if member["member_id"] == "relaxation" else "static")
        if staged.revision != member["revision"]:
            raise AIDBError("该 EOS 成员输入在读取期间发生改变", "stale_input")
        if not staged.matches(record):
            raise AIDBError("该记录无法核实为可交付的完全匹配", "not_ready")
        _deliver_result(selected, _readback(record))
        return _record_artifacts(staged, staged.revision, record, spec.get("required_artifacts", ()), owner)

    delivery = session_service(context).export(
        record_id, _formats(spec), result_id=reference["result_id"], binding_id=binding_id, verify=verify,
    )
    member.update(status="reused", record_id=record_id, source_id=selected["result"]["source_id"],
                  result=selected["result"], delivery={**delivery, "user_id": owner})


def resolve_eos_member(
    tool_context: ToolContext, eos_id: str, member_id: str, action: str, record_id: str | None = None,
) -> dict:
    """Resolve a pending EOS candidate after a subsequent explicit human choice.

    member_id is relaxation or the 1-based point ID string. action is select,
    recalculate, or cancel. Specify record_id if that member has multiple
    candidates. Selection includes a download receipt in the member result.
    Recalculation follows the original submission authorization.
    """
    try:
        path = _path(tool_context, eos_id)
        with _completion_lock(path):
            state = json.loads(path.read_text(encoding="utf-8"))
            member = next((row for row in (state["relaxation"], *state["points"])
                           if row["member_id"] == member_id), None)
            if not member or member["status"] != "waiting_choice":
                raise ValueError("该 EOS 成员没有待处理的候选")
            if (_notification(tool_context) or not turn_id(tool_context)
                    or turn_id(tool_context) == member["prepared_turn"]
                    or not (tool_context.state.get("_vasp_human_message") or current_message(tool_context))):
                raise ValueError("需要用户在后续对话中明确决定该 EOS 成员")
            if action == "cancel":
                member.update(status="cancelled", reason="用户取消该 EOS 成员")
            elif action == "select":
                _select_member(tool_context, member, state["binding_id"], record_id)
            elif action == "recalculate":
                inputs = _inputs(member["spec"], "relaxation" if member_id == "relaxation" else "static")
                if inputs.revision != member["revision"]:
                    raise AIDBError("该 EOS 成员输入已改变，请重新准备", "stale_input")
                member.update(status="ready", recalculate=True)
            else:
                raise ValueError("EOS 成员操作应为选用、重算或取消")
            member.pop("candidates", None)
            _save(path, state)
            if member["status"] == "ready":
                _launch(tool_context, state, member)
                _save(path, state)
        return collect_eos(tool_context, eos_id)
    except _BATCH_ERRORS as exc:
        return {"status": getattr(exc, "status", "error"), "message": str(exc)}


submit_eos_async = _async_tool(submit_eos)
collect_eos_async = _async_tool(collect_eos)
resolve_eos_member_async = _async_tool(resolve_eos_member)
