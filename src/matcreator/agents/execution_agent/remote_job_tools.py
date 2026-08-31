"""Remote-job tools available to isolated step executors.

Submission is provider-specific — a bohr sandbox needs a template while a
batch job needs a machine type and image, so there is one submit tool per
provider (``submit_bohr_sandbox``, ``submit_bohr_job``). Every operation
after submission dispatches on the ``job_id`` alone and works the same for
any provider, so adding a new provider plugin never requires a new
post-submission tool here.

``submit_e2b_sandbox`` is retained below for existing/in-flight e2b jobs and
its unit tests, but is no longer registered on the step executor — new
submissions go through ``submit_bohr_sandbox``/``submit_bohr_job`` instead.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ...control_plane.providers.e2b import E2BConnectionConfig
from ...control_plane.remote_job_service import RemoteJobService
from ...control_plane.remote_jobs import TERMINAL_REMOTE_JOB_STATUSES, RemoteJobStore
from ...control_plane.remote_task_contract import (
    RemoteTaskContractError,
    build_remote_task_envelope,
    make_public_input_record,
    validate_task_run_id,
    validate_workload_profile,
)
from ...workspace import ADK_DIR
from .recovery import record_remote_job_reference

# Every terminal status except "collected" (the successful end of a batch
# job) means the submission is not usable and must not be reported as ready.
_FAILED_SUBMISSION_STATUSES = TERMINAL_REMOTE_JOB_STATUSES - {"collected"}

_PRESENTATION_VERSION = "mc.remote-task-presentation.v1"
_TASK_PRESENTATION_PROFILES: dict[str, dict[str, Any]] = {
    "md": {
        "task_type": "Molecular dynamics",
        "phase_plan": (
            ("prepare", "Prepare MD inputs"),
            ("submit", "Submit Sandbox"),
            ("queue", "Stage runtime"),
            ("execute", "Simulate"),
            ("collect", "Collect outputs"),
            ("validate", "Validate trajectory"),
        ),
    },
    "vasp": {
        "task_type": "VASP",
        "phase_plan": (
            ("prepare", "Prepare VASP inputs"),
            ("submit", "Submit Sandbox"),
            ("queue", "Stage runtime"),
            ("execute", "Solve"),
            ("collect", "Collect outputs"),
            ("validate", "Verify calculation"),
        ),
    },
    "training": {
        "task_type": "Model training",
        "phase_plan": (
            ("prepare", "Prepare training inputs"),
            ("submit", "Submit Sandbox"),
            ("queue", "Stage runtime"),
            ("execute", "Train"),
            ("collect", "Collect checkpoints"),
            ("validate", "Evaluate model"),
        ),
    },
    "generic": {
        "task_type": "Generic remote task",
        "phase_plan": (
            ("prepare", "Prepare inputs"),
            ("submit", "Submit Sandbox"),
            ("queue", "Stage runtime"),
            ("execute", "Execute"),
            ("collect", "Collect outputs"),
            ("validate", "Verify task"),
        ),
    },
}


def _service() -> RemoteJobService:
    return RemoteJobService(RemoteJobStore(ADK_DIR / "remote-jobs.db"))


def _owner_id(tool_context: ToolContext) -> str:
    invocation = getattr(tool_context, "_invocation_context", None)
    return str(getattr(invocation, "user_id", "") or tool_context.state.get("user_id") or "default")


def _node_id(tool_context: ToolContext) -> str:
    graph_node = str(tool_context.state.get("_graph_exec_node_id") or "step")
    return graph_node.rsplit("__node_", 1)[-1]


def _idempotency_key(session_id: str, node_id: str, discriminator: str) -> str:
    identity = f"{session_id}:{node_id}:{discriminator}"
    return f"remote-job:{hashlib.sha256(identity.encode()).hexdigest()}"


def _bohr_provider_session_id(
    tool_context: ToolContext,
    *,
    template: str,
    stable_name: str,
) -> str:
    """Derive one scoped, opaque Bohrium association ID.

    Public ``stable_name`` values are unique only inside the MC task
    contract.  Hash the owning MC scope before using Bohrium's global-ish
    session metadata so two users or sessions may safely choose the same
    friendly task name without colliding or exposing those scope values.
    """

    identity = json.dumps(
        [
            _owner_id(tool_context),
            str(tool_context.state.get("session_id") or ""),
            _node_id(tool_context),
            str(template),
            str(stable_name),
            1,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "mc-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _default_task_run_id(session_id: str, node_id: str) -> str:
    """Return a deterministic public identity without rejecting legacy node IDs."""
    try:
        return validate_task_run_id(node_id)
    except RemoteTaskContractError:
        digest = hashlib.sha256(f"{session_id}:{node_id}".encode()).hexdigest()[:20]
        return f"task-{digest}"


def _task_submission_metadata(
    tool_context: ToolContext,
    *,
    stable_name: str | None,
    workload_kind: str | None,
) -> dict[str, Any]:
    """Build provider-neutral, browser-safe task metadata for a Sandbox."""
    session_id = str(tool_context.state.get("session_id") or "")
    node_id = _node_id(tool_context)
    task_run_id = (
        validate_task_run_id(stable_name)
        if stable_name is not None
        else _default_task_run_id(session_id, node_id)
    )
    profile = validate_workload_profile(
        "generic" if workload_kind is None else workload_kind
    )
    envelope = build_remote_task_envelope(
        task_run_id=task_run_id,
        attempt=1,
        workload_profile=profile,
        inputs=(),
    )
    profile_definition = _TASK_PRESENTATION_PROFILES[profile]
    phase_plan = [
        {
            "key": key,
            "label": label,
            "group": (
                "preparation"
                if key in {"prepare", "submit", "queue"}
                else "execution"
                if key == "execute"
                else "completion"
            ),
        }
        for key, label in profile_definition["phase_plan"]
    ]
    presentation = {
        "version": _PRESENTATION_VERSION,
        "workload_kind": profile,
        "task_type": profile_definition["task_type"],
        "current_phase": "prepare",
        "phase_plan": phase_plan,
    }
    return {
        "stable_name": task_run_id,
        "workload_kind": profile,
        "task_type": profile_definition["task_type"],
        "presentation": presentation,
        "task_contract": envelope,
    }


def _submit(
    tool_context: ToolContext,
    *,
    provider: str,
    spec: dict[str, Any],
    discriminator: str,
    persisted_specification: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shared submission plumbing used by every provider-specific submit tool."""
    session_id = str(tool_context.state.get("session_id") or "")
    if not session_id:
        return {"status": "error", "message": "No session_id is available for remote-job submission."}
    node_id = _node_id(tool_context)
    idempotency_key = _idempotency_key(session_id, node_id, discriminator)
    try:
        job = _service().submit_job(
            owner_id=_owner_id(tool_context),
            session_id=session_id,
            provider=provider,
            node_id=node_id,
            step_number=tool_context.state.get("step_number"),
            idempotency_key=idempotency_key,
            spec=spec,
            persisted_specification=persisted_specification,
        )
    except Exception as exc:
        return {"status": "error", "message": f"{provider} submission failed: {exc}"}
    record_remote_job_reference(
        session_id=session_id,
        node_id=node_id,
        job_id=job["job_id"],
        provider=provider,
        external_id=job["external_id"],
    )
    return {
        "status": job["status"],
        "job_id": job["job_id"],
        "external_id": job["external_id"],
        "error": job.get("error"),
    }


