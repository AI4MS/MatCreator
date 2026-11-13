"""Tracked MatPES batches: each source frame has its own lookup, decision and job."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import re
import sqlite3

from google.adk.tools.tool_context import ToolContext

from ...aidb.exports import export_record
from ...aidb.managed_vasp import _completion_lock, get_managed_vasp_completion
from ...aidb.relaxation import PASSIVE_QUERY_SECONDS, current_message, turn_id
from ...aidb.relaxation_inputs import RelaxationInputs
from ...aidb.service import AIDBError, _save, coverage
from ...aidb.vasp_archive import file_digest
from ...tools.aidb_tools import session_service
from . import remote_job_tools as jobs

logger = logging.getLogger(__name__)
_BATCH_ERRORS = (AIDBError, OSError, ValueError, TypeError, KeyError, sqlite3.Error)


def _workspace(context):
    return Path(context.state["workspace_dir"]).resolve()


def _notification(context):
    message = context.state.get("_vasp_human_message") or current_message(context)
    return message.startswith("REMOTE JOB STATUS UPDATE from the harness.\n")


def _batch_path(context, batch_id):
    if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
        raise ValueError("无效的 MatPES 批次标识")
    owner = json.dumps([jobs._owner_id(context), context.state.get("session_id")])
    return jobs.ADK_DIR / "matpes-batches" / sha256(owner.encode()).hexdigest() / (batch_id + ".json")


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _workspace_child(workspace, path):
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(workspace) or resolved == workspace:
        raise ValueError("MatPES 输入必须在会话工作区内")
    return resolved


def _manifest(context, manifest_path):
    from ase.io import read
    from pymatgen.io.ase import AseAtomsAdaptor

    workspace = _workspace(context)
    manifest = _workspace_child(workspace, workspace / manifest_path)
    raw = manifest.read_bytes()
    data = json.loads(raw)
    if data["schema"] != 1 or not isinstance(data["frames"], list) or not data["frames"]:
        raise ValueError("MatPES 清单没有可识别的源帧")
    source = _workspace_child(workspace, manifest.parent / data["source_path"])
    if file_digest(source) != data["source_sha256"]:
        raise ValueError("MatPES 源文件已改变，请重新准备")
    images = read(source, index=":")
    if file_digest(source) != data["source_sha256"]:
        raise ValueError("MatPES 源文件在读取期间改变，请重新准备")

    members = []
    seen = set()
    for frame in data["frames"]:
        index = frame["frame_index"]
        if type(index) is not int or not 0 <= index < len(images) or index in seen:
            raise ValueError("MatPES 清单源帧索引无效或重复")
        seen.add(index)
        member = {"frame_index": index, "status": frame["status"]}
        members.append(member)
        if frame["status"] in {"skipped", "failed"}:
            member["reason"] = str(frame["reason"])
            continue
        if frame["status"] != "prepared":
            raise ValueError("MatPES 清单含未知准备状态")
        expected_name = f"frame_{index:04d}"
        if frame["input_path"] != expected_name:
            raise ValueError("MatPES 清单与逐帧输入目录不对应")
        folder = manifest.parent / expected_name
        image = images[index]
        if not image.pbc.all():
            member.update(status="failed", reason="MatPES VASP 标注需要三个方向均为周期边界")
            continue
        structure = AseAtomsAdaptor.get_structure(image)
        member.update(input_path=str(folder.relative_to(workspace)), structure=structure.as_dict())
    return (str(manifest.relative_to(workspace)), sha256(raw).hexdigest()), {
        "source_path": str(source.relative_to(workspace)), "source_sha256": data["source_sha256"],
        "members": members,
    }


def _inputs(spec, member):
    import numpy as np
    from pymatgen.core import Structure

    folder = Path(spec["input_root"]) / spec["input_path"]
    if folder.resolve(strict=True) != folder or folder.is_symlink():
        raise ValueError("MatPES 输入目录必须为工作区内的普通目录")
    inputs = RelaxationInputs.from_batch_spec(spec)
    if inputs is None or getattr(inputs, "calculation_type", None) != "matpes":
        raise ValueError("该帧未准备可核实的 MatPES 自洽静态输入")
    original = Structure.from_dict(member["structure"])
    delta = inputs.structure.frac_coords - original.frac_coords if len(inputs.structure) == len(original) else None
    if (delta is None or inputs.structure.species != original.species
            or not np.allclose(inputs.structure.lattice.matrix, original.lattice.matrix, atol=1e-7, rtol=0)
            or not np.allclose(delta - np.rint(delta), 0, atol=1e-7, rtol=0)):
        raise ValueError("POSCAR 与原始帧的晶胞、坐标或原子顺序不一致")
    return inputs


def _candidate(inputs, record):
    try:
        return inputs.matches(record) and not export_record(record, ("extxyz",))[1]
    except (AttributeError, KeyError, IndexError, OverflowError, TypeError, ValueError):
        return False


def _query_binding(context):
    try:
        service = session_service(context, query_timeout=PASSIVE_QUERY_SECONDS)
        return service, service.binding()["binding_id"]
    except (AIDBError, OSError, ValueError) as exc:
        logger.info("VASP member lookup unavailable in session %s: %s", context.state.get("session_id"), exc)
        return None, None


def _lookup_member(context, service, binding_id, queries, inputs, deliverable, **presentation):
    if service is None or not binding_id:
        return None
    try:
        key = (inputs.formula, inputs.calculation_type)
        if key not in queries:
            queries[key] = service.query(
                {"formula": inputs.formula, "calculation_type": inputs.calculation_type}, deduplicate=False,
            )
        result = queries[key]
        service.binding(binding_id)
        if result["binding_id"] != binding_id:
            raise AIDBError("查询与原数据库绑定不同", "stale_binding")
        records = [record for record in result["records"] if deliverable(record)]
        if records:
            reference = service.publish({
                **result, "records": records, "excluded": [], "matched_count": len(records),
                "report": {**result["report"], "match_count": len(records)}, "coverage": coverage(records),
                **presentation,
            })
            reference["user_id"] = jobs._owner_id(context)
            return reference
    except (AIDBError, OSError, KeyError, TypeError, ValueError) as exc:
        logger.info("VASP member lookup unavailable in session %s: %s", service.session_id, exc)
    return None


def _prepare(context, batch):
    service, batch["binding_id"] = _query_binding(context)
    queries = {}
    for member in batch["members"]:
        if member["status"] != "prepared":
            continue
        spec = {**batch["job_spec"], "input_path": member["input_path"],
                "name": f'{batch["job_spec"]["name"]}-frame-{member["frame_index"]}'}
        try:
            inputs = _inputs(spec, member)
        except (OSError, ValueError, TypeError) as exc:
            member.update(status="failed", reason=str(exc))
            continue
        member.update(spec=spec, revision=inputs.revision, status="ready")
        reference = _lookup_member(
            context, service, batch["binding_id"], queries, inputs, lambda record: _candidate(inputs, record),
            operation="matpes_candidates", frame_index=member["frame_index"],
        )
        if reference:
            member.update(status="waiting_choice", candidates=reference)


def _source(batch, member):
    return {key: batch[key] for key in ("batch_id", "source_path", "source_sha256")} | {
        "frame_index": member["frame_index"],
    }


def _launch_member(context, member, binding_id, group_id, origin_node_id, revision_key, *, member_source=None, eos_member=None):
    job = jobs._submit_managed_vasp(context, member["spec"], revision_key, member["revision"],
                                   binding_id, group_id, origin_node_id=origin_node_id,
                                   member=member_source, eos_member=eos_member)
    member["status"] = job["status"] if job.get("job_id") else "failed"
    if job.get("job_id"):
        member["job_id"] = job["job_id"]
        member["external_id"] = job["external_id"]
    if job.get("error") or job.get("message"):
        member["reason"] = job.get("error") or job["message"]
    else:
        member.pop("reason", None)


def _launch(context, batch, member, *, group_id):
    # Store the intent before submitting so a restarted call uses the same identity.
    _launch_member(context, member, batch["binding_id"], group_id, batch["node_id"],
                   f'{batch["batch_id"]}:{member["frame_index"]}', member_source=_source(batch, member))


def _collect_member(context, member, destination, store):
    job_id = member["job_id"]
    status = jobs.get_remote_job_status(job_id, context)
    member["status"] = status["status"]
    if status.get("error") or status.get("message"):
        member["reason"] = status.get("error") or status["message"]
    if status["status"] not in {"succeeded", "collected"}:
        return None, None
    collected = jobs.collect_remote_job_outputs(job_id, destination, context)
    completion = get_managed_vasp_completion(
        user_id=jobs._owner_id(context), session_id=context.state["session_id"], job_id=job_id, store=store,
    )
    if not completion or completion["status"] != "verified":
        member.update(status="archive_failed", archive_status=(completion or {}).get("status"),
                      reason=(completion or {}).get("reason") or collected.get("message")
                      or "未能核实并归档 VASP 结果")
    else:
        member.update(status="archived", archive_status="verified", record_id=completion["record_id"],
                      source_id=completion["verified_source"]["source_id"])
        member.pop("reason", None)
    return completion, collected.get("aidb_result")


def _receipt(batch):
    members = [{key: row[key] for key in (
        "frame_index", "status", "job_id", "external_id", "reason", "candidates",
        "record_id", "archive_status", "source_id", "output_index",
    ) if key in row} for row in batch["members"]]
    statuses = {row["status"] for row in members}
    if statuses & {"ready", "submitting", "created", "queued", "running", "succeeded", "collecting"}:
        status = "running"
    elif "waiting_choice" in statuses:
        status = "awaiting_frame_choices"
    else:
        status = "complete"
    return {"status": status, "batch_id": batch["batch_id"], "group_id": batch["group_id"], "members": members}


def submit_matpes_batch(
    tool_context: ToolContext,
    *,
    manifest_path: str,
    name: str,
    image: str,
    command: str,
    project_id: int | None = None,
    machine_type: str | None = None,
    sku_id: int | None = None,
    max_run_time: str = "24h",
    max_wait_time: str = "30m",
) -> dict:
    """Run authorized MatPES labeling from prepare_matpes.py --batch's manifest.

    State the job count, machine and estimated core-hours and follow the existing
    submission authorization first. Submit the batch once. Each unmatched source
    frame is submitted automatically and exact matches wait for a user decision
    while other jobs proceed. Report the batch_id and affected frame indices.
    Use collect_matpes_batch with that batch_id to resume or collect. Use
    resolve_matpes_frame only for an explicit frame decision on a later user turn.
    """
    try:
        if _notification(tool_context):
            raise ValueError("作业通知只能回收现有 MatPES 批次")
        workspace = _workspace(tool_context)
        session_id = tool_context.state.get("session_id")
        project_id = project_id or os.environ.get("BOHRIUM_PROJECT_ID")
        if not (session_id and project_id and name and image and command) or bool(machine_type) == (sku_id is not None):
            raise ValueError("MatPES 作业需要会话、项目、名称、镜像、命令及一种机型标识")
        manifest_identity, batch = _manifest(tool_context, manifest_path)
        spec = {
            "project_id": project_id, "name": name, "machine_type": machine_type, "sku_id": sku_id,
            "image": image, "command": command, "input_root": str(workspace), "out_files": None,
            "max_run_time": max_run_time, "max_wait_time": max_wait_time, "vasp_type": "matpes",
        }
        identity = [tool_context.state.get("_vasp_graph_id"), jobs._node_id(tool_context), manifest_identity, spec]
        batch_id = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        path = _batch_path(tool_context, batch_id)
        with _completion_lock(path):
            if path.is_file():
                batch = _load(path)
            else:
                batch.update(batch_id=batch_id, job_spec=spec, created_turn=turn_id(tool_context),
                             node_id=jobs._node_id(tool_context), group_id=None)
                _prepare(tool_context, batch)
                ready = [member for member in batch["members"] if member["status"] == "ready"]
                if len(ready) > 1:
                    group = jobs.create_remote_job_group(
                        tool_context, name=f"matpes:{batch_id}", expected_jobs=len(ready),
                    )
                    if group["status"] != "ready":
                        return group
                    batch["group_id"] = group["group_id"]
                _save(path, batch)
            for member in batch["members"]:
                if member["status"] == "ready":
                    group_id = None if member.get("recalculate") else batch["group_id"]
                    _launch(tool_context, batch, member, group_id=group_id)
                    _save(path, batch)
            return _receipt(batch)
    except _BATCH_ERRORS as exc:
        return {"status": "error", "message": str(exc)}


def resolve_matpes_frame(
    tool_context: ToolContext,
    batch_id: str,
    frame_index: int,
    action: str,
    record_id: str | None = None,
) -> dict:
    """Resolve one MatPES candidate after an explicit later user instruction.

    action is select, recalculate, or cancel. For select, use a record_id from
    that frame's candidates when there are multiple records. For recalculate,
    follow the original job submission authorization. Read-only details and
    downloads remain available on each candidate's existing result card.
    """
    try:
        path = _batch_path(tool_context, batch_id)
        with _completion_lock(path):
            batch = _load(path)
            member = next((row for row in batch["members"] if row["frame_index"] == frame_index), None)
            if not member or not (member["status"] == "waiting_choice" or (
                member["status"] == "ready" and member.get("recalculate") and action == "recalculate"
            )):
                raise ValueError("该帧没有待处理的 MatPES 候选")
            decision_turn = turn_id(tool_context)
            message = tool_context.state.get("_vasp_human_message") or current_message(tool_context)
            if (not message or _notification(tool_context)
                    or not decision_turn or decision_turn == batch["created_turn"]):
                raise ValueError("需要用户在后续对话中明确决定该帧")
            if action == "cancel":
                member.update(status="cancelled", reason="用户取消该帧")
            elif action in {"select", "recalculate"}:
                source = _workspace_child(_workspace(tool_context), _workspace(tool_context) / batch["source_path"])
                inputs = _inputs(member["spec"], member)
                if inputs.revision != member["revision"] or file_digest(source) != batch["source_sha256"]:
                    raise AIDBError("该帧输入已改变，请重新准备 MatPES 批次", "stale_input")
                if action == "select":
                    service = session_service(tool_context)
                    binding_id = batch["binding_id"]
                    service.binding(binding_id)
                    allowed = [row["record_id"] for row in member["candidates"]["records"]]
                    record_id = record_id or (allowed[0] if len(allowed) == 1 else None)
                    if record_id not in allowed:
                        raise ValueError("请明确选用该帧的哪一条候选记录")
                    record = service.detail(record_id, binding_id=binding_id)["record"]
                    if not _candidate(inputs, record):
                        raise AIDBError("该记录已无法核实为可交付的完全匹配", "not_ready")
                    member.update(status="reused", record_id=record_id, source_id=record["source_id"])
                else:
                    # This decision is a new job, independent of the already active group.
                    member.update(status="ready", recalculate=True)
                    _save(path, batch)
                    _launch(tool_context, batch, member, group_id=None)
            else:
                raise ValueError("MatPES 帧操作应为选用、重算或取消")
            member.pop("candidates", None)
            _save(path, batch)
        return collect_matpes_batch(tool_context, batch_id)
    except _BATCH_ERRORS as exc:
        return {"status": getattr(exc, "status", "error"), "message": str(exc)}


def _dataset(context, batch):
    from io import StringIO
    from ase.io import read, write

    workspace = _workspace(context)
    configured = context.state.get("output_dir")
    output = Path(configured).resolve() if configured else workspace
    if not output.is_relative_to(workspace):
        raise ValueError("MatPES 汇总输出目录必须位于工作区内")
    output = output / f'matpes-{batch["batch_id"]}'
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink() or not output.resolve().is_relative_to(workspace):
        raise ValueError("MatPES 汇总输出目录不能为符号链接")

    snapshot = _receipt(batch)
    atoms_list = []
    service = None
    for member in snapshot["members"]:
        if member["status"] not in {"archived", "reused"}:
            continue
        try:
            if service is None:
                service = session_service(context)
            record = service.detail(member["record_id"], binding_id=batch["binding_id"])["record"]
            if record["source_id"] != member["source_id"]:
                raise ValueError("读回记录的来源已改变")
            files, issues = export_record(record, ("extxyz",))
            if issues:
                raise ValueError(issues["extxyz"])
            atoms = read(StringIO(files["extxyz"]["content"]), format="extxyz")
            atoms.info.update(labeling_source_path=batch["source_path"], labeling_source_sha256=batch["source_sha256"],
                              labeling_source_frame_index=member["frame_index"])
            member["output_index"] = len(atoms_list)
            atoms_list.append(atoms)
        except _BATCH_ERRORS as exc:
            member.update(status="unavailable", reason=str(exc))

    # Files are replaced only once fully written. Return the current snapshot's
    # downloads and never advertise an older EXTXYZ after a failed readback.
    manifest = output / "members.json"
    dataset = output / "training.extxyz"
    artifacts = []
    if atoms_list:
        from tempfile import NamedTemporaryFile

        pending = None
        try:
            with NamedTemporaryFile(mode="w", dir=output, encoding="utf-8", delete=False) as stream:
                pending = Path(stream.name)
                write(stream, atoms_list, format="extxyz")
            pending.replace(dataset)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
        artifacts.append(str(dataset))
    elif dataset.exists():
        dataset.unlink()
    _save(manifest, {**snapshot, "source_path": batch["source_path"], "source_sha256": batch["source_sha256"]})
    return {**snapshot, "artifact_paths": [str(manifest), *artifacts]}


def collect_matpes_batch(tool_context: ToolContext, batch_id: str) -> dict:
    """Reattach to an existing MatPES batch and collect succeeded members.

    Use the returned batch ID across turns and status wakeups. This reads the
    original jobs, uses standard managed VASP collection, and delivers a frame
    manifest and EXTXYZ with verified available labels in source order. Report
    missing, pending, or failed members explicitly. Use the member's result
    card for its full detail and individual export.
    """
    try:
        path = _batch_path(tool_context, batch_id)
        with _completion_lock(path):
            batch = _load(path)
            store = jobs._service().store
            owner_id = jobs._owner_id(tool_context)
            session_id = tool_context.state["session_id"]
            tracked = {
                member["frame_index"]: job for job in store.list_jobs(owner_id=owner_id, session_id=session_id)
                if (member := job["specification"].get("managed_vasp", {}).get("matpes_frame"))
                and member["batch_id"] == batch_id
            }
            completions = []
            for member in batch["members"]:
                if not member.get("job_id") and member["frame_index"] in tracked:
                    recovered = tracked[member["frame_index"]]
                    member.update(job_id=recovered["job_id"], external_id=recovered["external_id"])
                if not member.get("job_id"):
                    if member["status"] == "ready":
                        member["reason"] = "上次授权的提交未完成，请在对话中继续原批次"
                    continue
                index = member["frame_index"]
                destination = f"matpes-{batch_id}/frame-{index}-output"
                try:
                    _, reference = _collect_member(tool_context, member, destination, store)
                    if reference:
                        completions.append(reference)
                except _BATCH_ERRORS as exc:
                    member.update(status="unavailable", reason=str(exc))
            _save(path, batch)
            return {**_dataset(tool_context, batch), "aidb_completions": completions}
    except _BATCH_ERRORS as exc:
        return {"status": getattr(exc, "status", "error"), "message": str(exc)}


def _async_tool(tool):
    from functools import wraps

    @wraps(tool)
    async def invoke(*args, **kwargs):
        return await asyncio.to_thread(tool, *args, **kwargs)

    return invoke


submit_matpes_batch_async = _async_tool(submit_matpes_batch)
resolve_matpes_frame_async = _async_tool(resolve_matpes_frame)
collect_matpes_batch_async = _async_tool(collect_matpes_batch)
