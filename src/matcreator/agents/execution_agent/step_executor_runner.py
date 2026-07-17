from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
from contextlib import aclosing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.tools.agent_tool import ForwardingArtifactService
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from ...llm_cards import LLMCard, select_executor_llm_card
from ...workspace import get_session_workdir
from .step_executor import (
    STEP_EXECUTOR_AGENT_NAME,
    StepExecutorInput,
    StepExecutorResult,
    build_step_executor_agent,
)
from .recovery import finish_node_attempt, heartbeat_node_attempt, start_node_attempt
from ..graph_logger import AgentGraphLogger
from ..session_log import (
    append_session_log_entry,
    collect_artifact_paths,
    is_session_log_state_key,
    session_artifacts_from_state,
)
from ..cancellation import (
    is_cancellation_requested,
    get_cancellation_reason,
    is_step_cancellation_requested,
    clear_step_cancellation,
)

logger = logging.getLogger(__name__)

# Time between flag-file polls in the watcher task.
# Event-count polling is unreliable when events are sparse (e.g. mid-LLM-call),
# so we use a wall-clock interval instead.
_CANCEL_POLL_INTERVAL = 0.5  # seconds

# Wall-clock timeout for a single step or sub-step execution.
# A sub-step that exceeds this returns needs_replanning instead of hanging.
_SUB_STEP_TIMEOUT = int(os.environ.get("SUB_STEP_TIMEOUT", "3600"))  # seconds
_RECOVERY_HEARTBEAT_INTERVAL = int(os.environ.get("STEP_RECOVERY_HEARTBEAT_INTERVAL", "10"))  # seconds
_MAX_INPUT_IMAGE_ATTACHMENTS = int(os.environ.get("MATCREATOR_MAX_INPUT_IMAGE_ATTACHMENTS", "4"))
_MAX_INPUT_IMAGE_BYTES = int(os.environ.get("MATCREATOR_MAX_INPUT_IMAGE_BYTES", str(5 * 1024 * 1024)))
_IMAGE_PATH_RE = re.compile(
    r"(?P<path>(?:~|/|\./|\.\./)?[^\s'\"<>]+?\.(?:png|jpe?g|webp|gif|bmp|tiff?))",
    re.IGNORECASE,
)
_IMAGE_CONTEXT_TOKENS = {
    "image",
    "images",
    "plot",
    "plots",
    "figure",
    "figures",
    "screenshot",
    "diagram",
    "visual",
    "vision",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response_dict(response) -> dict:
    """Best-effort conversion for ADK function response payloads."""
    if isinstance(response, dict):
        return response
    try:
        return dict(response or {})
    except (TypeError, ValueError):
        return {}


def _append_unique(values: list[str], value) -> None:
    if isinstance(value, str) and value and value not in values:
        values.append(value)


def _image_mime_type(path: Path) -> str | None:
    guessed, _ = mimetypes.guess_type(path.name)
    if guessed and guessed.startswith("image/"):
        return guessed
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return None


def _resolve_input_image_path(raw_path: str, workspace_dir: Path) -> Path | None:
    cleaned = raw_path.strip().strip("'\"`.,;:)]}")
    if not cleaned:
        return None
    path = Path(cleaned).expanduser()
    if not path.is_absolute():
        path = workspace_dir / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file() or _image_mime_type(resolved) is None:
        return None
    return resolved


def _extract_image_paths_from_text(text: str | None, workspace_dir: Path) -> list[Path]:
    paths: list[Path] = []
    for match in _IMAGE_PATH_RE.finditer(text or ""):
        resolved = _resolve_input_image_path(match.group("path"), workspace_dir)
        if resolved is not None and resolved not in paths:
            paths.append(resolved)
    return paths


def _mentions_image_context(*values: str | None) -> bool:
    tokens = set(re.findall(r"[a-z0-9_+-]+", " ".join(value or "" for value in values).lower()))
    return bool(tokens & _IMAGE_CONTEXT_TOKENS)


def _state_to_dict(state) -> dict:
    if isinstance(state, dict):
        return state
    to_dict = getattr(state, "to_dict", None)
    if callable(to_dict):
        try:
            data = to_dict()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _input_image_paths(
    *,
    action: str,
    prior_context: str | None,
    workspace_dir: Path,
    state: dict,
) -> list[Path]:
    paths: list[Path] = []
    for candidate in [
        *_extract_image_paths_from_text(action, workspace_dir),
        *_extract_image_paths_from_text(prior_context, workspace_dir),
    ]:
        if candidate not in paths:
            paths.append(candidate)

    if _mentions_image_context(action, prior_context):
        for artifact in session_artifacts_from_state(_state_to_dict(state)):
            resolved = _resolve_input_image_path(artifact, workspace_dir)
            if resolved is not None and resolved not in paths:
                paths.append(resolved)

    return paths[:_MAX_INPUT_IMAGE_ATTACHMENTS]


def _build_step_content(
    step_input: StepExecutorInput,
    *,
    llm_card: LLMCard,
    workspace_dir: Path,
    state: dict,
) -> tuple[types.Content, list[str]]:
    parts = [types.Part.from_text(text=step_input.model_dump_json(exclude_none=True))]
    attached_paths: list[str] = []
    if llm_card.supports_image_input():
        for image_path in _input_image_paths(
            action=step_input.action,
            prior_context=step_input.prior_context,
            workspace_dir=workspace_dir,
            state=state,
        ):
            try:
                if image_path.stat().st_size > _MAX_INPUT_IMAGE_BYTES:
                    logger.warning(
                        "[step_executor_runner] skipping oversized input image for node: %s",
                        image_path,
                    )
                    continue
                parts.append(types.Part.from_bytes(
                    data=image_path.read_bytes(),
                    mime_type=_image_mime_type(image_path) or "image/png",
                ))
                attached_paths.append(str(image_path))
            except OSError as exc:
                logger.warning(
                    "[step_executor_runner] could not attach input image %s: %s",
                    image_path,
                    exc,
                )

    return types.Content(role="user", parts=parts), attached_paths


def _is_within_any_root(path: Path, roots: list[Path]) -> bool:
    resolved_path = path.resolve()
    for root in roots:
        try:
            resolved_path.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def _artifact_allowed_roots(
    step_workspace: Path,
    suggested_skills: list[str],
    output_dir: Optional[Path] = None,
) -> list[Path]:
    roots = [output_dir or step_workspace]
    if "skill-creation" in suggested_skills:
        from ...skill import user_skills_dir

        roots.append(user_skills_dir().expanduser())
    return roots


def _split_verified_artifacts(
    artifacts: list[str],
    allowed_roots: Optional[list[Path]] = None,
) -> tuple[list[str], list[str]]:
    """Return (existing, invalid_or_missing) artifact paths."""
    existing: list[str] = []
    missing: list[str] = []
    for artifact in artifacts:
        artifact_path = Path(artifact).expanduser()
        is_valid = artifact_path.is_absolute() and artifact_path.exists()
        if is_valid and allowed_roots is not None:
            is_valid = _is_within_any_root(artifact_path, allowed_roots)
        if is_valid:
            existing.append(str(artifact_path))
        else:
            missing.append(artifact)
    return existing, missing


def _verify_step_result_artifacts(
    result: StepExecutorResult,
    allowed_roots: Optional[list[Path]] = None,
) -> tuple[StepExecutorResult, list[str]]:
    """Prevent successful step results from claiming nonexistent artifacts."""
    existing_artifacts, missing_artifacts = _split_verified_artifacts(
        result.artifacts,
        allowed_roots=allowed_roots,
    )
    result.artifacts = existing_artifacts

    if result.status == "success" and missing_artifacts:
        missing_text = ", ".join(missing_artifacts)
        result.status = "needs_replanning"
        result.replan_reason = (
            "Step reported success but claimed artifact path(s) were not found "
            "or were outside allowed artifact roots: "
            f"{missing_text}"
        )
        logger.warning("[step_executor_runner] %s", result.replan_reason)
        result.concise_summary = result.replan_reason
        result.key_results = result.replan_reason

    return result, missing_artifacts


async def _watch_for_cancellation(
    target: asyncio.Task,
    session_id: str,
    step_number: int,
) -> None:
    """Poll cancellation flags at fixed intervals; call target.cancel() when flagged.

    Runs concurrently with the step execution task.  Using task.cancel() raises
    CancelledError at the target's current await point (including in-flight HTTP
    calls to the LLM), which is the only reliable way to stop a running coroutine.
    """
    try:
        while not target.done():
            await asyncio.sleep(_CANCEL_POLL_INTERVAL)
            if (
                is_cancellation_requested(session_id)
                or is_step_cancellation_requested(session_id, step_number)
            ):
                logger.warning(
                    "[CANCEL] Step %d watcher triggered task cancellation (session=%s)",
                    step_number, session_id,
                )
                target.cancel()
                return
    except asyncio.CancelledError:
        pass


async def _heartbeat_recovery_attempt(attempt: dict) -> None:
    try:
        while True:
            await asyncio.sleep(_RECOVERY_HEARTBEAT_INTERVAL)
            await asyncio.to_thread(heartbeat_node_attempt, attempt)
    except asyncio.CancelledError:
        pass


async def _cleanup_step_runner(
    runner: Runner,
    tasks: tuple[asyncio.Task, ...],
) -> None:
    """Wait for executor tasks to finish, then close their runner."""
    await asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.wait_for(runner.close(), timeout=5.0)
    except Exception:
        pass


def _log_cancelled_cleanup_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception("[CANCEL] step runner cleanup failed")


def _schedule_step_runner_cleanup(
    runner: Runner,
    tasks: tuple[asyncio.Task, ...],
) -> None:
    cleanup_task = asyncio.create_task(_cleanup_step_runner(runner, tasks))
    cleanup_task.add_done_callback(_log_cancelled_cleanup_result)


async def _stream_step_events(
    runner: Runner,
    session,
    content,
    step_id: str,
    tool_context: ToolContext,
    graph: AgentGraphLogger,
) -> tuple[dict, list[str], list[str], dict]:
    """Stream events from the step executor sub-agent and collect results.

    Returns (step_state_delta, plot_paths, artifact_paths, event_log). Raises
    CancelledError if the task is cancelled mid-stream.
    """
    step_state_delta: dict = {}
    pending_tool_calls: dict[str, dict] = {}
    plot_paths: list[str] = []
    artifact_paths: list[str] = []
    event_log: dict = {"conversation": [], "tool_calls": []}

    async with aclosing(
        runner.run_async(
            user_id=session.user_id, session_id=session.id, new_message=content
        )
    ) as agen:
        async for event in agen:
            if event.actions and event.actions.state_delta:
                tool_context.state.update(event.actions.state_delta)
                step_state_delta.update(event.actions.state_delta)

            if event.content:
                for part in event.content.parts:
                    fc = getattr(part, "function_call", None)
                    fr = getattr(part, "function_response", None)
                    is_thought = getattr(part, "thought", False)
                    text = getattr(part, "text", None)

                    if is_thought and text:
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "thought",
                            "content": text,
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif text and not fc and not fr:
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "text",
                            "content": text,
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif fc and not is_thought:
                        pending_tool_calls[fc.name] = {
                            "name": fc.name,
                            "args_summary": str(dict(fc.args or {}))[:300],
                            "start_time": _now(),
                        }
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "function_call",
                            "content": f"{fc.name}({str(dict(fc.args or {}))[:500]})",
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)
                    elif fr:
                        response = _response_dict(fr.response)
                        _append_unique(plot_paths, response.get("plot_path"))
                        for artifact_path in collect_artifact_paths(response):
                            _append_unique(artifact_paths, artifact_path)

                        record = pending_tool_calls.pop(fr.name, {"name": fr.name, "start_time": _now()})
                        record["result_summary"] = str(fr.response)[:300]
                        record["end_time"] = _now()
                        record["artifacts"] = collect_artifact_paths(response)
                        event_log["tool_calls"].append(record.copy())
                        await asyncio.to_thread(graph.log_tool_call, step_id, record)
                        entry = {
                            "timestamp": _now(),
                            "author": event.author,
                            "type": "function_response",
                            "content": f"{fr.name} → {str(fr.response)[:500]}",
                        }
                        event_log["conversation"].append(entry)
                        await asyncio.to_thread(graph.log_conversation_event, step_id, entry)

    return step_state_delta, plot_paths, artifact_paths, event_log


