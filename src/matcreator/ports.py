"""Port configuration layer for MatCreator.

Resolves port values with precedence:
    environment variables > ~/.matcreator/config.yaml > defaults

All functions are pure and side-effect-free at import time — values are resolved when
called, making the module easy to test without starting any services.

Config file integration
-----------------------
The config file is read from ``MATCREATOR_CONFIG_PATH`` when set. Otherwise it
is read from ``~/.matcreator/config.yaml`` by default. Set ``MATCREATOR_HOME``
to point at a different directory (the file is assumed to be named
``config.yaml`` inside that directory).

The ``ports`` section of the YAML file is read:

.. code-block:: yaml

    ports:
      adk: 8100
      web: 8101
      frontend: 5174
      server_proxy: 8080
      worker_base: 9101
      adk_host: 0.0.0.0
      web_host: 0.0.0.0
      frontend_host: 0.0.0.0

Legacy alias
------------
``ADK_LOCAL_PORT`` is recognised as a legacy alias for ``MATCREATOR_ADK_PORT``.
When both are present, ``MATCREATOR_ADK_PORT`` takes precedence.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Default port values
# ---------------------------------------------------------------------------

_DEFAULTS: dict[str, int | str] = {
    "adk": 8000,
    "web": 8001,
    "frontend": 5173,
    "server_proxy": 80,
    "worker_base": 9001,
    "adk_host": "127.0.0.1",
    "web_host": "127.0.0.1",
    "frontend_host": "127.0.0.1",
}

# ---------------------------------------------------------------------------
# Environment variable name mapping (port names -> env var names)
# ---------------------------------------------------------------------------

_ENV_VAR_MAP: dict[str, str] = {
    "adk": "MATCREATOR_ADK_PORT",
    "web": "MATCREATOR_WEB_PORT",
    "frontend": "MATCREATOR_FRONTEND_PORT",
    "server_proxy": "MATCREATOR_SERVER_PROXY_PORT",
    "worker_base": "MATCREATOR_WORKER_BASE_PORT",
    "adk_host": "MATCREATOR_ADK_HOST",
    "web_host": "MATCREATOR_WEB_HOST",
    "frontend_host": "MATCREATOR_FRONTEND_HOST",
}

# Legacy aliases: primary env var name -> legacy env var name
_LEGACY_ENV_MAP: dict[str, str] = {
    "MATCREATOR_ADK_PORT": "ADK_LOCAL_PORT",
}

# ---------------------------------------------------------------------------
# Config file helpers
# ---------------------------------------------------------------------------

def _get_config_path() -> str:
    """Return the path to the YAML config file.

    Uses ``MATCREATOR_CONFIG_PATH`` when set. Otherwise uses
    ``MATCREATOR_HOME/config.yaml`` if ``MATCREATOR_HOME`` is set and non-empty,
    then defaults to ``~/.matcreator/config.yaml``.
    """
    config_path = os.environ.get("MATCREATOR_CONFIG_PATH", "").strip()
    if config_path:
        return os.path.expanduser(config_path)
    matcreator_home = os.environ.get("MATCREATOR_HOME", "").strip()
    if matcreator_home:
        return os.path.join(matcreator_home, "config.yaml")
    return os.path.expanduser("~/.matcreator/config.yaml")


def _read_yaml_config() -> dict[str, Any]:
    """Read and parse the YAML config file.

    Returns an empty dict if the file does not exist or cannot be parsed.
    """
    config_path = _get_config_path()
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (FileNotFoundError, yaml.YAMLError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _get_config_file_value(key: str) -> Any:
    """Return a value from the ``ports`` section of the config file.

    *key* may be a dotted path such as ``"section.field"`` to reach nested dicts.
    Returns ``None`` when the key is absent.
    """
    cfg = _read_yaml_config()
    ports = cfg.get("ports")
    if not isinstance(ports, dict):
        return None

    parts = key.split(".", 1)
    if len(parts) == 1:
        return ports.get(key)
    else:
        section = ports.get(parts[0])
        if isinstance(section, dict):
            return section.get(parts[1])
        return None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_port(value: Any, name: str) -> int:
    """Validate that *value* is a valid TCP port number and return it as an int.

    Args:
        value: The raw value to validate (may be int, str, or other).
        name: Human-readable name used in error messages.

    Returns:
        The validated port number as an :class:`int`.

    Raises:
        ValueError: If the value is not a valid port (non-integer, < 1, or > 65535).
    """
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid port for {name!r}: {value!r}. "
            f"Port must be an integer between 1 and 65535."
        )
    if port < 1 or port > 65535:
        raise ValueError(
            f"Invalid port for {name!r}: {value!r}. "
            f"Port must be an integer between 1 and 65535."
        )
    return port


def _validate_host(value: str, name: str) -> str:
    """Validate that *value* is a non-empty host string with no illegal characters.

    This is a lightweight sanity check (not a full hostname/IP parser).  It
    catches common misconfigurations such as empty strings, whitespace, or
    shell metacharacters that would cause cryptic failures in socket binding.

    Args:
        value: The raw host value to validate.
        name: Human-readable name used in error messages.

    Returns:
        The trimmed host string.

    Raises:
        ValueError: If the value is empty or contains illegal characters.
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(
            f"Host for {name!r} must not be empty."
        )
    illegal = (' ', '\t', '\n', '\r', '$', '`', ';', '&', '|', '<', '>')
    for char in illegal:
        if char in cleaned:
            raise ValueError(
                f"Host for {name!r} contains illegal character {char!r}: {cleaned!r}"
            )
    return cleaned


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------

