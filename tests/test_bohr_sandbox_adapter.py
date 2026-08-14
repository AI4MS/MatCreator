from __future__ import annotations

import json
import subprocess

import pytest

from matcreator.control_plane.providers._bohr_cli import BohrCLIError
from matcreator.control_plane.providers.base import RemoteJobCapability
from matcreator.control_plane.providers.bohr_sandbox import BohrSandboxAdapter


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _ok(data) -> str:
    return json.dumps({"ok": True, "data": data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": {"message": message}})


def test_create_builds_command_and_extracts_sandbox_id(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        # Real `bohr sandbox create -o json` payload shape (CLI 2.6.15):
        # the ID key is "sandboxID", not "sandbox_id".
        return _FakeCompleted(
            _ok(
                {
                    "sandboxID": "default--sdbxdefault-abc12",
                    "templateID": "sdbxagent",
                    "state": "running",
                    "domain": "bohr-sandbox.bohrium.com",
                }
            )
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    sandbox_id = adapter.create(
        {
            "project_id": 1234,
            "template": "sdbxagent",
            "timeout": 3600,
            "env": {"FOO": "bar"},
        }
    )

    assert sandbox_id == "default--sdbxdefault-abc12"
    command = captured["command"]
    assert command[1:3] == ["sandbox", "create"]
    assert "--template" in command and "sdbxagent" in command
    assert "--project-id" in command and "1234" in command
    assert "--timeout" in command and "3600" in command
    assert "--env" in command and "FOO=bar" in command


def test_create_requires_template() -> None:
    adapter = BohrSandboxAdapter()

    with pytest.raises(ValueError, match="template"):
        adapter.create({"project_id": 1234})


def test_create_accepts_snake_case_sandbox_id_spelling(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"sandbox_id": "default--sdbxdefault-abc12"}))

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert BohrSandboxAdapter().create(
        {"project_id": 1, "template": "sdbxagent"}
    ) == "default--sdbxdefault-abc12"


def test_create_requires_project_id() -> None:
    adapter = BohrSandboxAdapter()

    with pytest.raises(ValueError, match="project_id"):
        adapter.create({})


def test_create_raises_when_no_sandbox_id_returned(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"state": "running", "templateID": "sdbxagent"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    with pytest.raises(BohrCLIError, match=r"did not return a sandbox ID.*state.*templateID"):
        adapter.create({"project_id": 1, "template": "sdbxagent"})


def test_status_reports_liveness_without_normalized_status_by_default(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"status": "running"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    status = adapter.status("sbx-1")

    assert status.normalized_status is None
    assert status.snapshot["provider_status"] == "reachable"


def test_status_maps_unreachable_states_to_lost(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"status": "terminated"}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    status = adapter.status("sbx-1")

    assert status.normalized_status == "lost"
    assert status.snapshot["provider_status"] == "unreachable"


def test_cancel_invokes_delete_with_force(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompleted(_ok(None))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    adapter.cancel("sbx-1")

    command = captured["command"]
    assert command[1:4] == ["sandbox", "delete", "sbx-1"]
    assert "--force" in command


def test_run_command_parses_stdout_stderr_exit_code(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeCompleted(_ok({"stdout": "hello\n", "stderr": "", "exit_code": 0}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    result = adapter.run_command("sbx-1", "echo hello")

    assert result == {"stdout": "hello\n", "stderr": "", "exit_code": 0}
    command = captured["command"]
    assert command[1:5] == ["sandbox", "exec", "sbx-1", "--command"]
    assert "echo hello" in command


def test_run_command_disables_both_cli_and_subprocess_timeouts(monkeypatch) -> None:
    """`bohr sandbox exec` caps a command at 90s by default; a long-running
    remote computation must not be silently truncated, matching the E2B
    adapter's unbounded `timeout=0` command semantics."""
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return _FakeCompleted(_ok({"stdout": "", "stderr": "", "exit_code": 0}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    adapter.run_command("sbx-1", "sleep 300")

    command = captured["command"]
    timeout_index = command.index("--timeout")
    assert command[timeout_index + 1] == "0"
    assert captured["kwargs"]["timeout"] is None


def test_upload_file_rejects_missing_source(tmp_path) -> None:
    adapter = BohrSandboxAdapter()

    with pytest.raises(FileNotFoundError):
        adapter.upload_file("sbx-1", tmp_path / "missing.txt", "/home/user/missing.txt")


def test_upload_file_invokes_files_write(monkeypatch, tmp_path) -> None:
    source = tmp_path / "input.txt"
    source.write_text("hello")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompleted(_ok(None))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()

    adapter.upload_file("sbx-1", source, "/home/user/input.txt")

    command = captured["command"]
    assert command[1:5] == ["sandbox", "files", "write", "sbx-1"]
    assert "/home/user/input.txt" in command
    assert "--source" in command and str(source) in command


def test_download_file_invokes_files_read_and_creates_parent_dir(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompleted(_ok(None))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrSandboxAdapter()
    dest = tmp_path / "outputs" / "CHGCAR"

    result = adapter.download_file("sbx-1", "/home/user/CHGCAR", dest)

    assert result == dest.resolve()
    assert dest.parent.is_dir()
    command = captured["command"]
    assert command[1:5] == ["sandbox", "files", "read", "sbx-1"]
    assert "--destination" in command and str(dest.resolve()) in command


def test_capabilities_have_no_pause() -> None:
    adapter = BohrSandboxAdapter()

    assert adapter.capabilities == frozenset(
        {RemoteJobCapability.INTERACTIVE_EXEC, RemoteJobCapability.FILE_TRANSFER}
    )
    assert adapter.provider == "bohr_sandbox"
