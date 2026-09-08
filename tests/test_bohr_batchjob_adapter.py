from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from matcreator.control_plane.providers._bohr_cli import BohrCLIError
from matcreator.control_plane.providers.base import (
    CapabilityError,
    RemoteJobCapability,
    RemoteJobSubmissionUncertainError,
)
from matcreator.control_plane.providers.bohr_batchjob import BohrBatchJobAdapter


def _ok(data) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, json.dumps({"ok": True, "data": data}), "")


def _err(message: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        [], 1, json.dumps({"ok": False, "error": {"message": message}}), ""
    )


@pytest.fixture(autouse=True)
def no_remote_commands(monkeypatch):
    def reject(*args, **kwargs):
        pytest.fail("Unexpected subprocess invocation; remote commands must be mocked")

    monkeypatch.setattr(subprocess, "run", reject)


@pytest.fixture
def spec():
    return {
        "project_id": 42,
        "name": "relax-job",
        "image": "registry.dp.tech/dptech/vasp:5.4.4",
        "command": "vasp_std > run.log",
        "machine_type": "c8_m32_cpu",
    }


def test_create_exact_argv_preflights_even_without_input(monkeypatch, spec):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _ok({"jobId": "batch-job-123"})

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert BohrBatchJobAdapter().create(spec) == "batch-job-123"
    args = [
        "batchjob", "submit",
        "--name", "relax-job",
        "--image", spec["image"],
        "--command", "vasp_std > run.log",
        "--project-id", "42",
        "--machine-type", "c8_m32_cpu",
        "--max-run-time", "24h",
        "--max-wait-time", "30m",
    ]
    assert len(calls) == 2
    assert calls[0][0][1:] == [*args, "--dry-run", "-o", "json", "--no-interactive", "-y"]
    assert calls[1][0][1:] == [*args, "-o", "json", "--no-interactive", "-y"]
    assert calls[0][1] == {
        "capture_output": True, "text": True, "timeout": 120, "check": False,
    }


