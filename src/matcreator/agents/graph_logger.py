"""Agent execution graph logger.

Writes a JSON file per session to {workspace_root}/agent_graphs/{session_id}.json
tracking the agent node hierarchy and execution status for frontend visualization.

Schema
------
{
  "session_id": "<str>",
  "nodes": {
    "<node_id>": {
      "id": "<str>",
      "type": "orchestrator|planning|execution|tester|step",
      "label": "<str>",
      "status": "idle|running|success|failed|needs_replanning",
      "parent_id": "<str|null>",
      "start_time": "<ISO-8601|null>",
      "end_time": "<ISO-8601|null>",
      "summary": "<str|null>",
      "artifacts": ["<str>", ...],
      "input": {"step_number": int, "action": str, ...} | null,
      "tool_calls": [{"name": str, "args_summary": str, "result_summary": str,
                       "start_time": str, "end_time": str}, ...],
      "state_delta": {"<key>": "<value>", ...}
    }
  },
  "edges": [{"from": "<str>", "to": "<str>"}],
  "updated_at": "<ISO-8601>"
}
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

from ..workspace import ADK_DIR

_session_locks: dict[str, threading.Lock] = {}
_session_locks_mutex = threading.Lock()

NodeStatus = Literal["idle", "running", "success", "failed", "cancelled", "needs_replanning"]
NodeType = Literal["orchestrator", "planning", "execution", "tester", "step"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentGraphLogger:
    """File-backed logger that tracks agent node lifecycle for a single session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        graph_dir = ADK_DIR / "agent_graphs"
        graph_dir.mkdir(parents=True, exist_ok=True)
        self._path = graph_dir / f"{session_id}.json"
        with _session_locks_mutex:
            if session_id not in _session_locks:
                _session_locks[session_id] = threading.Lock()
            self._lock = _session_locks[session_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_node_start(
        self,
        node_id: str,
        node_type: NodeType,
        label: str,
        parent_id: Optional[str] = None,
    ) -> None:
        """Create or overwrite a node with status=running."""
        with self._lock:
            graph = self._read()
            existing = graph["nodes"].get(node_id)
            node = {
                "id": node_id,
                "type": node_type,
                "label": label,
                "status": "running",
                "parent_id": parent_id,
                "start_time": existing["start_time"] if existing else _now(),
                "end_time": None,
                "summary": None,
                "artifacts": [],
                "input": None,
                "tool_calls": [],
                "state_delta": {},
                "conversation": [],
            }
            graph["nodes"][node_id] = node
            if parent_id and parent_id in graph["nodes"]:
                edge = {"from": parent_id, "to": node_id}
                if edge not in graph["edges"]:
                    graph["edges"].append(edge)
            self._write(graph)

    def log_node_complete(
        self,
        node_id: str,
        status: NodeStatus,
        summary: Optional[str] = None,
        artifacts: Optional[List[str]] = None,
    ) -> None:
        """Update an existing node with completion info."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            # A cancellation is terminal for this run. A late completion from
            # an already-cancelled async task must not turn the graph green.
            if node.get("status") == "cancelled" and status != "cancelled":
                return
            node["status"] = status
            node["end_time"] = _now()
            if summary is not None:
                node["summary"] = summary
            if artifacts is not None:
                node["artifacts"] = artifacts
            self._write(graph)

    def count_nodes_of_type(self, node_type: NodeType) -> int:
        """Return how many nodes of the given type already exist (for loop indices)."""
        graph = self._read()
        return sum(1 for n in graph["nodes"].values() if n["type"] == node_type)

    def log_node_input(self, node_id: str, input_data: dict) -> None:
        """Store the structured input that was passed to the sub-agent."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            node["input"] = input_data
            self._write(graph)

    def log_tool_call(self, node_id: str, entry: dict) -> None:
        """Append one tool-call record to the node's tool_calls list."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            node.setdefault("tool_calls", []).append(entry)
            self._write(graph)

    def log_state_delta(self, node_id: str, delta: dict) -> None:
        """Merge delta into the node's accumulated state_delta."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            node.setdefault("state_delta", {}).update(delta)
            self._write(graph)

    def log_conversation_event(self, node_id: str, entry: dict) -> None:
        """Append one conversation turn to the node's conversation list."""
        with self._lock:
            graph = self._read()
            node = graph["nodes"].get(node_id)
            if node is None:
                return
            node.setdefault("conversation", []).append(entry)
            self._write(graph)

    def mark_running_nodes_cancelled(
        self,
        node_types: Optional[list[NodeType]] = None,
        summary: str = "Cancelled by user",
    ) -> None:
        """Pre-emptively mark running nodes as cancelled.

        Called eagerly on session cancel so the graph reflects cancellation
        before the step executor polls its flag.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            for node in graph["nodes"].values():
                if node.get("status") != "running":
                    continue
                if node_types is not None and node.get("type") not in node_types:
                    continue
                node["status"] = "cancelled"
                node["end_time"] = now
                node["summary"] = summary
            self._write(graph)

    def cancel_step_node_by_number(self, step_number: int, summary: str = "Cancelled by user") -> bool:
        """Find the running step node with input.step_number==step_number and mark it cancelled.

        Returns True if a node was found and updated.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            for node in graph["nodes"].values():
                if (
                    node.get("type") == "step"
                    and node.get("status") == "running"
                    and (node.get("input") or {}).get("step_number") == step_number
                ):
                    node["status"] = "cancelled"
                    node["end_time"] = now
                    node["summary"] = summary
                    self._write(graph)
                    return True
        return False

    def cancel_step_node_by_id(self, node_id: str, summary: str = "Cancelled by user") -> bool:
        """Find the running step node with input.node_id==node_id and mark it cancelled.

        Used for DAG-mode cancellation where nodes are identified by string node_id.
        Returns True if a matching node was found and updated.
        """
        with self._lock:
            graph = self._read()
            now = _now()
            for node in graph["nodes"].values():
                if (
                    node.get("type") == "step"
                    and node.get("status") == "running"
                    and (node.get("input") or {}).get("node_id") == node_id
                ):
                    node["status"] = "cancelled"
                    node["end_time"] = now
                    node["summary"] = summary
                    self._write(graph)
                    return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"session_id": self.session_id, "nodes": {}, "edges": [], "updated_at": _now()}

    def _write(self, graph: dict) -> None:
        graph["updated_at"] = _now()
        self._path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
