"""Unified session-log reading tool for the thinking_agent."""
from __future__ import annotations

import logging
from typing import Optional

from google.adk.tools.tool_context import ToolContext

from ..session_log import (
    normalize_artifact_paths,
    session_artifacts_from_state,
    session_log_entries_from_state,
    session_log_graph_from_entries,
    strip_conversation_from_entry,
)

logger = logging.getLogger(__name__)

_SESSION_LOG_VIEWS = {"overview", "detail", "artifacts"}


def _state_dict(tool_context: ToolContext) -> dict:
    state = tool_context.state
    if hasattr(state, "to_dict"):
        return state.to_dict()
    try:
        return dict(state)
    except (TypeError, ValueError):
        return {}


def read_session_log(
    tool_context: ToolContext,
    view: str = "overview",
    node_id: Optional[str] = None,
    step_id: Optional[str] = None,
    event_id: Optional[str] = None,
    include_conversation: bool = False,
    last_n_events: Optional[int] = None,
) -> dict:
    """Read the unified session log hierarchically.

    Default ``view='overview'`` returns a coarse graph of executor nodes with
    statuses, event IDs, and counts only. Use ``view='detail'`` with exactly one
    selector (``node_id``, ``step_id``, or ``event_id``) to inspect one executor.
    Use ``view='artifacts'`` to list all recorded artifact paths.
    """
    sid = tool_context._invocation_context.session.id
    requested_view = (view or "overview").strip().lower()
    if requested_view not in _SESSION_LOG_VIEWS:
        return {
            "status": "error",
            "session_id": sid,
            "message": f"Unknown view '{view}'. Choose one of: {sorted(_SESSION_LOG_VIEWS)}.",
        }

    state = _state_dict(tool_context)
    entries = session_log_entries_from_state(state)
    artifacts = session_artifacts_from_state(state)

    if requested_view == "overview":
        graph = session_log_graph_from_entries(entries)
        return {
            "status": "ok",
            "view": "overview",
            "session_id": sid,
            "event_count": len(entries),
            "executor_count": len(graph["nodes"]),
            "artifact_count": len(artifacts),
            "graph": graph,
            "next_step": (
                "Call read_session_log(view='detail', step_id='<id>') or "
                "read_session_log(view='detail', node_id='<node_id>') for one executor."
            ),
        }

    if requested_view == "artifacts":
        return {
            "status": "ok",
            "view": "artifacts",
            "session_id": sid,
            "artifact_count": len(artifacts),
            "artifacts": artifacts,
        }

    selectors = {
        "event_id": event_id,
        "step_id": step_id,
        "node_id": node_id,
    }
    active_selectors = {key: value for key, value in selectors.items() if value}
    if len(active_selectors) != 1:
        return {
            "status": "error",
            "view": "detail",
            "session_id": sid,
            "message": "Detail view requires exactly one selector: event_id, step_id, or node_id.",
            "overview_hint": "Call read_session_log(view='overview') first to discover executor IDs.",
        }

    selector_name, selector_value = next(iter(active_selectors.items()))
    if selector_name == "event_id":
        entries = [entry for entry in entries if entry.get("event_id") == selector_value]
    elif selector_name == "step_id":
        entries = [entry for entry in entries if entry.get("step_id") == selector_value]
    else:
        entries = [
            entry for entry in entries
            if entry.get("node_id") == selector_value or entry.get("step_id") == selector_value
        ]

    if last_n_events is not None and int(last_n_events) > 0:
        entries = entries[-int(last_n_events):]

    if not include_conversation:
        entries = [strip_conversation_from_entry(entry) for entry in entries]

    selected_artifacts = normalize_artifact_paths(
        artifact for entry in entries for artifact in (entry.get("artifacts") or [])
    )

    return {
        "status": "ok",
        "view": "detail",
        "session_id": sid,
        "selector": {selector_name: selector_value},
        "event_count": len(entries),
        "artifacts": selected_artifacts,
        "graph": session_log_graph_from_entries(entries),
        "events": entries,
    }
