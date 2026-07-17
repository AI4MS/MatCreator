"""Tracked E2B sandbox tools available to isolated step executors."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ...control_plane.remote_job_service import E2BConnectionConfig, RemoteJobService
from ...control_plane.remote_jobs import RemoteJobStore
from ...workspace import ADK_DIR
from .recovery import record_remote_job_reference


def _service() -> RemoteJobService:
    return RemoteJobService(RemoteJobStore(ADK_DIR / "remote-jobs.db"))


def _owner_id(tool_context: ToolContext) -> str:
    invocation = getattr(tool_context, "_invocation_context", None)
    return str(getattr(invocation, "user_id", "") or tool_context.state.get("user_id") or "default")


def _node_id(tool_context: ToolContext) -> str:
    graph_node = str(tool_context.state.get("_graph_exec_node_id") or "step")
    return graph_node.rsplit("__node_", 1)[-1]


def _connection() -> E2BConnectionConfig:
    return E2BConnectionConfig(
        api_key=os.environ.get("E2B_API_KEY", ""),
        api_url=os.environ.get("E2B_API_URL", ""),
        project_id=os.environ.get("BOHRIUM_PROJECT_ID", ""),
        template="",
    )


def submit_e2b_sandbox(
    tool_context: ToolContext,
    *,
    timeout: int = 7200,
    template: str = None,
    lifecycle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or reuse a tracked E2B sandbox for the current execution step.

    The configured E2B API key, endpoint, and project ID are used server-side.
    Never use shell commands or include credentials in tool inputs. A repeated
    call for the same step and template returns the existing sandbox record.
    """
    session_id = str(tool_context.state.get("session_id") or "")
    if not session_id:
        return {"status": "error", "message": "No session_id is available for E2B submission."}
    node_id = _node_id(tool_context)
    connection = _connection()
    if not template:
        return {
            "status": "error",
            "message": "An explicit E2B sandbox template is required. Use 'lbg sdbx template ls -q' to list available templates.",
        }
    connection = E2BConnectionConfig(
        api_key=connection.api_key,
        api_url=connection.api_url,
        project_id=connection.project_id,
        template=template,
    )
    identity = f"{session_id}:{node_id}:{connection.template}"
    idempotency_key = f"e2b:{hashlib.sha256(identity.encode()).hexdigest()}"
    try:
        job = _service().submit_e2b(
            owner_id=_owner_id(tool_context),
            session_id=session_id,
            node_id=node_id,
            step_number=tool_context.state.get("step_number"),
            idempotency_key=idempotency_key,
            connection=connection,
            timeout=timeout,
            lifecycle=lifecycle or {"on_timeout": "pause", "auto_resume": True},
        )
    except Exception as exc:
        return {"status": "error", "message": f"E2B submission failed: {exc}"}
    record_remote_job_reference(
        session_id=session_id,
        node_id=node_id,
        job_id=job["job_id"],
        provider="e2b",
        external_id=job["external_id"],
    )
    return {
        "status": job["status"],
        "job_id": job["job_id"],
        "sandbox_id": job["external_id"],
        "message": "Tracked E2B sandbox is ready. Use its job_id for status or controls.",
    }


def get_e2b_job_status(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Read one tracked E2B job owned by the current session."""
    service = _service()
    job = service.store.get_job(job_id)
    if job is None or job["owner_id"] != _owner_id(tool_context) or job["session_id"] != tool_context.state.get("session_id"):
        return {"status": "error", "message": "E2B job was not found in this session."}
    result = {key: job[key] for key in ("job_id", "status", "external_id", "snapshot", "error", "updated_at")}
    controls = [
        event["payload"]
        for event in service.store.list_events(job_id)
        if event["event_type"] == "user_control"
    ]
    if controls:
        result["user_control"] = controls[-1]
    return result


def pause_e2b_sandbox(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Pause a tracked E2B sandbox belonging to the current session."""
    job = get_e2b_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        paused = _service().pause_e2b(job_id)
    except Exception as exc:
        return {"status": "error", "message": f"E2B pause failed: {exc}"}
    return {"job_id": paused["job_id"], "status": paused["status"], "sandbox_id": paused["external_id"]}


def terminate_e2b_sandbox(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Terminate a tracked E2B sandbox belonging to the current session."""
    job = get_e2b_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        terminated = _service().terminate_e2b(job_id)
    except Exception as exc:
        return {"status": "error", "message": f"E2B termination failed: {exc}"}
    return {"job_id": terminated["job_id"], "status": terminated["status"], "sandbox_id": terminated["external_id"]}


def run_e2b_command(
    job_id: str,
    command: str,
    tool_context: ToolContext,
    user: str = "root",
) -> dict[str, Any]:
    """Run one command inside a tracked E2B sandbox in the current session.

    Do not put credentials in ``command``. Command text and output are returned
    to the current step but are not persisted in the durable job snapshot.
    """
    job = get_e2b_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        return _service().run_e2b_command(job_id, command, user=user)
    except Exception as exc:
        current = get_e2b_job_status(job_id, tool_context)
        result = {"status": "error", "message": f"E2B command failed: {exc}"}
        if current.get("user_control"):
            result["user_control"] = current["user_control"]
        return result


def upload_e2b_input(
    job_id: str,
    source_path: str,
    destination_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Upload a workspace input file into a tracked E2B sandbox.

    ``source_path`` must resolve inside the current workspace. Use an absolute
    sandbox path for ``destination_path`` such as ``/home/user/input.in``.
    """
    job = get_e2b_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    workspace = Path(str(tool_context.state.get("workspace_dir") or "")).resolve()
    source = Path(source_path).expanduser()
    source = source.resolve() if source.is_absolute() else (workspace / source).resolve()
    if not workspace or not source.is_relative_to(workspace):
        return {"status": "error", "message": "E2B upload source must be inside the current workspace."}
    try:
        return _service().upload_e2b_file(job_id, source, destination_path)
    except Exception as exc:
        return {"status": "error", "message": f"E2B upload failed: {exc}"}