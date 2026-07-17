from __future__ import annotations

import sys
import types

import pytest

from matcreator.control_plane.e2b import E2BConfigurationError, E2BSandboxAdapter, E2BSandboxSpec


class _FakeResult:
    stdout = "hello\n"
    stderr = ""
    exit_code = 0


class _FakeSandbox:
    sandbox_id = "sandbox-123"
    created_with: dict = {}
    connected_to: list[str] = []
    paused = False
    killed = False

    @classmethod
    def create(cls, **kwargs):
        cls.created_with = kwargs
        return cls()

    @classmethod
    def connect(cls, sandbox_id):
        cls.connected_to.append(sandbox_id)
        return cls()

    class commands:
        @staticmethod
        def run(command, user):
            assert command == "echo hello"
            assert user == "root"
            return _FakeResult()

    def pause(self):
        type(self).paused = True

    def kill(self):
        type(self).killed = True


@pytest.fixture(autouse=True)
def fake_e2b_module(monkeypatch):
    _FakeSandbox.created_with = {}
    _FakeSandbox.connected_to = []
    _FakeSandbox.paused = False
    _FakeSandbox.killed = False
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", types.SimpleNamespace(Sandbox=_FakeSandbox))


def test_adapter_creates_sandbox_with_project_header() -> None:
    adapter = E2BSandboxAdapter()
    sandbox_id = adapter.create(
        E2BSandboxSpec(
            template="doc-compiler",
            api_key="secret",
            api_url="https://e2b.example",
            project_id="project-42",
            lifecycle={"on_timeout": "pause"},
        )
    )

    assert sandbox_id == "sandbox-123"
    assert _FakeSandbox.created_with["headers"] == {"X-Project-Id": "project-42"}
    assert _FakeSandbox.created_with["lifecycle"] == {"on_timeout": "pause"}


def test_adapter_connects_for_command_and_controls() -> None:
    adapter = E2BSandboxAdapter()

    assert adapter.run_command("sandbox-123", "echo hello") == {
        "stdout": "hello\n",
        "stderr": "",
        "exit_code": 0,
    }
    adapter.pause("sandbox-123")
    adapter.terminate("sandbox-123")

    assert _FakeSandbox.connected_to == ["sandbox-123", "sandbox-123", "sandbox-123"]
    assert _FakeSandbox.paused is True
    assert _FakeSandbox.killed is True


def test_adapter_rejects_missing_required_configuration() -> None:
    spec = E2BSandboxSpec(
        template="",
        api_key="secret",
        api_url="https://e2b.example",
        project_id="project-42",
    )

    with pytest.raises(E2BConfigurationError, match="template"):
        spec.create_kwargs()