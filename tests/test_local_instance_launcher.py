"""Local launchers must reserve identity and endpoints instead of reattaching."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace

import click
from click.testing import CliRunner
import pytest

from matcreator.scripts import start_agent as launcher


def test_same_home_adk_lock_rejects_second_writer_and_releases(tmp_path):
    with launcher._local_adk_lock(tmp_path):
        with pytest.raises(click.ClickException, match="already owns"):
            with launcher._local_adk_lock(tmp_path):
                pytest.fail("The second writer must never acquire the home")
        # Another independent home remains usable.
        with launcher._local_adk_lock(tmp_path / "other"):
            pass
    with launcher._local_adk_lock(tmp_path):
        pass


def test_occupied_adk_port_is_not_reused():
    first = launcher._reserve_adk_socket("127.0.0.1", 0)
    try:
        port = first.getsockname()[1]
        with pytest.raises(click.ClickException, match="existing service was not reused"):
            launcher._reserve_adk_socket("127.0.0.1", port)
    finally:
        first.close()


def test_access_link_is_explicit_fragment_and_uses_requested_frontend(monkeypatch, tmp_path):
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setattr(launcher, "_MATCREATOR_HOME", tmp_path)
    secret = "synthetic-test-token"
    monkeypatch.setitem(sys.modules, "matcreator.local_access", SimpleNamespace(
        ensure_local_access=lambda home: SimpleNamespace(token=secret, browser_host="mc-synthetic.localhost"),
    ))
    result = CliRunner().invoke(launcher.main, ["access", "--url", "http://127.0.0.1:25173/"])
    assert result.exit_code == 0
    assert result.output.strip() == f"http://mc-synthetic.localhost:25173/#token={secret}"


@pytest.mark.parametrize("url", ["file:///tmp/page", "https://user:password@example.com/", "https://example.com/", "http://mc-other-instance.localhost/"])
def test_access_link_rejects_non_web_or_embedded_credentials(monkeypatch, tmp_path, url):
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setattr(launcher, "_MATCREATOR_HOME", tmp_path)
    monkeypatch.setitem(sys.modules, "matcreator.local_access", SimpleNamespace(
        ensure_local_access=lambda home: SimpleNamespace(token="synthetic-test-token", browser_host="mc-synthetic.localhost"),
    ))
    result = CliRunner().invoke(launcher.main, ["access", "--url", url])
    assert result.exit_code != 0
    assert "synthetic-test-token" not in result.output


@pytest.mark.parametrize("url", ["http://localhost:25173/path?q=1", "http://[::1]:25173/path?q=1", "http://mc-synthetic.localhost:25173/path?q=1"])
def test_access_link_preserves_port_path_and_query_with_instance_host(monkeypatch, tmp_path, url):
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setattr(launcher, "_MATCREATOR_HOME", tmp_path)
    monkeypatch.setitem(sys.modules, "matcreator.local_access", SimpleNamespace(
        ensure_local_access=lambda home: SimpleNamespace(token="synthetic-test-token", browser_host="mc-synthetic.localhost"),
    ))
    result = CliRunner().invoke(launcher.main, ["access", "--url", url])
    assert result.exit_code == 0
    assert result.output.strip() == "http://mc-synthetic.localhost:25173/path?q=1#token=synthetic-test-token"


def test_server_mode_does_not_offer_local_access_links(monkeypatch):
    monkeypatch.setenv("MATCREATOR_MODE", "server")
    result = CliRunner().invoke(launcher.main, ["access"])
    assert result.exit_code != 0
    assert "private local instance" in result.output


def test_adk_has_auth_middleware_before_serving_and_logs_no_secret(monkeypatch, tmp_path, capsys):
    import uvicorn
    import google.adk.cli.fast_api

    monkeypatch.setenv("MATCREATOR_MODE", "local")
    monkeypatch.setattr(launcher, "_MATCREATOR_HOME", tmp_path)
    monkeypatch.setattr(launcher, "_DEFAULT_ADK_DIR", tmp_path / ".adk")
    monkeypatch.setattr(launcher, "_apply_harness_config_env", lambda: None)
    monkeypatch.setattr(launcher, "_make_agent_loader", lambda: object())
    app = object()
    calls = []
    secret = "synthetic-secret-not-for-startup-log"
    access = SimpleNamespace(instance_id="synthetic-instance", token_path=tmp_path / "token", token=secret)
    monkeypatch.setitem(sys.modules, "matcreator.local_access", SimpleNamespace(
        ensure_local_access=lambda home: access,
        install_local_access=lambda supplied_app, **kw: calls.append((supplied_app, kw)),
    ))
    monkeypatch.setattr(google.adk.cli.fast_api, "get_fast_api_app", lambda **kw: app)

    class FakeServer:
        def __init__(self, config):
            assert calls == [(app, {"home": tmp_path, "audience": "adk"})]

        def run(self, sockets):
            assert len(sockets) == 1 and sockets[0].getsockname()[1] > 0

    monkeypatch.setattr(uvicorn, "Config", lambda *a, **kw: object())
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    launcher._start_adk_server("127.0.0.1", 0, "info", False)
    output = capsys.readouterr().out
    assert "synthetic-instance" in output
    assert str(tmp_path) in output
    assert secret not in output


def test_workspace_default_is_exported_as_resolved_path(monkeypatch, tmp_path):
    monkeypatch.delenv("MATCLAW_WORKSPACE", raising=False)
    monkeypatch.setattr(launcher, "_default_workspace", lambda: tmp_path / "workspace")
    workspace = launcher._setup_workspace(None)
    assert workspace == (tmp_path / "workspace").resolve()
    assert os.environ["MATCLAW_WORKSPACE"] == str(workspace)