@pytest.mark.parametrize("input_kind", ["file", "directory"])
def test_create_preserves_sku_input_outputs_and_durations(monkeypatch, spec, tmp_path, input_kind):
    source = tmp_path / "input with spaces"
    if input_kind == "directory":
        source.mkdir()
        (source / "INCAR").write_text("input")
    else:
        source.write_text("input")
    spec.pop("machine_type")
    spec.update(
        sku_id=123, input_path=source, out_files=["OUTCAR", "logs/*.log"],
        max_run_time="1h30m0.5s", max_wait_time="1000ms", project_id=0,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _ok({"jobId": "batch-job-123"})

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert BohrBatchJobAdapter().create(spec) == "batch-job-123"
    submitted = calls[1]
    for flag, value in [
        ("--sku-id", "123"), ("--input", str(source)), ("--project-id", "0"),
        ("--max-run-time", "1h30m0.5s"), ("--max-wait-time", "1000ms"),
    ]:
        assert submitted[submitted.index(flag) + 1] == value
    assert "--machine-type" not in submitted
    assert submitted.count("--out-file") == 2
    assert submitted[submitted.index("--out-file"):] == [
        "--out-file", "OUTCAR", "--out-file", "logs/*.log",
        "-o", "json", "--no-interactive", "-y",
    ]
    assert [arg for arg in calls[0] if arg != "--dry-run"] == submitted


@pytest.mark.parametrize("field", ["project_id", "name", "image", "command"])
def test_create_requires_fields(spec, field):
    spec.pop(field)
    with pytest.raises(ValueError, match=field):
        BohrBatchJobAdapter().create(spec)


@pytest.mark.parametrize("changes", [
    {"machine_type": None},
    {"sku_id": "sku-123"},
    {"machine_type": ""},
    {"machine_type": 123},
    {"machine_type": None, "sku_id": ""},
    {"machine_type": None, "sku_id": False},
])
def test_create_requires_exactly_one_valid_selector(spec, changes):
    spec.update(changes)
    with pytest.raises(ValueError, match="machine_type|sku_id"):
        BohrBatchJobAdapter().create(spec)


@pytest.mark.parametrize("field,value", [
    ("max_run_time", 60), ("max_wait_time", None), ("max_run_time", ""),
    ("out_files", "OUTCAR"), ("out_files", [""]), ("out_files", [3]),
    ("input_path", ""), ("input_path", 123),
    ("input_root", ""), ("input_root", 123),
    ("name", " "), ("image", None), ("project_id", True),
])
def test_create_rejects_invalid_spec_types(spec, field, value):
    spec[field] = value
    with pytest.raises(ValueError, match=field):
        BohrBatchJobAdapter().create(spec)


def test_create_with_input_root_pins_cwd_for_preflight_and_submit(monkeypatch, spec, tmp_path):
    source = tmp_path / "input dir"
    source.mkdir()
    (source / "INCAR").write_text("input")
    spec.update(input_path="input dir", input_root=str(tmp_path))
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return _ok({"jobId": "batch-job-123"})

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert BohrBatchJobAdapter().create(spec) == "batch-job-123"
    assert len(calls) == 2
    for command, kwargs in calls:
        assert command[command.index("--input") + 1] == "input dir"
        assert kwargs["cwd"] == str(tmp_path)
    assert [arg for arg in calls[0][0] if arg != "--dry-run"] == calls[1][0]


def test_create_with_missing_input_root_raises_cli_error_not_missing_binary(spec, tmp_path):
    # A vanished workspace must not be misreported as a missing `bohr` binary;
    # the autouse no_remote_commands fixture proves subprocess.run is never hit.
    spec.update(input_path="input", input_root=str(tmp_path / "gone"))
    with pytest.raises(BohrCLIError, match="working directory does not exist"):
        BohrBatchJobAdapter().create(spec)


@pytest.mark.parametrize("duration", ["0", "500ms", "-1s", "1d", "nonsense"])
def test_duration_preflight_failure_never_submits(monkeypatch, spec, duration):
    calls = []
    spec["max_run_time"] = duration

    def fake_run(command, **kwargs):
        calls.append(command)
        assert command[command.index("--max-run-time") + 1] == duration
        return _err("max-run-time must be a valid Go duration of at least 1s")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(BohrCLIError, match="at least 1s"):
        BohrBatchJobAdapter().create(spec)
    assert len(calls) == 1 and "--dry-run" in calls[0]


@pytest.mark.parametrize("input_kind", [
    "missing", "empty-directory", "root-symlink", "nested-symlink", "fifo",
])
def test_input_preflight_failure_never_submits(monkeypatch, spec, tmp_path, input_kind):
    source = tmp_path / "input"
    if input_kind == "empty-directory":
        source.mkdir()
    elif input_kind in {"root-symlink", "nested-symlink"}:
        target = tmp_path / "real-file"
        target.write_text("input")
        if input_kind == "root-symlink":
            source.symlink_to(target)
        else:
            source.mkdir()
            (source / "linked").symlink_to(target)
    elif input_kind == "fifo":
        import os

        os.mkfifo(source)
    spec["input_path"] = source
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert command[command.index("--input") + 1] == str(source)
        return _err(f"invalid input {source}; no remote job was created")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(BohrCLIError, match="no remote job was created"):
        BohrBatchJobAdapter().create(spec)
    assert len(calls) == 1 and "--dry-run" in calls[0]


@pytest.mark.parametrize("data", [
    None, [], "job-id", {}, {"id": "job-id"}, {"bohrId": "123"},
    {"jobId": 123}, {"jobId": True}, {"jobId": ""}, {"jobId": " "},
])
def test_create_rejects_malformed_ids_without_retry(monkeypatch, spec, data):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return _ok(data)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(
        RemoteJobSubmissionUncertainError, match="string jobId.*do not resubmit blindly"
    ) as exc:
        BohrBatchJobAdapter().create(spec)
    assert isinstance(exc.value.__cause__, BohrCLIError)
    assert len(calls) == 2


def test_post_precreate_failure_preserves_inspection_guidance(monkeypatch, spec):
    calls = []
    message = "Upload failed; prepared job batch-job-123 remains. Run bohr batchjob describe batch-job-123"

    def fake_run(command, **kwargs):
        calls.append(command)
        return _ok({}) if "--dry-run" in command else _err(message)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RemoteJobSubmissionUncertainError, match="prepared job batch-job-123") as exc:
        BohrBatchJobAdapter().create(spec)
    assert str(exc.value).startswith(message)
    assert "do not resubmit blindly" in str(exc.value)
    assert isinstance(exc.value.__cause__, BohrCLIError)
    assert str(exc.value.__cause__) == message
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["envelope", "non-json", "timeout", "missing-cli"])
def test_real_submission_cli_failures_are_uncertain(monkeypatch, spec, failure):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "--dry-run" in command:
            return _ok({})
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "missing-cli":
            raise FileNotFoundError("bohr")
        if failure == "non-json":
            return subprocess.CompletedProcess(command, 0, "invalid JSON", "")
        return _err("final submit failed")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(
        RemoteJobSubmissionUncertainError, match="Inspect.*manually.*do not resubmit blindly"
    ) as exc:
        BohrBatchJobAdapter().create(spec)
    assert isinstance(exc.value.__cause__, BohrCLIError)
    assert str(exc.value.__cause__) in str(exc.value)
    assert len(calls) == 2


