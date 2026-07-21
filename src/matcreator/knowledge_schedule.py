"""Configuration helpers for completion-based knowledge automation."""

from __future__ import annotations

import logging
import os


def knowledge_frequency(
    env_name: str,
    default: int,
    *,
    logger: logging.Logger | None = None,
) -> int:
    """Return a non-negative completion frequency from the environment."""
    raw = os.environ.get(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        if logger:
            logger.warning("Ignoring invalid %s=%r; using %d", env_name, raw, default)
        return default
    if value < 0:
        if logger:
            logger.warning("Ignoring negative %s=%r; using %d", env_name, raw, default)
        return default
    return value


def is_knowledge_run_due(completion_count: int, frequency: int) -> bool:
    """Return whether a periodic process is due; zero disables it."""
    return frequency > 0 and completion_count % frequency == 0

