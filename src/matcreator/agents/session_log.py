"""Unified session logging helpers.

The ADK session database persists session state, while child step executors run
with isolated in-memory sessions.  These helpers mirror child execution details
back into the parent session state so one session record contains the full run.
"""

from __future__ import annotations

import os
import hashlib
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from google.adk.tools.tool_context import ToolContext

SESSION_LOG_KEY = "session_log"
SESSION_ARTIFACTS_KEY = "session_artifacts"
SESSION_LOG_EVENT_PREFIX = "session_log_event__"
SESSION_ARTIFACT_PREFIX = "session_artifact__"
_SESSION_LOG_STATE_KEYS = {SESSION_LOG_KEY, SESSION_ARTIFACTS_KEY}
_SESSION_LOG_STATE_PREFIXES = (SESSION_LOG_EVENT_PREFIX, SESSION_ARTIFACT_PREFIX)

_ARTIFACT_PATH_KEYS = {"artifact_path", "plot_path", "structure_path"}
_ARTIFACT_LIST_KEYS = {"artifacts", "artifact_paths", "plot_paths", "structure_paths"}

_session_locks: dict[str, threading.Lock] = {}
_session_locks_mutex = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_id(tool_context: ToolContext) -> str:
    return (
        tool_context.state.get("session_id")
        or tool_context._invocation_context.session.id
        or "default"
    )


def _lock_for_session(session_id: str) -> threading.Lock:
    with _session_locks_mutex:
        if session_id not in _session_locks:
            _session_locks[session_id] = threading.Lock()
        return _session_locks[session_id]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, os.PathLike):
        return str(value)
    return str(value)


def normalize_artifact_paths(paths: Iterable[Any]) -> list[str]:
    """Return unique artifact paths as absolute strings."""
    normalized: list[str] = []
    for value in paths:
        if not isinstance(value, (str, os.PathLike)):
            continue
        raw_path = str(value).strip()
        if not raw_path:
            continue
        try:
            full_path = str(Path(raw_path).expanduser().resolve())
        except OSError:
            full_path = raw_path
        if full_path not in normalized:
            normalized.append(full_path)
    return normalized


def collect_artifact_paths(payload: Any) -> list[str]:
    """Collect artifact paths from nested tool responses or result payloads."""
    paths: list[Any] = []

    def visit(value: Any, key: str | None = None) -> None:
        if key in _ARTIFACT_PATH_KEYS:
            paths.append(value)
            return
        if key in _ARTIFACT_LIST_KEYS and isinstance(value, list):
            paths.extend(value)
            return
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key))
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return normalize_artifact_paths(paths)


def is_session_log_state_key(key: str) -> bool:
    """Return True for debug-log state keys that should not enter agent context."""
    return key in _SESSION_LOG_STATE_KEYS or key.startswith(_SESSION_LOG_STATE_PREFIXES)


def session_log_entries_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Return de-duplicated session-log entries from persisted session state."""
    entries = state.get(SESSION_LOG_KEY) or []
    if not isinstance(entries, list):
        entries = []
    keyed_entries = [
        value for key, value in state.items()
        if isinstance(key, str)
        and key.startswith(SESSION_LOG_EVENT_PREFIX)
        and isinstance(value, dict)
    ]

    by_event_id: dict[str, dict[str, Any]] = {}
    anonymous_entries: list[dict[str, Any]] = []
    for entry in [*entries, *keyed_entries]:
        if not isinstance(entry, dict):
            continue
        event_id = entry.get("event_id")
        if isinstance(event_id, str) and event_id:
            by_event_id[event_id] = entry
        else:
            anonymous_entries.append(entry)

    merged = [*anonymous_entries, *by_event_id.values()]
    merged.sort(key=lambda entry: str(entry.get("timestamp", "")))
    return merged


def session_artifacts_from_state(state: dict[str, Any]) -> list[str]:
    """Return de-duplicated absolute artifact paths from persisted session state."""
    artifacts = state.get(SESSION_ARTIFACTS_KEY) or []
    if not isinstance(artifacts, list):
        artifacts = []
    return normalize_artifact_paths([
        *artifacts,
        *(
            value for key, value in state.items()
            if isinstance(key, str) and key.startswith(SESSION_ARTIFACT_PREFIX)
        ),
    ])


def strip_conversation_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Return one session-log entry without bulky conversation turns."""
    clean_entry = dict(entry)
    events = clean_entry.get("events")
    if isinstance(events, dict) and "conversation" in events:
        clean_events = dict(events)
        clean_events.pop("conversation", None)
        clean_entry["events"] = clean_events
    return clean_entry