def _resolve_env_var(env_name: str) -> str | None:
    """Return the value of *env_name* from the environment, checking legacy aliases.

    If the primary env var is set, its value is returned directly.  Otherwise, if a
    legacy alias exists and is set, the legacy value is returned.
    """
    value = os.environ.get(env_name)
    if value is not None:
        return value
    legacy = _LEGACY_ENV_MAP.get(env_name)
    if legacy is not None:
        return os.environ.get(legacy)
    return None


def _resolve_port_value(name: str, config_file_key: str) -> int:
    """Resolve a single port value using env > config file > default.

    Args:
        name: Internal key name (e.g. ``"adk"``, ``"web"``).
        config_file_key: Dotted key within the ``ports`` section of config.yaml
            (e.g. ``"adk"``).

    Returns:
        The resolved port number as an :class:`int`.
    """
    # 1. Environment variable
    env_name = _ENV_VAR_MAP.get(name)
    if env_name:
        env_value = _resolve_env_var(env_name)
        if env_value is not None:
            return _validate_port(env_value, name)

    # 2. Config file
    config_value = _get_config_file_value(config_file_key)
    if config_value is not None:
        return _validate_port(config_value, name)

    # 3. Default
    default = _DEFAULTS.get(name)
    if default is not None:
        return _validate_port(default, name)

    raise ValueError(f"No default value defined for port {name!r}.")


def _resolve_string_value(name: str, config_file_key: str) -> str:
    """Resolve a string config value using env > config > default.

    For host-like values the result is validated by :func:`_validate_host`.
    """
    # 1. Environment variable
    env_name = _ENV_VAR_MAP.get(name)
    if env_name:
        env_value = _resolve_env_var(env_name)
        if env_value is not None:
            return _validate_host(str(env_value), name)

    # 2. Config file
    config_value = _get_config_file_value(config_file_key)
    if config_value is not None:
        return _validate_host(str(config_value), name)

    # 3. Default
    default = _DEFAULTS.get(name)
    fallback = str(default) if default is not None else "localhost"
    return _validate_host(fallback, name)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortsConfig:
    """Immutable snapshot of all resolved port configuration values.

    Integer port fields are validated to be in the range 1–65535.
    Host string fields (``adk_host``, ``web_host``, ``frontend_host``)
    are validated to be non-empty and free of illegal characters.
    """

    adk: int
    """ADK API server port (default: 8000)."""

    web: int
    """FastAPI web / control-plane port (default: 8001)."""

    frontend: int
    """Vite frontend dev-server port (default: 5173)."""

    server_proxy: int
    """Nginx server-proxy port (default: 80)."""

    worker_base: int
    """Worker base port for host-port allocation (default: 9001)."""

    adk_host: str
    """Binding host for the ADK API server (default: 127.0.0.1)."""

    web_host: str
    """Binding host for the FastAPI web / control-plane server (default: 127.0.0.1)."""

    frontend_host: str
    """Binding host for the Vite frontend dev-server (default: 127.0.0.1)."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_ports_config() -> PortsConfig:
    """Resolve all port values and return a frozen :class:`PortsConfig`.

    Precedence: environment variables > config.yaml > defaults.

    Every integer port is validated to be in the range 1–65535.
    """
    return PortsConfig(
        adk=_resolve_port_value("adk", "adk"),
        web=_resolve_port_value("web", "web"),
        frontend=_resolve_port_value("frontend", "frontend"),
        server_proxy=_resolve_port_value("server_proxy", "server_proxy"),
        worker_base=_resolve_port_value("worker_base", "worker_base"),
        adk_host=_resolve_string_value("adk_host", "adk_host"),
        web_host=_resolve_string_value("web_host", "web_host"),
        frontend_host=_resolve_string_value("frontend_host", "frontend_host"),
    )


def get_adk_port() -> int:
    """Return the resolved ADK API server port."""
    return _resolve_port_value("adk", "adk")


def get_web_port() -> int:
    """Return the resolved web (FastAPI) server port."""
    return _resolve_port_value("web", "web")


def get_frontend_port() -> int:
    """Return the resolved frontend (Vite) dev-server port."""
    return _resolve_port_value("frontend", "frontend")


def get_server_proxy_port() -> int:
    """Return the resolved server proxy (nginx) port."""
    return _resolve_port_value("server_proxy", "server_proxy")


def get_worker_base_port() -> int:
    """Return the resolved worker base port."""
    return _resolve_port_value("worker_base", "worker_base")


def get_adk_host() -> str:
    """Return the resolved binding host for the ADK API server."""
    return _resolve_string_value("adk_host", "adk_host")


def get_web_host() -> str:
    """Return the resolved binding host for the FastAPI web server."""
    return _resolve_string_value("web_host", "web_host")


def get_frontend_host() -> str:
    """Return the resolved binding host for the Vite frontend dev-server."""
    return _resolve_string_value("frontend_host", "frontend_host")


def get_local_adk_command(host: str | None = None) -> list[str]:
    """Return the command list for starting the ADK API server locally.

    Args:
        host: The binding host address.  When ``None``, resolved via
            :func:`get_adk_host` (env > config > default).

    Returns:
        A list of command-line tokens, e.g.
        ``["matcreator", "api-server", "--host", "127.0.0.1", "--port", "8000"]``.
    """
    port = get_adk_port()
    if host is None:
        host = get_adk_host()
    return ["matcreator", "api-server", "--host", host, "--port", str(port)]
