from __future__ import annotations

import base64
import re

from matcreator.control_plane.providers.base import RemoteJobAdapter, RemoteJobCapability, RemoteJobStatus
from matcreator.control_plane.remote_job_service import RemoteJobService
from matcreator.control_plane.remote_jobs import RemoteJobStore


class _FakeAdapter(RemoteJobAdapter):
    """Fake adapter conforming to the provider protocol, used via adapter_overrides.

    Declares every optional capability by default so one fake can cover the
    submit/pause/terminate/command/upload/download surface; a test can shrink
    ``capabilities`` to exercise CapabilityError handling.
    """

    provider = "e2b"
    capabilities = frozenset(
        {
            RemoteJobCapability.PAUSE,
            RemoteJobCapability.INTERACTIVE_EXEC,
            RemoteJobCapability.FILE_TRANSFER,
        }
    )

    def __init__(self) -> None:
        self.created_specs: list[dict] = []
        self.paused: list[str] = []
        self.cancelled: list[str] = []
        self.on_run = None
        self.files: dict[str, bytes] = {}
        self.launched_commands: list[str] = []

    def create(self, spec: dict) -> str:
        self.created_specs.append(spec)
        return "sandbox-123"

    def status(self, external_id: str) -> RemoteJobStatus:
        return RemoteJobStatus(normalized_status=None, snapshot={"provider_status": "reachable"})

    def cancel(self, external_id: str) -> None:
        self.cancelled.append(external_id)

    def pause(self, external_id: str) -> None:
        self.paused.append(external_id)

    def run_command(self, external_id: str, command: str, *, user: str = "root") -> dict:
        if self.on_run:
            self.on_run()
        # Minimal shell simulation covering the three wrapper patterns
        # RemoteJobService.start_job_command/poll_job_command construct, so
        # tests can exercise them without a real shell.
        launch = re.search(
            r"rm -f (\S+); nohup sh -c 'echo (\S+) \| base64 -d \| sh; echo \$\? > \S+' > (\S+) 2>&1",
            command,
        )
        if launch:
            exit_path, payload, log_path = launch.groups()
            self.launched_commands.append(base64.b64decode(payload).decode("utf-8"))
            self.files.pop(exit_path, None)
            self.files.setdefault(log_path, b"")
            return {"stdout": "LAUNCHED\n", "stderr": "", "exit_code": 0}
        check = re.match(r"if \[ -f (\S+) \]; then echo DONE:\$\(cat \S+\); else echo RUNNING; fi", command)
        if check:
            exit_path = check.group(1)
            if exit_path in self.files:
                code = self.files[exit_path].decode("utf-8").strip()
                return {"stdout": f"DONE:{code}\n", "stderr": "", "exit_code": 0}
            return {"stdout": "RUNNING\n", "stderr": "", "exit_code": 0}
        tail = re.match(r"tail -c (\d+) (\S+)", command)
        if tail:
            n, log_path = tail.groups()
            data = self.files.get(log_path, b"")
            return {"stdout": data[-int(n):].decode("utf-8", errors="replace"), "stderr": "", "exit_code": 0}
        return {"stdout": "", "stderr": "", "exit_code": 0}

    def upload_file(self, external_id: str, source, destination: str) -> None:
        self.uploads = getattr(self, "uploads", [])
        self.uploads.append((external_id, str(source), destination))

    def download_file(self, external_id: str, source: str, destination, *, user: str | None = None):
        self.downloads = getattr(self, "downloads", [])
        self.downloads.append((external_id, source, str(destination)))
        return destination


def _spec() -> dict:
    return {
        "template": "doc-compiler",
        "api_key": "super-secret",
        "api_url": "https://e2b.example",
        "project_id": "project-42",
        "timeout": 600,
    }


def _persisted_spec() -> dict:
    return {key: value for key, value in _spec().items() if key != "api_key"}


def test_submit_job_persists_sandbox_without_api_key_and_is_idempotent(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})

    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
        persisted_specification=_persisted_spec(),
    )
    replay = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
        persisted_specification=_persisted_spec(),
    )

    assert job["status"] == "running"
    assert job["external_id"] == "sandbox-123"
    assert "api_key" not in job["specification"]
    assert replay["job_id"] == job["job_id"]
    assert len(adapter.created_specs) == 1