MAX_RECURSION_DEPTH = 3


async def run_step_executor(
    step_number: int,
    action: str,
    suggested_skills: list[str],
    workspace_dir: str,
    prior_context: Optional[str] = None,
    node_id: Optional[str] = None,
    *,
    tool_context: ToolContext,
) -> dict:
    """Run step_executor as an isolated sub-agent, following AgentTool logic.

    Each invocation gets its own workspace subdirectory. When called from within
    a step_executor (recursion_depth > 0) the workspace is nested under the
    parent's workspace; at depth 0 it lives under the session workdir.
    """
    session_id = tool_context.state.get("session_id", "default")
    recursion_depth = tool_context.state.get("recursion_depth", 0)

    if recursion_depth >= MAX_RECURSION_DEPTH:
        return {
            "status": "error",
            "message": (
                f"Maximum recursion depth ({MAX_RECURSION_DEPTH}) reached. "
                "Execute this action directly or call submit_step_result with needs_replanning."
            ),
        }

    # Use node_id for label when provided (DAG mode); fall back to step_number.
    effective_id = node_id if node_id else str(step_number)

    # All steps CWD directly to the workspace root/session workdir so they can read shared inputs.
    # If configured, generated artifacts are constrained separately by output_dir.
    step_workspace = Path(tool_context.state.get("workspace_dir") or str(get_session_workdir(session_id)))
    step_workspace.mkdir(parents=True, exist_ok=True)
    output_dir_value = tool_context.state.get("output_dir") or tool_context.state.get("session_output_dir")
    output_dir = Path(output_dir_value).expanduser().resolve() if output_dir_value else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    logger.debug("[step_executor_runner] depth=%d node %s workspace: %s", recursion_depth, effective_id, step_workspace)

    graph = AgentGraphLogger(session_id)
    parent_id = tool_context.state.get("_graph_exec_node_id", "orchestrator")
    step_id = f"{parent_id}__node_{effective_id}"
    parent_path = tool_context.state.get("_step_label_path", "")
    step_label_path = f"{parent_path}-{effective_id}" if parent_path else effective_id
    llm_card = select_executor_llm_card(
        action=action,
        suggested_skills=suggested_skills,
        prior_context=prior_context,
    )
    public_llm_card = llm_card.public_dict()
    logger.info(
        "[step_executor_runner] node %s using LLM card %s (%s)",
        effective_id,
        llm_card.name,
        llm_card.model,
    )
    await asyncio.to_thread(graph.log_node_start, step_id, "step", f"Node {step_label_path}", parent_id)

    # Serialize input as user message (matches AgentTool input_schema path)
    step_input = StepExecutorInput(
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir=str(step_workspace),
        output_dir=str(output_dir) if output_dir else None,
        prior_context=prior_context,
    )
    content, input_image_paths = _build_step_content(
        step_input,
        llm_card=llm_card,
        workspace_dir=step_workspace,
        state=tool_context.state,
    )

    # Log input parameters
    await asyncio.to_thread(graph.log_node_input, step_id, {
        "node_id": effective_id,
        "step_number": step_number,
        "action": action,
        "workspace_dir": str(step_workspace),
        "output_dir": str(output_dir) if output_dir else None,
        "prior_context": prior_context,
        "suggested_skills": suggested_skills,
        "llm_card": public_llm_card,
        "input_images": input_image_paths,
    })
    step_input_log = {
        "node_id": effective_id,
        "step_id": step_id,
        "parent_id": parent_id,
        "step_number": step_number,
        "action": action,
        "workspace_dir": str(step_workspace),
        "output_dir": str(output_dir) if output_dir else None,
        "prior_context": prior_context,
        "suggested_skills": suggested_skills,
        "llm_card": public_llm_card,
        "input_images": input_image_paths,
    }
    append_session_log_entry(tool_context, {
        "kind": "step_start",
        **step_input_log,
    })
    recovery_attempt = await asyncio.to_thread(
        start_node_attempt,
        workspace_dir=step_workspace,
        session_id=session_id,
        node_id=str(effective_id),
        step_id=step_id,
        step_number=step_number,
        action=action,
        suggested_skills=suggested_skills,
        prior_context=prior_context,
    )
    if node_id:
        graph_state = tool_context.state.get("execution_graph") or {}
        graph_nodes = graph_state.get("nodes") or {}
        if node_id in graph_nodes:
            graph_nodes[node_id]["status"] = "running"
            graph_nodes[node_id]["recovery"] = {
                "status": "running",
                "started_at": _now(),
            }
            tool_context.state["execution_graph"] = graph_state

    # Pre-step cancellation check — abort before creating the runner if flagged
    if is_cancellation_requested(session_id) or is_step_cancellation_requested(session_id, step_number):
        reason = get_cancellation_reason(session_id) or "user_requested"
        logger.warning(
            "[CANCEL] Node %s aborted before start (session=%s, reason=%s)",
            effective_id, session_id, reason,
        )
        await asyncio.to_thread(graph.log_node_complete, step_id, "failed", summary=f"Cancelled before start: {reason}")
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "cancelled",
            "message": f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        })
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="cancelled",
            message=f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        )
        return {
            "status": "cancelled",
            "message": f"Node {effective_id} skipped: execution cancellation was requested ({reason}).",
        }
    # Create runner with isolated session (mirrors AgentTool)
    invocation_context = tool_context._invocation_context
    child_app_name = (
        invocation_context.app_name if invocation_context else STEP_EXECUTOR_AGENT_NAME
    )
    step_executor_agent = build_step_executor_agent(llm_card)
    runner = Runner(
        app_name=child_app_name,
        agent=step_executor_agent,
        artifact_service=ForwardingArtifactService(tool_context),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
        credential_service=invocation_context.credential_service,
    )

    # Inherit parent state, override workspace_dir with per-step path and increment depth
    state_dict = {
        k: v
        for k, v in tool_context.state.to_dict().items()
        if not k.startswith("_adk") and not is_session_log_state_key(k)
    }
    state_dict["workspace_dir"] = str(step_workspace)
    state_dict["step_number"] = step_number
    if output_dir is not None:
        state_dict["output_dir"] = str(output_dir)
        state_dict["session_output_dir"] = str(output_dir)
    state_dict["recursion_depth"] = recursion_depth + 1
    state_dict["_graph_exec_node_id"] = step_id
    state_dict["_step_label_path"] = step_label_path

    session = await runner.session_service.create_session(
        app_name=child_app_name,
        user_id=invocation_context.user_id,
        state=state_dict,
    )

    # Wrap event streaming in a Task so task.cancel() can raise CancelledError at
    # the current await point (including in-flight LLM HTTP calls).  A sibling
    # watcher task polls cancellation flags on a wall-clock interval and cancels
    # the inner task when a flag is detected.
    inner_task = asyncio.create_task(
        _stream_step_events(runner, session, content, step_id, tool_context, graph)
    )
    watcher = asyncio.create_task(
        _watch_for_cancellation(inner_task, session_id, step_number)
    )
    recovery_heartbeat = asyncio.create_task(_heartbeat_recovery_attempt(recovery_attempt))

    cancelled = False
    timed_out = False
    step_state_delta: dict = {}
    plot_paths: list[str] = []
    artifact_paths: list[str] = []
    event_log: dict = {"conversation": [], "tool_calls": []}
    try:
        step_state_delta, plot_paths, artifact_paths, event_log = await asyncio.wait_for(
            inner_task, timeout=_SUB_STEP_TIMEOUT
        )
    except asyncio.TimeoutError:
        timed_out = True
    except asyncio.CancelledError:
        cancelled = True
    finally:
        if not watcher.done():
            watcher.cancel()
        if not recovery_heartbeat.done():
            recovery_heartbeat.cancel()
        cleanup_tasks = (inner_task, watcher, recovery_heartbeat)
        if cancelled:
            _schedule_step_runner_cleanup(runner, cleanup_tasks)
        else:
            await _cleanup_step_runner(runner, cleanup_tasks)

    if timed_out:
        logger.warning(
            "[TIMEOUT] Step %d timed out after %ds (session=%s)",
            step_number, _SUB_STEP_TIMEOUT, session_id,
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id, "needs_replanning",
            summary=f"Timed out after {_SUB_STEP_TIMEOUT}s",
        )
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "needs_replanning",
            "replan_reason": f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="needs_replanning",
            artifacts=artifact_paths,
            message=f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
        )
        return {
            "status": "needs_replanning",
            "replan_reason": f"Step {step_number} timed out after {_SUB_STEP_TIMEOUT}s.",
        }

    if cancelled:
        reason = get_cancellation_reason(session_id) or "user_requested"
        logger.warning(
            "[CANCEL] Step %d task cancelled (session=%s, reason=%s)",
            step_number, session_id, reason,
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id, "failed", summary=f"Cancelled mid-step ({reason})"
        )
        clear_step_cancellation(session_id, step_number)
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": "cancelled",
            "message": f"Step {step_number} cancelled ({reason}).",
            "events": event_log,
        }, artifacts=artifact_paths)
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status="cancelled",
            artifacts=artifact_paths,
            message=f"Step {step_number} cancelled ({reason}).",
        )
        return {
            "status": "cancelled",
            "message": f"Step {step_number} cancelled ({reason}).",
        }

    if step_state_delta:
        await asyncio.to_thread(graph.log_state_delta, step_id, step_state_delta)
    public_state_delta = {
        key: value for key, value in step_state_delta.items()
        if not key.startswith("_")
    }

    step_result_data = step_state_delta.get("_step_result")
    if step_result_data:
        tool_context.state["_step_result"] = None  # State has no pop(); reset instead
        result = StepExecutorResult.model_validate(step_result_data)
        result, missing_artifacts = _verify_step_result_artifacts(
            result,
            allowed_roots=_artifact_allowed_roots(step_workspace, suggested_skills, output_dir),
        )
        await asyncio.to_thread(
            graph.log_node_complete,
            step_id,
            result.status,
            summary=result.concise_summary,
            artifacts=result.artifacts,
        )
        payload = result.model_dump(exclude_none=True)
        if missing_artifacts:
            payload["missing_artifacts"] = missing_artifacts
            payload["message"] = result.replan_reason
        if plot_paths:
            payload["plot_paths"] = plot_paths
            payload["plot_path"] = plot_paths[0]
        append_session_log_entry(tool_context, {
            "kind": "step_complete",
            **step_input_log,
            "status": result.status,
            "result": payload,
            "state_delta": public_state_delta,
            "events": event_log,
        }, artifacts=[*artifact_paths, *result.artifacts, *plot_paths])
        await asyncio.to_thread(
            finish_node_attempt,
            recovery_attempt,
            status=result.status,
            result=payload,
            artifacts=[*artifact_paths, *result.artifacts, *plot_paths],
        )
        clear_step_cancellation(session_id, step_number)
        return payload

    # submit_step_result was never called — surface as replanning signal
    logger.warning("[step_executor_runner] step %d: submit_step_result was never called", step_number)
    result = StepExecutorResult(
        status="needs_replanning",
        replan_reason="step executor did not call submit_step_result — no result captured",
    )
    await asyncio.to_thread(graph.log_node_complete, step_id, "needs_replanning")
    append_session_log_entry(tool_context, {
        "kind": "step_complete",
        **step_input_log,
        "status": "needs_replanning",
        "result": result.model_dump(),
        "state_delta": public_state_delta,
        "events": event_log,
    }, artifacts=artifact_paths)
    await asyncio.to_thread(
        finish_node_attempt,
        recovery_attempt,
        status="needs_replanning",
        result=result.model_dump(),
        artifacts=artifact_paths,
    )
    clear_step_cancellation(session_id, step_number)
    return result.model_dump()