def session_log_graph_from_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a coarse graph view from full session-log entries."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []

    for entry in entries:
        step_id = entry.get("step_id") or entry.get("node_id") or entry.get("event_id")
        if not isinstance(step_id, str) or not step_id:
            continue

        node = nodes.setdefault(step_id, {
            "id": step_id,
            "node_id": entry.get("node_id"),
            "parent_id": entry.get("parent_id"),
            "step_number": entry.get("step_number"),
            "action": entry.get("action"),
            "workspace_dir": entry.get("workspace_dir"),
            "suggested_skills": entry.get("suggested_skills") or [],
            "status": "running",
            "start_time": None,
            "end_time": None,
            "summary": None,
            "event_ids": [],
            "tool_call_count": 0,
            "artifact_count": 0,
        })

        for key in ("node_id", "parent_id", "step_number", "action", "workspace_dir"):
            if node.get(key) is None and entry.get(key) is not None:
                node[key] = entry.get(key)
        if not node.get("suggested_skills") and entry.get("suggested_skills"):
            node["suggested_skills"] = entry.get("suggested_skills")

        event_id = entry.get("event_id")
        if isinstance(event_id, str) and event_id not in node["event_ids"]:
            node["event_ids"].append(event_id)

        timestamp = entry.get("timestamp")
        if entry.get("kind") == "step_start" and node.get("start_time") is None:
            node["start_time"] = timestamp
        if entry.get("kind") == "step_complete":
            node["end_time"] = timestamp
            node["status"] = entry.get("status") or node["status"]
            result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            node["summary"] = (
                result.get("concise_summary")
                or result.get("replan_reason")
                or entry.get("message")
                or entry.get("replan_reason")
            )

        events = entry.get("events") if isinstance(entry.get("events"), dict) else {}
        tool_calls = events.get("tool_calls") if isinstance(events, dict) else []
        if isinstance(tool_calls, list):
            node["tool_call_count"] += len(tool_calls)

        artifacts = entry.get("artifacts") if isinstance(entry.get("artifacts"), list) else []
        node["artifact_count"] += len(artifacts)

        parent_id = entry.get("parent_id")
        if isinstance(parent_id, str) and parent_id:
            edge = {"from": parent_id, "to": step_id}
            if edge not in edges:
                edges.append(edge)

    return {"nodes": list(nodes.values()), "edges": edges}


def build_session_log_export(
    session_id: str,
    state: dict[str, Any],
    *,
    include_state: bool = False,
) -> dict[str, Any]:
    """Build the full user/debug export for one session."""
    entries = session_log_entries_from_state(state)
    artifacts = session_artifacts_from_state(state)
    payload = {
        "schema_version": 1,
        "session_id": session_id,
        "event_count": len(entries),
        "executor_count": len(session_log_graph_from_entries(entries)["nodes"]),
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "graph": session_log_graph_from_entries(entries),
        "events": entries,
    }
    if include_state:
        payload["state"] = state
    return payload


def append_session_log_entry(
    tool_context: ToolContext,
    entry: dict[str, Any],
    *,
    artifacts: Iterable[Any] = (),
) -> dict[str, Any]:
    """Append one structured record to the current ADK session state."""
    sid = _session_id(tool_context)
    event_id = uuid.uuid4().hex
    record = {
        "event_id": event_id,
        "timestamp": _now(),
        "session_id": sid,
        **_json_safe(entry),
    }

    artifact_paths = normalize_artifact_paths([
        *collect_artifact_paths(record),
        *list(artifacts),
    ])
    if artifact_paths:
        record["artifacts"] = artifact_paths

    with _lock_for_session(sid):
        tool_context.state[f"{SESSION_LOG_EVENT_PREFIX}{event_id}"] = record

        current_log = tool_context.state.get(SESSION_LOG_KEY) or []
        if not isinstance(current_log, list):
            current_log = []
        tool_context.state[SESSION_LOG_KEY] = [*current_log, record]

        if artifact_paths:
            current_artifacts = tool_context.state.get(SESSION_ARTIFACTS_KEY) or []
            if not isinstance(current_artifacts, list):
                current_artifacts = []
            merged = normalize_artifact_paths([*current_artifacts, *artifact_paths])
            tool_context.state[SESSION_ARTIFACTS_KEY] = merged
            for artifact_path in artifact_paths:
                digest = hashlib.sha1(artifact_path.encode("utf-8")).hexdigest()
                tool_context.state[f"{SESSION_ARTIFACT_PREFIX}{digest}"] = artifact_path

    return record