def _submission_response(result: dict[str, Any], *, id_field: str, success_message: str) -> dict[str, Any]:
    """Convert a ``_submit`` result into the tool response, never claiming a

    failed/cancelled/terminated/lost job is ready. The durable record's
    ``error`` is surfaced so the caller sees the actual cause.
    """
    if result.get("status") == "error":
        return result
    response = {
        "status": result["status"],
        "job_id": result["job_id"],
        id_field: result["external_id"],
    }
    if result["status"] in _FAILED_SUBMISSION_STATUSES:
        cause = result.get("error") or f"the tracked job is in terminal status '{result['status']}'"
        response["message"] = f"Remote job submission is not usable: {cause}"
        return response
    response["message"] = success_message
    return response


def _connection() -> E2BConnectionConfig:
    # Bohrium E2B endpoint uses bare hex keys; disable SDK format validation
    os.environ.setdefault("E2B_VALIDATE_API_KEY", "false")
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
    lifecycle: dict[str, Any] | str | None = None,
    stable_name: str | None = None,
    workload_kind: str | None = "generic",
) -> dict[str, Any]:
    """Create or reuse a tracked E2B sandbox for the current execution step.

    The configured E2B API key, endpoint, and project ID are used server-side.
    Never use shell commands or include credentials in tool inputs. A repeated
    call for the same step and template returns the existing sandbox record.
    ``stable_name`` may name an independently tracked logical task, while
    ``workload_kind`` selects ``md``, ``vasp``, ``training``, or ``generic``
    presentation phases without changing the provider lifecycle.
    """
    session_id = str(tool_context.state.get("session_id") or "")
    if not session_id:
        return {"status": "error", "message": "No session_id is available for E2B submission."}
    if not template:
        return {
            "status": "error",
            "message": "An explicit E2B sandbox template is required. Use 'lbg sdbx template ls -q' to list available templates.",
        }
    if isinstance(lifecycle, str):
        try:
            lifecycle = json.loads(lifecycle)
        except json.JSONDecodeError:
            pass  # still a str; rejected below
    if lifecycle is not None and not isinstance(lifecycle, dict):
        return {
            "status": "error",
            "message": "lifecycle must be a JSON object such as {\"on_timeout\": \"pause\", \"auto_resume\": true}.",
        }
    try:
        task_metadata = _task_submission_metadata(
            tool_context,
            stable_name=stable_name,
            workload_kind=workload_kind,
        )
    except RemoteTaskContractError as exc:
        return {"status": "error", "message": f"Invalid remote task contract: {exc}"}
    connection = _connection()
    missing_config = [
        name
        for name, value in (
            ("E2B_API_KEY", connection.api_key),
            ("E2B_API_URL", connection.api_url),
            ("BOHRIUM_PROJECT_ID", connection.project_id),
        )
        if not value
    ]
    if missing_config:
        return {
            "status": "error",
            "message": (
                f"E2B is not configured on the server: {', '.join(missing_config)} unset. "
                "If only the `bohr` CLI is available, use submit_bohr_sandbox instead."
            ),
        }
    connection = E2BConnectionConfig(
        api_key=connection.api_key,
        api_url=connection.api_url,
        project_id=connection.project_id,
        template=template,
    )
    spec = connection.to_spec_dict(timeout=timeout, lifecycle=lifecycle or {"on_timeout": "pause", "auto_resume": True})
    persisted_specification = {
        key: value for key, value in spec.items() if key != "api_key"
    }
    persisted_specification.update(task_metadata)
    result = _submit(
        tool_context,
        provider="e2b",
        spec=spec,
        discriminator=(
            template
            if stable_name is None
            else f"{template}:{task_metadata['stable_name']}"
        ),
        persisted_specification=persisted_specification,
    )
    return _submission_response(
        result,
        id_field="sandbox_id",
        success_message="Tracked E2B sandbox is ready. Use its job_id for status or controls.",
    )