def test_submit_job_retries_after_a_creation_failure(tmp_path) -> None:
    """A job that failed before acquiring an external ID must not poison its

    idempotency key forever: the next submission with the same key resets the
    record and re-attempts provider creation."""

    class _FlakyAdapter(_FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls = 0

        def create(self, spec: dict) -> str:
            self.create_calls += 1
            if self.create_calls == 1:
                raise ValueError("dictionary update sequence element #0 has length 1; 2 is required")
            return super().create(spec)

    adapter = _FlakyAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})

    failed = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )
    assert failed["status"] == "failed"
    assert failed["external_id"] is None
    assert "dictionary update sequence" in failed["error"]

    retried = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )
    assert retried["job_id"] == failed["job_id"]
    assert retried["status"] == "running"
    assert retried["external_id"] == "sandbox-123"
    assert retried["error"] is None
    assert adapter.create_calls == 2


def test_submit_job_does_not_retry_a_failure_that_has_an_external_id(tmp_path) -> None:
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )
    store.transition_job(job["job_id"], "failed", error="provider died mid-run")

    replay = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )

    assert replay["status"] == "failed"
    assert replay["error"] == "provider died mid-run"
    assert len(adapter.created_specs) == 1


def test_job_controls_update_durable_state(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )

    paused = service.pause_job(job["job_id"])
    assert paused["status"] == "paused"
    assert adapter.paused == ["sandbox-123"]

    terminated = service.terminate_job(paused["job_id"])
    assert terminated["status"] == "terminated"
    assert adapter.cancelled == ["sandbox-123"]


def test_pause_job_raises_capability_error_for_pause_unsupported_provider(tmp_path) -> None:
    class _NoPauseAdapter(_FakeAdapter):
        capabilities = frozenset({RemoteJobCapability.INTERACTIVE_EXEC, RemoteJobCapability.FILE_TRANSFER})

    adapter = _NoPauseAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )

    try:
        service.pause_job(job["job_id"])
        assert False, "expected CapabilityError"
    except Exception as exc:
        assert "does not support 'pause'" in str(exc)


def test_command_merges_telemetry_after_monitor_observation(tmp_path) -> None:
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )

    adapter.on_run = lambda: store.record_observation(
        job["job_id"],
        snapshot={"provider_status": "reachable", "monitor_probe": "fresh"},
        expected_revision=store.get_job(job["job_id"])["state_revision"],
    )

    assert service.run_job_command(job["job_id"], "echo done") == {
        "stdout": "", "stderr": "", "exit_code": 0
    }
    assert store.get_job(job["job_id"])["snapshot"] == {
        "provider_status": "reachable",
        "monitor_probe": "fresh",
        "last_command_exit_code": 0,
    }


def test_download_job_file_merges_telemetry_and_returns_paths(tmp_path) -> None:
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )
    dest = tmp_path / "CHGCAR"

    result = service.download_job_file(job["job_id"], "/home/user/CHGCAR", dest)

    assert result == {"source": "/home/user/CHGCAR", "destination": str(dest.resolve())}
    assert adapter.downloads == [("sandbox-123", "/home/user/CHGCAR", str(dest.resolve()))]
    assert store.get_job(job["job_id"])["snapshot"] == {
        "provider_status": "reachable",
        "last_download": "CHGCAR",
    }


def test_reconcile_job_transitions_on_normalized_status_change(tmp_path) -> None:
    class _BatchAdapter(_FakeAdapter):
        provider = "bohr_job"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def __init__(self) -> None:
            super().__init__()
            self.status_calls = 0

        def status(self, external_id: str) -> RemoteJobStatus:
            # First probe (right after create, inside submit_job) reports
            # "queued"; only a later explicit reconcile reports "succeeded" —
            # this exercises reconcile_job's own transition, not submission.
            self.status_calls += 1
            if self.status_calls == 1:
                return RemoteJobStatus(normalized_status="queued", snapshot={"phase": "pending"})
            return RemoteJobStatus(normalized_status="succeeded", snapshot={"phase": "completed"})

    adapter = _BatchAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_job": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_job",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "job_name": "n", "machine_type": "c2", "image_address": "img", "command": "cmd"},
    )
    assert job["status"] == "queued"

    reconciled = service.reconcile_job(job["job_id"])
    assert reconciled["status"] == "succeeded"
    assert reconciled["snapshot"]["phase"] == "completed"


