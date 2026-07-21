"""Cross-process per-session cancellation flags.

IPC contract:
  - web/main.py calls request_cancellation() → creates flag file
  - step_executor_runner.py calls is_cancellation_requested() → checks file
  - orchestrator calls clear_cancellation() after handling return_to_planner

Flag file path: {workspace_root}/cancellation/{session_id}.flag
File contents:  reason string (e.g. "user_requested")
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _workspace_root(workspace_root: Path | None = None) -> Path:
    if workspace_root is not None:
        return workspace_root.expanduser().resolve()
    from ..workspace import get_workspace_root  # lazy to avoid circular imports

    return get_workspace_root()


def _flag_path(session_id: str, workspace_root: Path | None = None) -> Path:
    return _workspace_root(workspace_root) / "cancellation" / f"{session_id}.flag"


def request_cancellation(
    session_id: str,
    reason: str = "user_requested",
    *,
    workspace_root: Path | None = None,
) -> None:
    """Create the cancellation flag file for session_id."""
    flag = _flag_path(session_id, workspace_root)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(reason, encoding="utf-8")
    logger.info("[cancellation] flag set for session %s: %s", session_id, reason)


def is_cancellation_requested(session_id: str, *, workspace_root: Path | None = None) -> bool:
    """Return True if the cancellation flag file exists."""
    return _flag_path(session_id, workspace_root).exists()


def get_cancellation_reason(
    session_id: str,
    *,
    workspace_root: Path | None = None,
) -> Optional[str]:
    """Return the reason string from the flag file, or None if not flagged."""
    flag = _flag_path(session_id, workspace_root)
    if not flag.exists():
        return None
    try:
        return flag.read_text(encoding="utf-8").strip() or "user_requested"
    except OSError:
        return "user_requested"


def clear_cancellation(session_id: str, *, workspace_root: Path | None = None) -> None:
    """Remove the cancellation flag file (idempotent)."""
    if not session_id:
        return
    try:
        _flag_path(session_id, workspace_root).unlink(missing_ok=True)
        logger.debug("[cancellation] flag cleared for session %s", session_id)
    except OSError as exc:
        logger.warning("[cancellation] failed to clear flag for %s: %s", session_id, exc)


# ---------------------------------------------------------------------------
# Per-step cancellation (targets a single step without stopping the session)
# ---------------------------------------------------------------------------


def _step_flag_path(
    session_id: str,
    step_number: int,
    workspace_root: Path | None = None,
) -> Path:
    return _workspace_root(workspace_root) / "cancellation" / f"{session_id}__step_{step_number}.flag"


def request_step_cancellation(
    session_id: str,
    step_number: int,
    reason: str = "user_requested",
    *,
    workspace_root: Path | None = None,
) -> None:
    """Create a per-step cancellation flag for session_id / step_number."""
    flag = _step_flag_path(session_id, step_number, workspace_root)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text(reason, encoding="utf-8")
    logger.info(
        "[cancellation] step flag set for session %s step %d: %s",
        session_id, step_number, reason,
    )


def is_step_cancellation_requested(
    session_id: str,
    step_number: int,
    *,
    workspace_root: Path | None = None,
) -> bool:
    """Return True if a per-step cancellation flag exists."""
    return _step_flag_path(session_id, step_number, workspace_root).exists()


def clear_step_cancellation(
    session_id: str,
    step_number: int,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Remove the per-step cancellation flag (idempotent)."""
    try:
        _step_flag_path(session_id, step_number, workspace_root).unlink(missing_ok=True)
        logger.debug(
            "[cancellation] step flag cleared for session %s step %d", session_id, step_number
        )
    except OSError as exc:
        logger.warning(
            "[cancellation] failed to clear step flag for %s step %d: %s",
            session_id, step_number, exc,
        )
