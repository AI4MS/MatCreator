from __future__ import annotations

import json
import subprocess

import pytest

from matcreator.control_plane.providers._bohr_cli import BohrCLIError
from matcreator.control_plane.providers.base import RemoteJobCapability
from matcreator.control_plane.providers.bohr_job import BohrJobAdapter


class _FakeCompleted:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _ok(data) -> str:
    return json.dumps({"ok": True, "data": data})


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": {"message": message}})


def test_create_submits_job_and_extracts_bohr_id(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompleted(_ok({"bohrId": 20543207, "id": 23197091}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    external_id = adapter.create(
        {
            "project_id": 42,
            "job_name": "relax-job",
            "machine_type": "c8_m32_cpu",
            "image_address": "registry.dp.tech/dptech/vasp:5.4.4",
            "command": "vasp_std",
            "input_directory": "./input",
            "max_run_time": 60,
        }
    )

    assert external_id == "20543207"
    command = captured["command"]
    assert command[1:4] == ["job", "submit", "--project_id"]
    assert "42" in command
    assert "--input_directory" in command and "./input" in command
    assert "--max_run_time" in command and "60" in command
    assert "-o" in command and "json" in command
    assert "--no-interactive" in command
    assert "-y" in command


def test_create_requires_all_fields() -> None:
    adapter = BohrJobAdapter()

    with pytest.raises(ValueError, match="job_name"):
        adapter.create({"project_id": 1})


def test_status_maps_phase_to_normalized_status(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"phase": "completed", "terminal": True}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    status = adapter.status("20543207")

    assert status.normalized_status == "succeeded"
    assert status.snapshot == {"phase": "completed", "terminal": True}
    assert status.error is None


def test_status_maps_failed_phase_and_captures_error_info(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"phase": "failed", "terminal": True, "errorInfo": "Command not found."}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    status = adapter.status("20543187")

    assert status.normalized_status == "failed"
    assert status.error == "Command not found."


def test_status_maps_stopped_phase_to_cancelled(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_ok({"phase": "stopped", "terminal": True}))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    status = adapter.status("20539094")

    assert status.normalized_status == "cancelled"


def test_status_raises_bohr_cli_error_on_failure(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        return _FakeCompleted(_err("record not found"))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    with pytest.raises(BohrCLIError, match="record not found"):
        adapter.status("nonexistent")


def test_cancel_invokes_terminate_with_no_wait(monkeypatch) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return _FakeCompleted(_ok(None))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    adapter.cancel("20543207")

    command = captured["command"]
    assert command[1:5] == ["job", "terminate", "--id", "20543207"]
    assert "--no-wait" in command


def test_collect_outputs_downloads_and_lists_files(monkeypatch, tmp_path) -> None:
    def fake_run(command, **kwargs):
        # Simulate the download command producing files in the destination.
        dest = tmp_path / "out"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "OUTCAR").write_text("data")
        (dest / "vasprun.xml").write_text("data")
        return _FakeCompleted(_ok(None))

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrJobAdapter()

    artifacts = adapter.collect_outputs("20543207", tmp_path / "out")

    sources = {artifact["source"] for artifact in artifacts}
    destinations = {artifact["destination"] for artifact in artifacts}
    assert sources == {"20543207"}
    assert destinations == {str(tmp_path / "out" / "OUTCAR"), str(tmp_path / "out" / "vasprun.xml")}


def test_capabilities_are_batch_collect_only() -> None:
    adapter = BohrJobAdapter()

    assert adapter.capabilities == frozenset({RemoteJobCapability.BATCH_COLLECT})
    assert adapter.provider == "bohr_job"
