from __future__ import annotations

import subprocess

import pytest

from matcreator.control_plane.providers import registry
from matcreator.control_plane.providers._bohr_cli import BohrCLIError, run_bohr_json
from matcreator.control_plane.providers.base import RemoteJobAdapter, RemoteJobStatus


class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_run_bohr_json_raises_on_missing_binary(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BohrCLIError, match="not installed"):
        run_bohr_json(["job", "list"])


def test_run_bohr_json_raises_on_timeout(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BohrCLIError, match="timed out"):
        run_bohr_json(["job", "list"], timeout=1)


def test_run_bohr_json_raises_on_empty_output(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(stdout="", stderr="permission denied", returncode=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BohrCLIError, match="permission denied"):
        run_bohr_json(["job", "list"])


def test_run_bohr_json_raises_on_non_json_output(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(stdout="not json")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(BohrCLIError, match="non-JSON"):
        run_bohr_json(["job", "list"])


def test_run_bohr_json_returns_data_on_success(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(stdout='{"ok": true, "data": {"a": 1}}')

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert run_bohr_json(["job", "list"]) == {"a": 1}


class _DummyAdapter(RemoteJobAdapter):
    provider = "dummy"

    def create(self, spec):
        return "id-1"

    def status(self, external_id):
        return RemoteJobStatus(normalized_status=None)

    def cancel(self, external_id):
        pass


def test_registry_lazy_construction_and_provider_mismatch_detection():
    registry.reset_registry()
    try:
        calls = []

        def factory():
            calls.append(1)
            return _DummyAdapter()

        registry.register_adapter("dummy", factory)
        assert calls == []  # not constructed until first get_adapter call

        adapter = registry.get_adapter("dummy")
        assert isinstance(adapter, _DummyAdapter)
        assert calls == [1]

        # Cached: second call does not re-invoke the factory.
        registry.get_adapter("dummy")
        assert calls == [1]

        with pytest.raises(KeyError):
            registry.get_adapter("nonexistent")

        class _MismatchedAdapter(_DummyAdapter):
            provider = "other-name"

        registry.register_adapter("mismatched", _MismatchedAdapter)
        with pytest.raises(ValueError, match="reports provider"):
            registry.get_adapter("mismatched")
    finally:
        registry.reset_registry()
        # Re-register built-ins so later tests in the same process still see them.
        import importlib

        import matcreator.control_plane.providers as providers_pkg

        importlib.reload(providers_pkg)