def submit_bohr_sandbox(
    tool_context: ToolContext,
    *,
    project_id: int = None,
    template: str = None,
    timeout: int = None,
    image: str = None,
    gpu: str = None,
    never_timeout: bool = False,
    env: dict[str, str] | None = None,
    stable_name: str | None = None,
    workload_kind: str | None = "generic",
) -> dict[str, Any]:
    """Create or reuse a tracked Bohrium CLI sandbox (`bohr sandbox`) for the current step.

    Both this and `submit_e2b_sandbox` reach the same Bohrium sandbox
    platform; use this one when only the `bohr` CLI (not the E2B SDK/API key)
    is available in the current environment. An explicit ``template`` is
    required (e.g. ``doc-compiler``). ``gpu`` selects a GPU shortcut template
    (``4090``/``5090``/``l20``). Falls back to the `BOHRIUM_PROJECT_ID`
    environment variable if ``project_id`` is omitted. ``stable_name`` and
    ``workload_kind`` add public task identity/presentation metadata.  When a
    ``stable_name`` is supplied, an opaque ID derived from the owning MC
    scope is also sent to Bohrium so a disconnected submission can be found
    without cross-user name collisions.  Environment values are never
    persisted, and the provider association ID is not exposed by the Web API.
    """
    resolved_project_id = project_id or os.environ.get("BOHRIUM_PROJECT_ID", "")
    if not resolved_project_id:
        return {"status": "error", "message": "An explicit project_id is required for a bohr sandbox."}
    if not template:
        return {
            "status": "error",
            "message": (
                "An explicit sandbox template is required (e.g. 'doc-compiler'). "
                "Use 'bohr sandbox template list' to see available templates."
            ),
        }
    try:
        task_metadata = _task_submission_metadata(
            tool_context,
            stable_name=stable_name,
            workload_kind=workload_kind,
        )
    except RemoteTaskContractError as exc:
        return {"status": "error", "message": f"Invalid remote task contract: {exc}"}
    spec = {
        "project_id": resolved_project_id,
        "template": template,
        "timeout": timeout,
        "image": image,
        "gpu": gpu,
        "never_timeout": never_timeout,
        "env": env or {},
    }
    provider_session_id = None
    if stable_name is not None:
        provider_session_id = _bohr_provider_session_id(
            tool_context,
            template=template,
            stable_name=task_metadata["stable_name"],
        )
        spec["session_id"] = provider_session_id
    persisted_specification = {
        key: value
        for key, value in spec.items()
        if key not in {"env", "session_id"}
    }
    persisted_specification["env_variable_count"] = len(env or {})
    if provider_session_id is not None:
        persisted_specification["provider_session_id"] = provider_session_id
    persisted_specification.update(task_metadata)
    result = _submit(
        tool_context,
        provider="bohr_sandbox",
        spec=spec,
        discriminator=(
            template
            if stable_name is None
            else f"{template}:{task_metadata['stable_name']}"
        ),
        persisted_specification=persisted_specification,
    )
    return _submission_response(
        result,
        id_field="sandbox_id",
        success_message="Tracked bohr sandbox is ready. Use its job_id for status or controls.",
    )