@pytest.mark.parametrize("name,normalized,terminal", [
    ("prepared", "queued", False), ("pending", "queued", False),
    ("active", "running", False), ("running", "running", False),
    ("succeeded", "succeeded", True), ("failed", "failed", True),
    ("deleted", "cancelled", True), ("killed", "cancelled", True),
    ("unknown", None, False), ("future-status", None, False),
    ("", None, False),
])
def test_status_semantics_ignore_numeric_status_and_exit_code(monkeypatch, name, normalized, terminal):
    def fake_run(command, **kwargs):
        assert command[1:4] == ["batchjob", "describe", "batch-job-123"]
        return _ok({
            "status_name": name, "terminal": True, "status": 2, "exitCode": 0,
            "resultUrl": "https://example.invalid/results?secret=credential",
        })

    monkeypatch.setattr(subprocess, "run", fake_run)
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.normalized_status == normalized
    assert status.snapshot == {
        "provider_status": name or "unknown",
        "status_name": name or None,
        "terminal": terminal,
    }


@pytest.mark.parametrize("name,normalized", [
    ("succeeded", "succeeded"), ("failed", "failed"),
    ("deleted", "cancelled"), ("killed", "cancelled"),
])
@pytest.mark.parametrize("terminal_field", [{}, {"terminal": False}, {"terminal": "false"}])
def test_terminal_snapshot_matches_semantic_outcome(monkeypatch, name, normalized, terminal_field):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({
        "status_name": name, "status": 0, "exitCode": None, **terminal_field,
    }))
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.normalized_status == normalized
    assert status.snapshot == {
        "provider_status": name, "status_name": name, "terminal": True,
    }


@pytest.mark.parametrize("details,error", [
    ({"errorMessage": "Queue timeout", "errorCode": 2005}, "Queue timeout"),
    ({"errorCode": 2005}, "Batch Job failed (errorCode: 2005)"),
    ({"errorMessage": " ", "errorCode": 0}, "Batch Job failed (errorCode: 0)"),
])
def test_status_curates_failure_details(monkeypatch, details, error):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({
        "status_name": "failed", "terminal": True, **details, "resultUrl": "private",
    }))
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.error == error
    assert status.snapshot == {
        "provider_status": "failed", "status_name": "failed", "terminal": True, **details,
    }


@pytest.mark.parametrize("data", [None, [], "failed", {"status_name": 3}])
def test_status_rejects_malformed_payloads(monkeypatch, data):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok(data))
    with pytest.raises(BohrCLIError, match="invalid"):
        BohrBatchJobAdapter().status("batch-job-123")


def test_status_without_semantic_name_is_nonterminal_observation(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({
        "status": 2, "exitCode": 0, "terminal": True,
    }))
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.normalized_status is None
    assert status.snapshot == {
        "provider_status": "unknown", "status_name": None, "terminal": False,
    }


@pytest.mark.parametrize("name,numeric,reason,normalized,terminal", [
    ("prepared", -10, "job_prepared", "queued", False),
    ("pending", 0, "job_submitted", "queued", False),
    ("succeeded", 2, "job_succeeded", "succeeded", True),
    ("failed", -1, "", "failed", True),
    ("deleted", -2, "", "cancelled", True),
])
def test_status_observed_bohr_2_6_86_describe_shape(
    monkeypatch, name, numeric, reason, normalized, terminal,
):
    # Sanitized direct data shape verified with live read-only describe calls.
    data = {
        "jobId": "batch-job-123",
        "status": numeric,
        "status_name": name,
        "terminal": terminal,
        "statusReason": reason,
        "exitCode": 0 if name == "succeeded" else None,
        "errorCode": 0,
        "errorMessage": "worker failed" if name == "failed" else "",
        "resultUrl": "https://example.invalid/results?secret=redacted",
        "cmd": "private command",
        "imageName": "private image",
    }
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok(data))
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.normalized_status == normalized
    assert status.snapshot == {
        "provider_status": name,
        "status_name": name,
        "terminal": terminal,
        "errorCode": 0,
        "errorMessage": data["errorMessage"],
    }


@pytest.mark.parametrize("exit_code", [None, 0])
def test_status_observed_queue_timeout_is_failure(monkeypatch, exit_code):
    # Live describe returned null; the embedded reference also warns about raw zero.
    message = "ScheduleTimeout: 排队超时未调度，可稍后重试或更换机型"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({
        "jobId": "batch-job-timeout",
        "status": -1,
        "status_name": "failed",
        "terminal": True,
        "statusReason": "scheduling_timeout",
        "exitCode": exit_code,
        "errorCode": 2005,
        "errorMessage": message,
    }))
    status = BohrBatchJobAdapter().status("batch-job-timeout")
    assert status.normalized_status == "failed"
    assert status.snapshot["terminal"] is True
    assert status.error == message


