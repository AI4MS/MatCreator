"""Shared pytest fixtures and helpers for the MatCreator test suite."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Canonical list of all configuration-related environment variables.
# This is the single source of truth — when a new config env var is added to
# src/matcreator/ports.py, add it here and all config-cleanup tests will
# automatically clear it.
# ---------------------------------------------------------------------------

ALL_CONFIG_ENV_VARS = (
    "MATCREATOR_ADK_PORT",
    "MATCREATOR_WEB_PORT",
    "MATCREATOR_FRONTEND_PORT",
    "MATCREATOR_SERVER_PROXY_PORT",
    "MATCREATOR_WORKER_BASE_PORT",
    "ADK_LOCAL_PORT",
    "MATCREATOR_ADK_HOST",
    "MATCREATOR_WEB_HOST",
    "MATCREATOR_FRONTEND_HOST",
    "MATCREATOR_HOME",
    "MATCREATOR_MODULE_SKILLS_ROOT",
    "MATCREATOR_CONFIG_PATH",
)


def clear_port_env_vars(monkeypatch) -> None:
    """Remove all configuration-related environment variables for a clean test slate."""
    for var in ALL_CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