def submit_bohr_job(
    tool_context: ToolContext,
    *,
    project_id: int = None,
    job_name: str = None,
    machine_type: str = None,
    image_address: str = None,
    command: str = None,
    input_directory: str | None = None,
    result_path: str | None = None,
    max_run_time: int | None = None,
) -> dict[str, Any]:
    """Submit a batch/HPC-style Bohrium job (`bohr job submit`) for the current step.

    This is a fire-and-forget batch submission, not an interactive sandbox:
    inputs are staged once via ``input_directory`` and there is no
    `run_remote_job_command` for this provider — the whole computation must
    be expressed in ``command``. Poll `get_remote_job_status` until it
    reports ``succeeded``, then call `collect_remote_job_outputs`.
    """
    resolved_project_id = project_id or os.environ.get("BOHRIUM_PROJECT_ID", "")
    missing = [
        name
        for name, value in (
            ("project_id", resolved_project_id),
            ("job_name", job_name),
            ("machine_type", machine_type),
            ("image_address", image_address),
            ("command", command),
        )
        if not value
    ]
    if missing:
        return {
            "status": "error",
            "message": f"Missing required field(s) for bohr job submission: {', '.join(missing)}",
        }
    spec = {
        "project_id": resolved_project_id,
        "job_name": job_name,
        "machine_type": machine_type,
        "image_address": image_address,
        "command": command,
        "input_directory": input_directory,
        "result_path": result_path,
        "max_run_time": max_run_time,
    }
    result = _submit(
        tool_context,
        provider="bohr_job",
        spec=spec,
        discriminator=f"{job_name}:{machine_type}:{image_address}",
    )
    return _submission_response(
        result,
        id_field="bohr_job_id",
        success_message=(
            "Tracked bohr batch job is submitted. Poll get_remote_job_status until it "
            "reports succeeded, then call collect_remote_job_outputs."
        ),
    )


