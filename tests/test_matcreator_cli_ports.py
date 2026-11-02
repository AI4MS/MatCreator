"""Tests for CLI config integration with port configuration.

Uses click.testing.CliRunner and monkeypatching to redirect the config path
to a temporary directory, avoiding any real home-directory reads or writes.
"""

from __future__ import annotations

import os
import importlib
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from src.matcreator.scripts import start_agent

# IMPORTANT: matcreator.config must be imported WITHOUT the "src." prefix.
# Inside start_agent.py the import is ``from matcreator.config import ...``,
# which resolves to a *different* module object than ``src.matcreator.config``.
# Monkeypatching the wrong object means the CLI commands never see the override.
#
# Ensure the project root is on sys.path so the bare ``import matcreator.config``
# works regardless of how the tests are invoked (e.g. ``python -m pytest`` vs
# ``pytest`` from different directories).
_PROJ_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

import matcreator.config as matconfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _redirect_config_home(monkeypatch, tmp_path: Path) -> None:
    """Point both config.py and ports.py at *tmp_path* for the config file."""
    # Other integration tests deliberately reload this module for each
    # instance. Resolve the current object, not a stale collection-time import.
    global matconfig
    matconfig = importlib.import_module("matcreator.config")
    monkeypatch.setenv("MATCREATOR_HOME", str(tmp_path))
    monkeypatch.setattr(matconfig, "_MATCREATOR_HOME", tmp_path)
    monkeypatch.setattr(matconfig, "_CONFIG_PATH", tmp_path / "config.yaml")


def _read_raw_yaml() -> dict:
    """Return the raw YAML content from the monkeypatched config path, or {}."""
    config_file = matconfig._CONFIG_PATH
    if not config_file.exists():
        return {}
    return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Port setting and reading
# ---------------------------------------------------------------------------


def test_cli_config_set_and_get_port(monkeypatch, tmp_path: Path) -> None:
    """Set ports.adk=8100 via CLI, then read it back."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(start_agent.main, ["config", "set", "ports.adk=8100"])
    assert result.exit_code == 0

    result = runner.invoke(start_agent.main, ["config", "get", "ports.adk"])
    assert result.exit_code == 0
    assert "8100" in result.output


def test_cli_config_show_contains_ports(monkeypatch, tmp_path: Path) -> None:
    """Set a port, then 'config show' should list it."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    runner.invoke(start_agent.main, ["config", "set", "ports.adk=8100"])
    result = runner.invoke(start_agent.main, ["config", "show"])
    assert result.exit_code == 0
    # The show command prints sections; ports section should appear
    assert "8100" in result.output or "ports" in result.output


def test_cli_config_set_web_port(monkeypatch, tmp_path: Path) -> None:
    """Set ports.web=8101 via CLI and verify it is stored."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(start_agent.main, ["config", "set", "ports.web=8101"])
    assert result.exit_code == 0

    result = runner.invoke(start_agent.main, ["config", "get", "ports.web"])
    assert result.exit_code == 0
    assert "8101" in result.output


def test_cli_config_set_frontend_port(monkeypatch, tmp_path: Path) -> None:
    """Set ports.frontend=5174 via CLI and verify."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(start_agent.main, ["config", "set", "ports.frontend=5174"])
    assert result.exit_code == 0

    result = runner.invoke(start_agent.main, ["config", "get", "ports.frontend"])
    assert result.exit_code == 0
    assert "5174" in result.output


def test_cli_config_set_server_proxy_port(monkeypatch, tmp_path: Path) -> None:
    """Set ports.server_proxy=8080 via CLI and verify."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        start_agent.main, ["config", "set", "ports.server_proxy=8080"]
    )
    assert result.exit_code == 0

    result = runner.invoke(start_agent.main, ["config", "get", "ports.server_proxy"])
    assert result.exit_code == 0
    assert "8080" in result.output


def test_cli_config_set_worker_base_port(monkeypatch, tmp_path: Path) -> None:
    """Set ports.worker_base=9101 via CLI and verify."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        start_agent.main, ["config", "set", "ports.worker_base=9101"]
    )
    assert result.exit_code == 0

    result = runner.invoke(start_agent.main, ["config", "get", "ports.worker_base"])
    assert result.exit_code == 0
    assert "9101" in result.output




def test_cli_config_persists_across_reads(monkeypatch, tmp_path: Path) -> None:
    """Set a value, then verify it persists across multiple config get calls."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    runner.invoke(start_agent.main, ["config", "set", "ports.adk=8100"])
    result1 = runner.invoke(start_agent.main, ["config", "get", "ports.adk"])
    result2 = runner.invoke(start_agent.main, ["config", "get", "ports.adk"])
    assert result1.exit_code == 0
    assert result2.exit_code == 0
    assert "8100" in result1.output
    assert "8100" in result2.output


def test_cli_config_persists_in_raw_yaml(monkeypatch, tmp_path: Path) -> None:
    """Set a port value and verify the YAML file has the correct structure."""
    _redirect_config_home(monkeypatch, tmp_path)
    runner = CliRunner()

    runner.invoke(start_agent.main, ["config", "set", "ports.adk=8100"])

    raw = _read_raw_yaml()
    assert "ports" in raw, f"Expected 'ports' key in YAML, got: {raw}"
    assert raw["ports"]["adk"] == "8100"