def test_collect_job_outputs_is_idempotent(tmp_path) -> None:
    class _BatchAdapter(_FakeAdapter):
        provider = "bohr_job"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def __init__(self) -> None:
            super().__init__()
            self.collect_calls: list[str] = []

        def status(self, external_id: str) -> RemoteJobStatus:
            return RemoteJobStatus(normalized_status="succeeded", snapshot={"phase": "completed"})

        def collect_outputs(self, external_id: str, destination_dir):
            self.collect_calls.append(external_id)
            return [{"source": external_id, "destination": str(destination_dir)}]

    adapter = _BatchAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_job": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_job",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "job_name": "n", "machine_type": "c2", "image_address": "img", "command": "cmd"},
    )
    service.reconcile_job(job["job_id"])

    collected = service.collect_job_outputs(job["job_id"], tmp_path / "out")
    assert collected["status"] == "collected"
    assert len(collected["artifacts"]) == 1
    assert adapter.collect_calls == ["sandbox-123"]

    replay = service.collect_job_outputs(job["job_id"], tmp_path / "out")
    assert replay["status"] == "collected"
    assert adapter.collect_calls == ["sandbox-123"]


def test_start_job_command_persists_handle_with_derived_marker_paths(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec=_spec(),
    )

    result = service.start_job_command(job["job_id"], "sleep 300 && echo done")

    assert result["handle"]["log_path"] == f"/tmp/matcreator-cmd-{job['job_id']}.log"
    assert result["handle"]["exit_path"] == f"/tmp/matcreator-cmd-{job['job_id']}.exit"
    assert adapter.launched_commands == ["sleep 300 && echo done"]
    persisted = service.store.get_job(job["job_id"])
    assert persisted["snapshot"]["background_command"]["log_path"] == result["handle"]["log_path"]


def test_start_job_command_base64_round_trips_arbitrary_shell_content(tmp_path) -> None:
    """Quotes, `$()`, and backticks in the command must survive intact —
    proving the wrapper can't be broken out of or reinterpreted."""
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1", spec=_spec(),
    )
    tricky_command = """echo 'it'"'"'s a test' && echo "$(date)" && echo `whoami`"""

    service.start_job_command(job["job_id"], tricky_command)

    assert adapter.launched_commands == [tricky_command]


def test_poll_job_command_reports_running_while_no_exit_marker(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1", spec=_spec(),
    )
    service.start_job_command(job["job_id"], "sleep 300")

    result = service.poll_job_command(job["job_id"])

    assert result["running"] is True


def test_poll_job_command_reports_result_once_finished_and_clears_handle(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1", spec=_spec(),
    )
    started = service.start_job_command(job["job_id"], "echo hello")
    # Simulate the background command finishing inside the sandbox.
    adapter.files[started["handle"]["exit_path"]] = b"0\n"
    adapter.files[started["handle"]["log_path"]] = b"hello\n"

    result = service.poll_job_command(job["job_id"])

    assert result == {
        "running": False,
        "exit_code": 0,
        "output_tail": "hello\n",
        "log_path": started["handle"]["log_path"],
    }
    assert service.store.get_job(job["job_id"])["snapshot"]["background_command"] is None


def test_poll_job_command_requires_a_started_command(tmp_path) -> None:
    adapter = _FakeAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1", spec=_spec(),
    )

    try:
        service.poll_job_command(job["job_id"])
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "no in-flight background command" in str(exc)


def test_start_job_command_raises_capability_error_for_batch_provider(tmp_path) -> None:
    class _BatchAdapter(_FakeAdapter):
        provider = "bohr_job"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

    adapter = _BatchAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"bohr_job": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="bohr_job",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "job_name": "n", "machine_type": "c2", "image_address": "img", "command": "cmd"},
    )

    try:
        service.start_job_command(job["job_id"], "echo hi")
        assert False, "expected CapabilityError"
    except Exception as exc:
        assert "does not support 'interactive_exec'" in str(exc)