@pytest.mark.parametrize("numeric", [-999, -3, 3, 999])
def test_status_unrecognized_numeric_status_cannot_become_success(monkeypatch, numeric):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({
        "status": numeric, "status_name": "unknown", "terminal": True, "exitCode": 0,
    }))
    status = BohrBatchJobAdapter().status("batch-job-123")
    assert status.normalized_status is None
    assert status.snapshot == {
        "provider_status": "unknown", "status_name": "unknown", "terminal": False,
    }


def test_cancel_waits_for_confirmed_stop(monkeypatch):
    def fake_run(command, **kwargs):
        assert command[1:] == [
            "batchjob", "kill", "batch-job-123", "-o", "json", "--no-interactive", "-y",
        ]
        assert kwargs["timeout"] >= 30
        return _ok({"confirmed": True, "status_name": "deleted", "terminal": True})

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert BohrBatchJobAdapter().cancel("batch-job-123") is None


@pytest.mark.parametrize("data", [
    {"confirmed": False, "status_name": "running"}, {}, None, [], {"confirmed": "true"},
])
def test_cancel_unconfirmed_or_malformed_is_not_termination(monkeypatch, data):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok(data))
    with pytest.raises(BohrCLIError, match="not confirmed.*bohr batchjob describe"):
        BohrBatchJobAdapter().cancel("batch-job-123")


def test_collect_downloads_to_new_directory_with_long_timeout(monkeypatch, tmp_path):
    dest = tmp_path / "new-parent" / "outputs"

    def fake_run(command, **kwargs):
        assert not dest.exists()
        assert command[1:] == [
            "batchjob", "download", "batch-job-123", "--dest", str(dest),
            "--timeout", "2h", "-o", "json", "--no-interactive", "-y",
        ]
        assert kwargs["timeout"] > 7200
        (dest / "logs").mkdir(parents=True)
        (dest / "OUTCAR").write_text("data")
        (dest / "logs" / "run.log").write_text("data")
        return _ok({})

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert BohrBatchJobAdapter().collect_outputs("batch-job-123", dest) == [
        {"source": "batch-job-123", "destination": str(dest / "OUTCAR")},
        {"source": "batch-job-123", "destination": str(dest / "logs" / "run.log")},
    ]


@pytest.mark.parametrize("kind", ["directory", "file", "dangling-symlink", "symlink"])
def test_collect_never_overwrites_existing_destination(tmp_path, kind):
    dest = tmp_path / "out"
    if kind == "directory":
        dest.mkdir()
    elif kind == "file":
        dest.write_text("keep")
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.mkdir()
        dest.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="must not exist"):
        BohrBatchJobAdapter().collect_outputs("batch-job-123", dest)
    if kind == "file":
        assert dest.read_text() == "keep"


def test_collect_rejects_success_without_downloaded_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _ok({}))
    with pytest.raises(BohrCLIError, match="did not create"):
        BohrBatchJobAdapter().collect_outputs("batch-job-123", tmp_path / "out")


def test_collect_does_not_return_partial_files_after_failure(monkeypatch, tmp_path):
    dest = tmp_path / "out"

    def fake_run(command, **kwargs):
        dest.mkdir()
        (dest / "partial").write_text("partial")
        return _err("unsafe archive")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(BohrCLIError, match="unsafe archive"):
        BohrBatchJobAdapter().collect_outputs("batch-job-123", dest)


@pytest.mark.parametrize("operation", ["create", "status", "cancel", "collect"])
@pytest.mark.parametrize("failure", ["envelope", "non-json", "timeout", "missing-cli"])
def test_cli_boundary_errors_propagate(monkeypatch, spec, tmp_path, operation, failure):
    def fake_run(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if failure == "missing-cli":
            raise FileNotFoundError("bohr")
        if failure == "non-json":
            return subprocess.CompletedProcess(command, 0, "invalid JSON", "")
        return _err("record not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = BohrBatchJobAdapter()
    with pytest.raises(BohrCLIError):
        if operation == "create":
            adapter.create(spec)
        elif operation == "status":
            adapter.status("batch-job-123")
        elif operation == "cancel":
            adapter.cancel("batch-job-123")
        else:
            adapter.collect_outputs("batch-job-123", tmp_path / "out")


def test_capabilities_and_poll_cadence():
    adapter = BohrBatchJobAdapter()
    assert adapter.provider == "bohr_batchjob"
    assert adapter.capabilities == frozenset({RemoteJobCapability.BATCH_COLLECT})
    assert adapter.poll_interval_seconds == 60.0
    with pytest.raises(CapabilityError):
        adapter.run_command("batch-job-123", "echo no")
    with pytest.raises(CapabilityError):
        adapter.upload_file("batch-job-123", Path("input"), "remote")
