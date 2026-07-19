"""Atomic session-state storage helpers for the active execution graph.

SQLite's JSON Merge Patch recursively merges JSON objects.  Storing a graph as
a bare object therefore leaves nodes from an older plan behind when a new plan
replaces it.  A single-item JSON array is an atomic Merge Patch value, so the
whole active graph is replaced while the graph's public shape remains a dict.
"""

from __future__ import annotations

import json
from typing import Any


EXECUTION_GRAPH_STATE_KEY = "execution_graph"


def decode_execution_graph(raw: Any) -> dict | None:
    """Return the public graph dict from current or legacy state storage."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if isinstance(raw, list):
        if len(raw) != 1 or not isinstance(raw[0], dict):
            return None
        return raw[0]
    if isinstance(raw, dict):
        return raw
    return None


def get_execution_graph(state: Any) -> dict | None:
    """Read the active graph, accepting pre-refactor bare-dict sessions."""
    if not hasattr(state, "get"):
        return None
    return decode_execution_graph(state.get(EXECUTION_GRAPH_STATE_KEY))


def set_execution_graph(state: Any, graph: dict) -> dict:
    """Persist *graph* as an atomic JSON Merge Patch value and return it."""
    state[EXECUTION_GRAPH_STATE_KEY] = [graph]
    return graph
