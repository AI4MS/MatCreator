"""Remote-job tools available to isolated step executors.

Submission is provider-specific — a bohr sandbox needs a template while a
batch job needs a machine type and image, so there is one submit tool per
provider (``submit_bohr_sandbox``, ``submit_bohr_batchjob``). Every operation
after submission dispatches on the ``job_id`` alone and works the same for
any provider, so adding a new provider plugin never requires a new
post-submission tool here.

``submit_e2b_sandbox`` is retained below for existing/in-flight e2b jobs and
its unit tests, but is no longer registered on the step executor — new
submissions go through ``submit_bohr_sandbox``/``submit_bohr_batchjob`` instead.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from ...control_plane.providers.e2b import E2BConnectionConfig
from ...control_plane.providers.registry import RetiredProviderError, require_supported_provider
from ...control_plane.remote_job_service import RemoteJobService
from ...control_plane.remote_jobs import TERMINAL_REMOTE_JOB_STATUSES, RemoteJobStore
from ...workspace import ADK_DIR
from .recovery import record_remote_job_reference

# Every terminal status except "collected" (the successful end of a batch
# job) means the submission is not usable and must not be reported as ready.
_FAILED_SUBMISSION_STATUSES = TERMINAL_REMOTE_JOB_STATUSES - {"collected"}


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
) -> dict[str, Any]:
    """Create or reuse a tracked E2B sandbox for the current execution step.

    The configured E2B API key, endpoint, and project ID are used server-side.
    Never use shell commands or include credentials in tool inputs. A repeated
    call for the same step and template returns the existing sandbox record.
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
    persisted_specification = {key: value for key, value in spec.items() if key != "api_key"}
    result = _submit(
        tool_context,
        provider="e2b",
        spec=spec,
        discriminator=template,
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
) -> dict[str, Any]:
    """Create or reuse a tracked Bohrium CLI sandbox (`bohr sandbox`) for the current step.

    Both this and `submit_e2b_sandbox` reach the same Bohrium sandbox
    platform; use this one when only the `bohr` CLI (not the E2B SDK/API key)
    is available in the current environment. An explicit ``template`` is
    required (e.g. ``doc-compiler``). ``gpu`` selects a GPU shortcut template
    (``4090``/``5090``/``l20``). Falls back to the `BOHRIUM_PROJECT_ID`
    environment variable if ``project_id`` is omitted.
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
    spec = {
        "project_id": resolved_project_id,
        "template": template,
        "timeout": timeout,
        "image": image,
        "gpu": gpu,
        "never_timeout": never_timeout,
        "env": env or {},
    }
    result = _submit(
        tool_context,
        provider="bohr_sandbox",
        spec=spec,
        discriminator=template,
    )
    return _submission_response(
        result,
        id_field="sandbox_id",
        success_message="Tracked bohr sandbox is ready. Use its job_id for status or controls.",
    )


def _coerce_out_files(out_files: Any) -> tuple[list[str] | None, str | None]:
    """Normalize ``out_files`` into ``(list_of_paths, None)`` or ``(None, error)``.

    LLM function calls sometimes deliver the array serialized as one string
    (JSON, Python literal, or comma-separated). Coerce those shapes here, and
    validate BEFORE submission, so an argument-shape mistake never creates a
    durably failed job record.
    """
    if out_files is None:
        return None, None
    if isinstance(out_files, str):
        text = out_files.strip()
        parsed: Any = None
        for load in (json.loads, ast.literal_eval):
            try:
                parsed = load(text)
                break
            except (ValueError, SyntaxError):
                continue
        if isinstance(parsed, str):
            text = parsed.strip()
            parsed = None
        if parsed is None:
            out_files = [part.strip() for part in text.split(",")]
        else:
            out_files = parsed
    if isinstance(out_files, (list, tuple)) and not out_files:
        return None, None
    if not isinstance(out_files, (list, tuple)) or any(
        not isinstance(path, str) or not path.strip() for path in out_files
    ):
        return None, (
            "out_files must be a JSON array of nonempty path strings, "
            'e.g. ["vasprun.xml", "OUTCAR", "log"]. Got: ' + repr(out_files)[:200]
        )
    return list(out_files), None


def submit_bohr_batchjob(
    tool_context: ToolContext,
    *,
    name: str,
    image: str,
    command: str,
    project_id: int | None = None,
    machine_type: str | None = None,
    sku_id: int | None = None,
    input_path: str | None = None,
    out_files: list[str] | None = None,
    max_run_time: str = "24h",
    max_wait_time: str = "30m",
) -> dict[str, Any]:
    """Submit or reuse a tracked sandbox-based job through `bohr batchjob submit`.

    Supply exactly one of machine_type or sku_id, discovered with
    `bohr batchjob machine list -o json`. project_id falls back to
    BOHRIUM_PROJECT_ID. input_path is a path RELATIVE to the step's working
    directory (the workspace) — never an absolute or fabricated path — naming
    a regular file or nonempty directory of regular files (no symlinks). A
    directory's contents are unpacked at the root of the remote job's working
    directory, so command references them by bare names: input_path="si_scf"
    containing run.sh -> command "bash run.sh". out_files is a JSON array of
    remote path strings to retain, e.g. ["vasprun.xml", "OUTCAR", "log"] —
    outputs AND logs, never a single comma-joined string. Durations use units,
    e.g. '2h' or '90s', and must be at least one second. Express the entire
    computation in command: interactive execution and incremental transfers
    are not supported. After status succeeds, collect outputs into a new,
    nonexistent workspace directory.
    """
    resolved_project_id = project_id if project_id is not None else os.environ.get("BOHRIUM_PROJECT_ID", "")
    missing = [
        name
        for name, value in (
            ("project_id", resolved_project_id),
            ("name", name),
            ("image", image),
            ("command", command),
        )
        if not value
    ]
    if missing:
        return {
            "status": "error",
            "message": f"Missing required field(s) for bohr batchjob submission: {', '.join(missing)}",
        }
    if bool(machine_type) == (sku_id is not None):
        return {"status": "error", "message": "Specify exactly one of machine_type or sku_id."}
    out_files, out_files_error = _coerce_out_files(out_files)
    if out_files_error:
        return {"status": "error", "message": out_files_error}
    input_root: str | None = None
    if input_path is not None:
        if not isinstance(input_path, str) or not input_path.strip():
            return {
                "status": "error",
                "message": "input_path must be a nonempty path relative to the step workspace.",
            }
        source, workspace, error = _resolve_workspace_child(tool_context, input_path)
        if error:
            return {"status": "error", "message": error}
        assert source is not None and workspace is not None
        if source == workspace:
            return {
                "status": "error",
                "message": (
                    f"input_path must name a file or subdirectory of the workspace ({workspace}), "
                    "not the workspace root itself."
                ),
            }
        if _workspace_join(tool_context, input_path).is_symlink():
            return {"status": "error", "message": "Batch Job input_path must not be a symbolic link."}
        if not source.exists():
            return {
                "status": "error",
                "message": (
                    f"input_path '{input_path}' does not exist in the workspace ({workspace}). "
                    "Create the file or directory there first, then pass its workspace-relative path."
                ),
            }
        input_path = str(source.relative_to(workspace))
        input_root = str(workspace)
    spec = {
        "project_id": resolved_project_id,
        "name": name,
        "machine_type": machine_type,
        "sku_id": sku_id,
        "image": image,
        "command": command,
        "input_path": input_path,
        "input_root": input_root,
        "out_files": out_files,
        "max_run_time": max_run_time,
        "max_wait_time": max_wait_time,
    }
    result = _submit(
        tool_context,
        provider="bohr_batchjob",
        spec=spec,
        discriminator=f"bohr_batchjob:{name}",
    )
    return _submission_response(
        result,
        id_field="batchjob_id",
        success_message=(
            "Tracked bohr batch job is submitted. Poll get_remote_job_status until it "
            "reports succeeded, then call collect_remote_job_outputs."
        ),
    )


def attach_bohr_batchjob(
    tool_context: ToolContext,
    *,
    batchjob_id: str,
) -> dict[str, Any]:
    """Attach an already-submitted Batch Job by its explicit string ID; never submit.

    Uses the existing bohr account authentication to read the remote status.
    Repeated attachment reuses the current session's durable job record.
    Use the returned job_id for status, controls, and output collection.
    """
    session_id = str(tool_context.state.get("session_id") or "")
    error = None
    if not isinstance(batchjob_id, str) or not batchjob_id.strip():
        error = "An explicit nonempty string batchjob_id is required."
    elif not session_id:
        error = "No session_id is available for remote-job attachment."
    if error:
        return {"status": "error", "job_id": None, "batchjob_id": None, "error": error, "message": error}
    batchjob_id = batchjob_id.strip()
    node_id = _node_id(tool_context)
    try:
        job = _service().attach_job(
            owner_id=_owner_id(tool_context),
            session_id=session_id,
            provider="bohr_batchjob",
            external_id=batchjob_id,
            node_id=node_id,
            step_number=tool_context.state.get("step_number"),
        )
    except Exception as exc:
        error = f"bohr batchjob attachment failed: {exc}"
        return {
            "status": "error", "job_id": None, "batchjob_id": batchjob_id,
            "error": error, "message": error,
        }
    record_remote_job_reference(
        session_id=session_id,
        node_id=node_id,
        job_id=job["job_id"],
        provider="bohr_batchjob",
        external_id=job["external_id"],
    )
    return {
        "status": job["status"],
        "job_id": job["job_id"],
        "batchjob_id": job["external_id"],
        "error": job.get("error"),
    }


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
    try:
        require_supported_provider(job["provider"])
    except RetiredProviderError as exc:
        result.update(
            status="error",
            tracked_status=job["status"],
            message=exc.args[0],
            provider_supported=False,
        )
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
    result = {"job_id": terminated["job_id"], "status": terminated["status"], "external_id": terminated["external_id"]}
    if terminated.get("error"):
        result["error"] = terminated["error"]
    return result


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
    `bohr_batchjob`) returns an error explaining that its whole command must run
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


def _workspace_join(tool_context: ToolContext, user_path: str) -> Path:
    """Join ``user_path`` against the raw workspace_dir WITHOUT resolving.

    Symlink checks need the unresolved path — ``_resolve_workspace_child``
    resolves through symlinks, so its result can never reveal one.
    """
    original = Path(user_path).expanduser()
    if original.is_absolute():
        return original
    return Path(str(tool_context.state["workspace_dir"])) / original


def _resolve_workspace_child(
    tool_context: ToolContext,
    user_path: str,
) -> tuple[Path | None, Path | None, str | None]:
    """Resolve ``user_path`` against the current workspace, confining it.

    Returns ``(resolved_path, workspace_root, None)`` on success or
    ``(None, None, message)`` if the workspace is unavailable or the path
    escapes it. Shared by upload (source) and download (destination) so
    confinement logic cannot drift between them.
    """
    workspace_dir = tool_context.state.get("workspace_dir")
    if not workspace_dir:
        return None, None, "No workspace_dir is available for the current step."
    workspace = Path(str(workspace_dir)).resolve()
    candidate = Path(user_path).expanduser()
    candidate = candidate.resolve() if candidate.is_absolute() else (workspace / candidate).resolve()
    if not candidate.is_relative_to(workspace):
        return None, None, (
            f"Path must resolve inside the current workspace ({workspace}). "
            "Pass a path relative to the workspace, e.g. 'si_scf' or './si_scf'."
        )
    return candidate, workspace, None


def upload_remote_job_input(
    job_id: str,
    source_path: str,
    destination_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Upload a workspace input file into a tracked interactive remote job.

    ``source_path`` must resolve inside the current workspace. Use an
    absolute remote path for ``destination_path`` such as
    ``/home/user/input.in``.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    source, _, error = _resolve_workspace_child(tool_context, source_path)
    if error is not None:
        return {"status": "error", "message": f"Upload failed: {error}"}
    try:
        return _service().upload_job_file(job_id, source, destination_path)
    except Exception as exc:
        return {"status": "error", "message": f"Upload failed: {exc}"}


def download_remote_job_output(
    job_id: str,
    source_path: str,
    destination_path: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Download a file from a tracked interactive remote job into the local workspace.

    ``source_path`` is an absolute path on the remote side (e.g.
    ``/home/user/CHGCAR``). ``destination_path`` must resolve inside the
    current workspace. For a batch job (e.g. `bohr_batchjob`), use
    `collect_remote_job_outputs` instead once the job has succeeded.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    destination, _, error = _resolve_workspace_child(tool_context, destination_path)
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
    new, nonexistent directory for Batch Jobs; do not create it first.
    A repeated call after outputs are already collected is a
    durable no-op that returns the same artifact list rather than
    downloading twice.
    """
    job = get_remote_job_status(job_id, tool_context)
    if job.get("status") == "error":
        return job
    _, _, error = _resolve_workspace_child(tool_context, destination_path)
    if error is not None:
        return {"status": "error", "message": f"Output collection failed: {error}"}
    original = _workspace_join(tool_context, destination_path)
    # An occupied destination is the most common collection mistake; catch it
    # before any status churn. A replay of an already-collected job skips this
    # check because its original destination legitimately exists.
    if job.get("status") != "collected" and (original.exists() or original.is_symlink()):
        return {
            "status": "error",
            "message": (
                f"Output collection failed: destination '{original}' already exists. "
                "Choose a NEW, nonexistent workspace directory; do not pre-create it."
            ),
        }
    try:
        collected = _service().collect_job_outputs(job_id, original)
    except Exception as exc:
        return {"status": "error", "message": f"Output collection failed: {exc}"}
    result = {
        "job_id": collected["job_id"],
        "status": collected["status"],
        "artifacts": collected.get("artifacts", []),
    }
    if collected.get("error"):
        result["error"] = collected["error"]
        if collected["status"] == "succeeded":
            result["message"] = (
                "Output collection failed but the remote job itself is still succeeded. "
                "Retry collect_remote_job_outputs with a new, nonexistent destination directory."
            )
    return result
