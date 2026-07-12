"""Shared utilities and constants used across MatCreator modules.

This module centralises commonly duplicated helpers (timestamps, agent-mode
detection, environment-variable patterns) so every subsystem imports from a
single source of truth.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Timestamp helpers (previously duplicated in 4+ modules)
# ---------------------------------------------------------------------------

def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Agent-mode helper (previously duplicated in orchestrator + thinking_agent)
# ---------------------------------------------------------------------------

def get_agent_mode(state: dict) -> str:
    """Return the active agent mode: 'flash', 'bench', or 'normal'.

    Checks ``state['agent_mode']`` first, then falls back to the legacy
    ``state['benchmark_mode']`` boolean flag.
    """
    mode = state.get("agent_mode")
    if mode in ("normal", "bench", "flash"):
        return mode
    return "bench" if state.get("benchmark_mode", False) else "normal"


# ---------------------------------------------------------------------------
# Environment-variable patterns (previously duplicated in 3+ modules)
# ---------------------------------------------------------------------------

USER_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

PROTECTED_USER_ENV_KEYS = frozenset({
    "HOME",
    "PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "MATCREATOR_HOME",
    "MATCREATOR_MODE",
    "MATCREATOR_USER_ID",
})

# Legacy env aliases for backward compatibility.
LEGACY_ENV_ALIASES: dict[str, str] = {
    "LLM_API_KEY": "MINIMAX_API_KEY",
    "LLM_BASE_URL": "MINIMAX_API_BASE",
}


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def json_safe(value: Any) -> Any:
    """Recursively convert *value* to a JSON-serialisable structure."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    import os
    if isinstance(value, os.PathLike):
        return str(value)
    return str(value)
