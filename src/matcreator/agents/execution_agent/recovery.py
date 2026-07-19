from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ...control_plane.remote_jobs import ACTIVE_REMOTE_JOB_STATUSES, RemoteJobStore
from ...workspace import ADK_DIR
from ..execution_graph_state import get_execution_graph, set_execution_graph

_RECOVERY_DIR = "recovery"
_STALE_AFTER_SECONDS = int(os.environ.get("STEP_RECOVERY_STALE_AFTER", "60"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "unknown")


def _recovery_root(session_id: Optional[str] = None, recovery_base_dir: Optional[str | Path] = None) -> Path:
    root = Path(recovery_base_dir).expanduser().resolve() if recovery_base_dir else ADK_DIR / _RECOVERY_DIR
    if session_id:
        return root / _safe_id(session_id)
    return root


def _attempt_public_data(attempt: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in attempt.items() if not key.startswith("_")}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _next_attempt_number(node_dir: Path) -> int:
    numbers: list[int] = []
    for path in node_dir.glob("attempt-*.json"):
        try:
            numbers.append(int(path.stem.removeprefix("attempt-")))
        except ValueError:
            continue
    return max(numbers, default=0) + 1


def _write_attempt(attempt: dict[str, Any]) -> None:
    public = _attempt_public_data(attempt)
    _write_json(Path(attempt["_attempt_path"]), public)
    _write_json(Path(attempt["_latest_path"]), public)


def _process_is_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_stale_running_attempt(
    attempt: dict[str, Any],
    *,
    stale_after_seconds: int,
) -> bool:
    if attempt.get("status") != "running":
        return False
    if not _process_is_alive(attempt.get("pid")):
        return True
    heartbeat_at = _parse_time(attempt.get("heartbeat_at") or attempt.get("started_at"))
    if heartbeat_at is None:
        return True
    return (datetime.now(timezone.utc) - heartbeat_at).total_seconds() > stale_after_seconds


def start_node_attempt(
    *,
    workspace_dir: str | Path,
    session_id: str,
    node_id: str,
    step_id: str,
    step_number: int,
    action: str,
    suggested_skills: list[str],
    prior_context: Optional[str],
    graph_id: Optional[str] = None,
    recovery_base_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    """Persist a durable running-attempt record for one graph node."""
    node_dir = _recovery_root(session_id, recovery_base_dir) / _safe_id(node_id)
    attempt_number = _next_attempt_number(node_dir)
    attempt_path = node_dir / f"attempt-{attempt_number:03d}.json"
    latest_path = node_dir / "latest.json"
    now = _now()
    attempt = {
        "session_id": session_id,
        "graph_id": graph_id,
        "node_id": node_id,
        "step_id": step_id,
        "step_number": step_number,
        "attempt": attempt_number,
        "status": "running",
        "pid": os.getpid(),
        "started_at": now,
        "heartbeat_at": now,
        "completed_at": None,
        "action": action,
        "suggested_skills": suggested_skills,
        "prior_context": prior_context,
        "workspace_dir": str(Path(workspace_dir).expanduser().resolve()),
        "_attempt_path": str(attempt_path),
        "_latest_path": str(latest_path),
    }
    _write_attempt(attempt)
    return attempt


def heartbeat_node_attempt(attempt: dict[str, Any]) -> None:
    """Refresh the heartbeat for a running attempt."""
    if not attempt or attempt.get("status") != "running":
        return
    if "remote_job" not in attempt:
        persisted = _read_json(Path(attempt["_latest_path"]))
        if isinstance(persisted.get("remote_job"), dict):
            attempt["remote_job"] = persisted["remote_job"]
    attempt["heartbeat_at"] = _now()
    _write_attempt(attempt)


def record_remote_job_reference(
    *,
    session_id: str,
    node_id: str,
    job_id: str,
    provider: str,
    external_id: str | None,
    recovery_base_dir: Optional[str | Path] = None,
) -> None:
    """Attach a persisted remote-job identity to the current node attempt.

    The job store remains the authoritative provider state. The recovery record
    only carries enough identity to prevent duplicate submission after restart.
    """
    latest_path = _recovery_root(session_id, recovery_base_dir) / _safe_id(node_id) / "latest.json"
    attempt = _read_json(latest_path)
    if not attempt or attempt.get("session_id") != session_id or attempt.get("node_id") != node_id:
        return
    attempt["remote_job"] = {
        "job_id": job_id,
        "provider": provider,
        "external_id": external_id,
        "recorded_at": _now(),
    }
    attempt["heartbeat_at"] = _now()
    attempt_path = latest_path.parent / f"attempt-{int(attempt.get('attempt', 0)):03d}.json"
    attempt["_attempt_path"] = str(attempt_path)
    attempt["_latest_path"] = str(latest_path)
    _write_attempt(attempt)


def finish_node_attempt(
    attempt: dict[str, Any],
    *,
    status: str,
    result: Optional[dict[str, Any]] = None,
    artifacts: Optional[list[str]] = None,
    message: Optional[str] = None,
) -> None:
    """Mark an attempt complete in the durable recovery journal."""
    if not attempt:
        return
    attempt["status"] = status
    attempt["completed_at"] = _now()
    attempt["heartbeat_at"] = attempt["completed_at"]
    if result is not None:
        attempt["result"] = result
    if artifacts is not None:
        attempt["artifacts"] = artifacts
    if message:
        attempt["message"] = message
    _write_attempt(attempt)


def _attempt_summary(attempt: dict[str, Any]) -> str:
    result = attempt.get("result") if isinstance(attempt.get("result"), dict) else {}
    return (
        result.get("concise_summary")
        or result.get("replan_reason")
        or attempt.get("message")
        or f"Recovered {attempt.get('status')} attempt {attempt.get('attempt')}"
    )


def _mark_attempt_stale(latest_path: Path, attempt: dict[str, Any]) -> None:
    now = _now()
    attempt["status"] = "stale"
    attempt["completed_at"] = now
    attempt["stale_at"] = now
    attempt["message"] = "Running attempt was stale after process restart or missed heartbeat."
    attempt_path = latest_path.parent / f"attempt-{int(attempt.get('attempt', 0)):03d}.json"
    attempt["_attempt_path"] = str(attempt_path)
    attempt["_latest_path"] = str(latest_path)
    _write_attempt(attempt)


def _active_remote_job(attempt: dict[str, Any]) -> dict[str, Any] | None:
    reference = attempt.get("remote_job")
    if not isinstance(reference, dict) or not reference.get("job_id"):
        return None
    try:
        job = RemoteJobStore(ADK_DIR / "remote-jobs.db").get_job(str(reference["job_id"]))
    except Exception:
        return None
    if (
        job
        and job.get("session_id") == attempt.get("session_id")
        and job.get("node_id") == attempt.get("node_id")
        and job.get("status") in ACTIVE_REMOTE_JOB_STATUSES
    ):
        return job
    return None


def reconcile_recovery_state(
    state: Any,
    workspace_dir: str | Path,
    *,
    stale_after_seconds: int = _STALE_AFTER_SECONDS,
    recovery_base_dir: Optional[str | Path] = None,
) -> list[dict[str, Any]]:
    """Fold durable attempt records back into the in-memory execution graph.

    Completed attempts repair graph state after a crash between step completion
    and status update. A stale local attempt with active remote work becomes
    ``waiting`` instead of ``pending`` so normal scheduling cannot resubmit it.
    """
    graph = get_execution_graph(state)
    if not isinstance(graph, dict):
        return []
    nodes = graph.get("nodes") or {}
    if not isinstance(nodes, dict):
        return []
    graph_id = graph.get("graph_id")

    actions: list[dict[str, Any]] = []
    session_id = state.get("session_id") if hasattr(state, "get") else None
    root = _recovery_root(session_id if isinstance(session_id, str) else None, recovery_base_dir)
    for latest_path in sorted(root.glob("*/latest.json")):
        attempt = _read_json(latest_path)
        if isinstance(session_id, str) and attempt.get("session_id") != session_id:
            continue
        # Node IDs are only unique within a graph.  Never apply a completed or
        # stale attempt from an earlier plan to a newly validated plan that
        # happens to reuse the same node ID.  Graphs created before graph_id was
        # introduced retain the legacy recovery behaviour.
        if graph_id is not None and attempt.get("graph_id") != graph_id:
            continue
        node_id = attempt.get("node_id")
        if not isinstance(node_id, str) or node_id not in nodes:
            continue

        node = nodes[node_id]
        node_status = node.get("status")
        attempt_status = attempt.get("status")

        if node_status in ("pending", "running") and attempt_status == "running" and _is_stale_running_attempt(
            attempt,
            stale_after_seconds=stale_after_seconds,
        ):
            remote_job = _active_remote_job(attempt)
            if remote_job is not None:
                node["status"] = "waiting"
                node["result"] = "Recovered active remote job; waiting for provider completion."
                node["remote_job"] = {
                    "job_id": remote_job["job_id"],
                    "provider": remote_job["provider"],
                    "external_id": remote_job["external_id"],
                    "status": remote_job["status"],
                }
                node["recovery"] = {
                    "attempt": attempt.get("attempt"),
                    "status": "waiting_remote",
                    "recovered_at": _now(),
                }
                actions.append({"node_id": node_id, "action": "wait_for_remote_job", "status": "waiting"})
                continue
            _mark_attempt_stale(latest_path, attempt)
            node["status"] = "pending"
            node["result"] = "Recovered stale running attempt; retrying node."
            node["recovery"] = {
                "attempt": attempt.get("attempt"),
                "status": "stale",
                "recovered_at": _now(),
            }
            actions.append({"node_id": node_id, "action": "reset_stale_running", "status": "pending"})
            continue

        if node_status in ("pending", "running") and attempt_status in {
            "success",
            "failed",
            "needs_replanning",
            "cancelled",
        }:
            recovered_status = "failed" if attempt_status == "needs_replanning" else attempt_status
            node["status"] = recovered_status
            node["result"] = _attempt_summary(attempt)
            node["recovery"] = {
                "attempt": attempt.get("attempt"),
                "status": attempt_status,
                "recovered_at": _now(),
            }
            actions.append({"node_id": node_id, "action": "recover_completed", "status": recovered_status})

    if actions:
        set_execution_graph(state, graph)
    return actions