def get_remote_job_status(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Read one tracked remote job (any provider) owned by the current session."""
    service = _service()
    job = service.store.get_job(job_id)
    if (
        job is None
        or job["owner_id"] != _owner_id(tool_context)
        or job["session_id"] != tool_context.state.get("session_id")
    ):
        return {"status": "error", "message": "Remote job was not found in this session."}
    result = {
        key: job[key] for key in ("job_id", "provider", "status", "external_id", "snapshot", "error", "updated_at")
    }
    controls = [
        event["payload"] for event in service.store.list_events(job_id) if event["event_type"] == "user_control"
    ]
    if controls:
        result["user_control"] = controls[-1]
    return result


def pause_remote_job(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Pause a tracked remote job belonging to the current session.

    Returns an error if the job's provider does not support pausing (e.g. a
    batch job); terminate it instead if it must stop.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        paused = _service().pause_job(job_id)
    except Exception as exc:
        return {"status": "error", "message": f"Pause failed: {exc}"}
    return {"job_id": paused["job_id"], "status": paused["status"], "external_id": paused["external_id"]}


def terminate_remote_job(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Terminate a tracked remote job belonging to the current session."""
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        terminated = _service().terminate_job(job_id)
    except Exception as exc:
        return {"status": "error", "message": f"Termination failed: {exc}"}
    return {"job_id": terminated["job_id"], "status": terminated["status"], "external_id": terminated["external_id"]}


def run_remote_job_command(
    job_id: str,
    command: str,
    tool_context: ToolContext,
    user: str = "root",
) -> dict[str, Any]:
    """Run one short command inside a tracked interactive remote job (e.g. a sandbox).

    This BLOCKS until the command finishes, with no timeout of its own. Only
    use it for commands expected to finish in well under a minute (checking a
    file, `mkdir`, `grep`, listing a directory, ...). For anything that might
    run longer — a training run, a `vasp_std`/`mpirun` invocation, any real
    computation — use `start_remote_job_command` + `poll_remote_job_command`
    instead: those never block longer than one quick status check and the
    command survives this process restarting or losing connection, unlike a
    long blocking call here which has no way to recover if interrupted.

    Do not put credentials in ``command``. Command text and output are
    returned to the current step but are not persisted in the durable job
    snapshot. Not every provider supports this — a batch job (e.g.
    `bohr_job`) returns an error explaining that its whole command must run
    at submission time instead.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        return _service().run_job_command(job_id, command, user=user)
    except Exception as exc:
        current = get_remote_job_status(job_id, tool_context)
        result = {"status": "error", "message": f"Remote command failed: {exc}"}
        if current.get("user_control"):
            result["user_control"] = current["user_control"]
        return result


def start_remote_job_command(
    job_id: str,
    command: str,
    tool_context: ToolContext,
    user: str = "root",
) -> dict[str, Any]:
    """Launch a long-running command inside a tracked interactive remote job WITHOUT blocking.

    Use this instead of `run_remote_job_command` for any real computation
    (training, `vasp_std`/`mpirun`, anything that might take more than a
    minute). Returns almost immediately once the command is launched in the
    background; call `poll_remote_job_command` with the same `job_id`
    afterward — repeatedly, across as many separate tool calls or even
    separate step-executor attempts as needed — to check whether it has
    finished. The command's progress is tracked durably on the job itself, so
    re-attaching to this `job_id` after a step timeout, a crash, or a lost
    connection always finds the same in-flight command rather than losing
    track of it or risking a duplicate run.

    There is at most one in-flight background command per job; starting a
    new one before polling the previous one to completion overwrites the
    previous command's tracked handle.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        return _service().start_job_command(job_id, command, user=user)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to start remote command: {exc}"}


def poll_remote_job_command(job_id: str, tool_context: ToolContext) -> dict[str, Any]:
    """Check on the job's most recently started background command.

    Returns `{"running": true, ...}` if it is still executing — call this
    again later (e.g. after doing other work, or in a fresh step-executor
    attempt after re-attaching via `get_remote_job_status`) rather than
    waiting in a tight loop. Once finished, returns `{"running": false,
    "exit_code": ..., "output_tail": ...}`; `output_tail` is only the last
    portion of combined stdout/stderr — for the full output of a long run,
    use `download_remote_job_output` on the returned `log_path`.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    try:
        return _service().poll_job_command(job_id)
    except Exception as exc:
        return {"status": "error", "message": f"Failed to poll remote command: {exc}"}


def publish_remote_job_progress(
    job_id: str,
    kind: str,
    current: int,
    total: int,
    unit: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Publish a real execution metric for one owned interactive remote job.

    Use only when the workload exposes an explicit numerator and denominator,
    such as MD step / target step or training epoch / target epoch. This does
    not infer progress from provider liveness and is rejected for batch jobs.
    """
    owned = get_remote_job_status(job_id, tool_context)
    if owned.get("status") == "error":
        return owned
    try:
        updated = _service().publish_job_progress(
            job_id,
            kind=kind,
            current=current,
            total=total,
            unit=unit,
        )
    except Exception as exc:
        return {"status": "error", "message": f"Progress update failed: {exc}"}
    progress = (updated.get("snapshot") or {}).get("progress") or {}
    return {
        "job_id": job_id,
        "status": updated["status"],
        "current_phase": "execution",
        "progress": progress,
    }


def _resolve_workspace_child(
    tool_context: ToolContext,
    user_path: str,
) -> tuple[Path | None, str | None]:
    """Resolve ``user_path`` against the current workspace, confining it.

    Returns ``(resolved_path, None)`` on success or ``(None, message)`` if the
    workspace is unavailable or the path escapes it. Shared by upload (source)
    and download (destination) so confinement logic cannot drift between them.
    """
    workspace_dir = tool_context.state.get("workspace_dir")
    if not workspace_dir:
        return None, "No workspace_dir is available for the current step."
    workspace = Path(str(workspace_dir)).resolve()
    candidate = Path(user_path).expanduser()
    candidate = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    if not candidate.is_relative_to(workspace):
        return None, "Path must resolve inside the current workspace."
    return candidate, None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_remote_job_input(
    job_id: str,
    source_path: str,
    destination_path: str,
    tool_context: ToolContext,
    role: str = "input",
    required: bool = True,
) -> dict[str, Any]:
    """Upload a workspace input file into a tracked interactive remote job.

    ``source_path`` must resolve inside the current workspace. Use an
    absolute remote path for ``destination_path`` such as
    ``/home/user/input.in``. The local file's size and SHA-256 are recorded in
    the public task contract; local paths and file contents are never stored.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    source, error = _resolve_workspace_child(tool_context, source_path)
    if error is not None:
        return {"status": "error", "message": f"Upload failed: {error}"}
    try:
        digest = _sha256_file(source)
        input_record = make_public_input_record(
            role=role,
            remote_path=destination_path,
            size=source.stat().st_size,
            sha256=digest,
            required=required,
        )
    except (OSError, RemoteTaskContractError) as exc:
        return {"status": "error", "message": f"Upload failed: {exc}"}
    service = _service()
    try:
        result = service.upload_job_file(job_id, source, destination_path)
    except Exception as exc:
        return {"status": "error", "message": f"Upload failed: {exc}"}
    try:
        durable = service.store.get_job(job_id) or {}
        specification = durable.get("specification")
        specification = specification if isinstance(specification, dict) else {}
        existing_contract = specification.get("task_contract")
        existing_contract = existing_contract if isinstance(existing_contract, dict) else {}
        existing_inputs = existing_contract.get("inputs")
        existing_inputs = existing_inputs if isinstance(existing_inputs, list) else []
        inputs = [
            item
            for item in existing_inputs
            if isinstance(item, dict) and item.get("remote_path") != destination_path
        ]
        inputs.append(input_record)
        contract = build_remote_task_envelope(
            task_run_id=(
                existing_contract.get("task_run_id")
                or specification.get("stable_name")
                or job_id
            ),
            attempt=existing_contract.get("attempt") or 1,
            workload_profile=(
                existing_contract.get("workload_profile")
                or specification.get("workload_kind")
                or "generic"
            ),
            inputs=inputs,
        )
        presentation = specification.get("presentation")
        presentation = dict(presentation) if isinstance(presentation, dict) else {}
        presentation["current_phase"] = "queue"
        service.store.merge_specification(
            job_id,
            {"task_contract": contract, "presentation": presentation},
        )
        result["input"] = input_record
        result["contract_digest"] = contract["contract_digest"]
    except Exception as exc:
        # The remote upload already succeeded. Surface an observability warning
        # instead of encouraging a caller to repeat the transfer blindly.
        result["contract_warning"] = f"Input uploaded but its public manifest was not updated: {exc}"
    return result


def download_remote_job_output(
    job_id: str,
    source_path: str,
    destination_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Download a file from a tracked interactive remote job into the local workspace.

    ``source_path`` is an absolute path on the remote side (e.g.
    ``/home/user/CHGCAR``). ``destination_path`` must resolve inside the
    current workspace. For a batch job (e.g. `bohr_job`), use
    `collect_remote_job_outputs` instead once the job has succeeded.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    destination, error = _resolve_workspace_child(tool_context, destination_path)
    if error is not None:
        return {"status": "error", "message": f"Download failed: {error}"}
    try:
        return _service().download_job_file(job_id, source_path, destination)
    except Exception as exc:
        return {"status": "error", "message": f"Download failed: {exc}"}


def collect_remote_job_outputs(
    job_id: str,
    destination_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Pull a finished batch job's declared output files into the local workspace.

    Only valid once `get_remote_job_status` reports ``status: succeeded``.
    ``destination_path`` must resolve inside the current workspace as a
    directory. A repeated call after outputs are already collected is a
    durable no-op that returns the same artifact list rather than
    downloading twice.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    destination, error = _resolve_workspace_child(tool_context, destination_path)
    if error is not None:
        return {"status": "error", "message": f"Output collection failed: {error}"}
    try:
        collected = _service().collect_job_outputs(job_id, destination)
    except Exception as exc:
        return {"status": "error", "message": f"Output collection failed: {exc}"}
    return {
        "job_id": collected["job_id"],
        "status": collected["status"],
        "artifacts": collected.get("artifacts", []),
    }
