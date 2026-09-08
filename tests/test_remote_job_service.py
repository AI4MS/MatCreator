from __future__ import annotations

import base64
import re

import pytest

from matcreator.control_plane.providers.base import (
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobStatus,
    RemoteJobSubmissionUncertainError,
)
from matcreator.control_plane.providers.registry import RetiredProviderError
from matcreator.control_plane.providers.bohr_batchjob import BohrBatchJobAdapter
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


@pytest.mark.parametrize("status", ["queued", "running", "succeeded", "failed", None])
def test_attach_job_reads_without_creating_and_survives_restart(tmp_path, monkeypatch, status):
    adapter = _FakeAdapter()
    reads = []

    def read(external_id):
        reads.append(external_id)
        return RemoteJobStatus(
            normalized_status=status,
            snapshot={"provider_status": status or "unknown"},
            error="remote execution failed" if status == "failed" else None,
        )

    monkeypatch.setattr(adapter, "status", read)
    monkeypatch.setattr(adapter, "create", lambda spec: pytest.fail("attach must never create remotely"))
    path = tmp_path / "jobs.db"
    store = RemoteJobStore(path)
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    args = {
        "owner_id": "alice", "session_id": "s", "provider": "bohr_batchjob",
        "external_id": "external-123", "node_id": "node", "step_number": 2,
    }
    job = service.attach_job(**args)
    assert reads == ["external-123"]
    assert job["external_id"] == "external-123"
    assert job["owner_id"] == "alice"
    assert job["session_id"] == "s"
    assert job["node_id"] == "node"
    assert job["step_number"] == 2
    assert job["provider"] == "bohr_batchjob"
    assert job["specification"] == {"attached": True}
    assert job["status"] == (status or "queued")
    assert job["snapshot"] == {"provider_status": status or "unknown"}
    assert job["error"] == ("remote execution failed" if status == "failed" else None)
    restarted = RemoteJobService(RemoteJobStore(path), adapter_overrides={"bohr_batchjob": adapter})
    assert service.attach_job(**args)["job_id"] == job["job_id"]
    assert restarted.attach_job(**{**args, "node_id": "different-step"})["job_id"] == job["job_id"]
    assert len(store.list_jobs(owner_id="alice", session_id="s")) == 1
    notifications = store.list_pending_notifications()
    assert len(notifications) == (1 if status in {"succeeded", "failed"} else 0)
    if notifications:
        assert notifications[0]["status"] == status
        assert notifications[0]["job_id"] == job["job_id"]


def test_attach_job_reuses_submitted_record_without_duplicate_creation(tmp_path, monkeypatch):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="s", provider="bohr_batchjob", idempotency_key="submitted",
        spec={"name": "original"}, node_id="original-node",
    )
    monkeypatch.setattr(adapter, "create", lambda spec: pytest.fail("attach must never create remotely"))
    attached = service.attach_job(
        owner_id="alice", session_id="s", provider="bohr_batchjob",
        external_id=job["external_id"], node_id="new-node",
    )
    assert attached["job_id"] == job["job_id"]
    assert attached["node_id"] == "original-node"
    assert attached["specification"] == {"name": "original"}
    assert len(store.list_jobs(owner_id="alice", session_id="s")) == 1


@pytest.mark.parametrize("owner,session", [("bob", "s"), ("alice", "other"), ("bob", "other")])
@pytest.mark.parametrize("submitted", [False, True])
def test_attach_job_rejects_other_owners_or_sessions_including_terminal(tmp_path, monkeypatch, owner, session, submitted):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    if submitted:
        job = service.submit_job(
            owner_id="alice", session_id="s", provider="bohr_batchjob", idempotency_key="submitted", spec={},
        )
    else:
        job = service.attach_job(
            owner_id="alice", session_id="s", provider="bohr_batchjob", external_id="external-123",
        )
    store.transition_job(job["job_id"], "failed")
    monkeypatch.setattr(adapter, "status", lambda external_id: pytest.fail("conflict must not probe provider"))
    monkeypatch.setattr(adapter, "create", lambda spec: pytest.fail("conflict must not submit"))
    with pytest.raises(ValueError, match="already tracked in another session") as error:
        service.attach_job(
            owner_id=owner, session_id=session, provider="bohr_batchjob", external_id=job["external_id"],
        )
    assert "alice" not in str(error.value)
    assert job["job_id"] not in str(error.value)
    assert store.list_jobs(owner_id=owner, session_id=session) == []


def test_attach_job_read_failure_does_not_persist_and_can_retry(tmp_path, monkeypatch):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    args = {"owner_id": "alice", "session_id": "s", "provider": "bohr_batchjob", "external_id": "external-123"}

    def unreadable(external_id):
        raise RuntimeError("not found or access denied")

    monkeypatch.setattr(adapter, "status", unreadable)
    monkeypatch.setattr(adapter, "create", lambda spec: pytest.fail("read failures must never create remotely"))
    with pytest.raises(ValueError, match="not found or access denied"):
        service.attach_job(**args)
    assert store.list_jobs(owner_id="alice", session_id="s") == []
    assert store.list_pending_notifications() == []
    monkeypatch.setattr(adapter, "status", lambda external_id: RemoteJobStatus("running", {}))
    job = service.attach_job(**args)
    monkeypatch.setattr(adapter, "status", unreadable)
    replay = service.attach_job(**args)
    assert replay["job_id"] == job["job_id"]
    assert replay["status"] == "running"
    assert "not found or access denied" in replay["error"]
    assert len(store.list_jobs(owner_id="alice", session_id="s")) == 1


@pytest.mark.parametrize("interrupted_status", ["submitting", "queued"])
def test_attach_job_recovers_interrupted_local_registration(tmp_path, monkeypatch, interrupted_status):
    adapter = _FakeAdapter()
    monkeypatch.setattr(adapter, "create", lambda spec: pytest.fail("recovery must never create remotely"))
    path = tmp_path / "jobs.db"
    store = RemoteJobStore(path)
    transition = store.transition_job

    def interrupt(job_id, status, **kwargs):
        if status == interrupted_status:
            raise RuntimeError("process interrupted")
        return transition(job_id, status, **kwargs)

    monkeypatch.setattr(store, "transition_job", interrupt)
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    args = {"owner_id": "alice", "session_id": "s", "provider": "bohr_batchjob", "external_id": "external-123"}
    with pytest.raises(RuntimeError, match="process interrupted"):
        service.attach_job(**args)
    incomplete, = store.list_jobs(owner_id="alice", session_id="s")
    assert incomplete["external_id"] is None
    restarted = RemoteJobService(RemoteJobStore(path), adapter_overrides={"bohr_batchjob": adapter})
    recovered = restarted.attach_job(**args)
    assert recovered["job_id"] == incomplete["job_id"]
    assert recovered["external_id"] == "external-123"
    assert recovered["status"] == "queued"
    assert len(store.list_jobs(owner_id="alice", session_id="s")) == 1


@pytest.mark.parametrize("external_id", ["", " ", None, 42, True])
def test_attach_job_requires_explicit_string_id_before_remote_io(tmp_path, monkeypatch, external_id):
    adapter = _FakeAdapter()
    monkeypatch.setattr(adapter, "status", lambda external_id: pytest.fail("invalid ID must not probe provider"))
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    with pytest.raises(ValueError, match="string external_id is required"):
        service.attach_job(owner_id="alice", session_id="s", provider="bohr_batchjob", external_id=external_id)
    assert store.list_jobs(owner_id="alice", session_id="s") == []


@pytest.mark.parametrize("initial", [True, False])
def test_terminal_probe_enqueues_notification_on_submit_or_refresh(tmp_path, initial):
    class Finished(_FakeAdapter):
        done = initial

        def status(self, external_id):
            return RemoteJobStatus(
                normalized_status="succeeded" if self.done else "running",
                snapshot={"provider_status": "finished" if self.done else "running"},
            )

    adapter = Finished()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="s", provider="e2b", idempotency_key="one", spec={},
    )
    adapter.done = True
    service.reconcile_job(job["job_id"])
    notification, = store.list_pending_notifications()
    assert notification["status"] == "succeeded"


def test_reconcile_inflight_submission_skips_provider(tmp_path):
    store = RemoteJobStore(tmp_path / "jobs.db")
    adapter = _FakeAdapter()
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = store.create_job(owner_id="alice", session_id="s", provider="e2b", idempotency_key="one")
    submitting = store.transition_job(job["job_id"], "submitting")
    assert service.reconcile_job(job["job_id"]) == submitting


def test_reconcile_preserves_background_command_and_poll_notifies(tmp_path):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="s", provider="e2b", idempotency_key="one", spec={},
    )
    started = service.start_job_command(job["job_id"], "simulation")
    service.reconcile_job(job["job_id"])
    assert store.get_job(job["job_id"])["snapshot"]["background_command"] == started["handle"]
    adapter.files[started["handle"]["exit_path"]] = b"1"
    result = service.poll_job_command(job["job_id"])
    assert result["exit_code"] == 1
    notification, = store.list_pending_notifications()
    assert notification["status"] == "failed"
    assert notification["kind"] == "command"
    assert store.get_job(job["job_id"])["status"] == "running"


def test_poll_after_monitor_consumes_completion_returns_durable_result(tmp_path):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="s", provider="e2b", idempotency_key="one", spec={},
    )
    started = service.start_job_command(job["job_id"], "simulation")
    adapter.files[started["handle"]["exit_path"]] = b"0"
    first = service.poll_job_command(job["job_id"])
    reattached = RemoteJobService(RemoteJobStore(store.path), adapter_overrides={"e2b": adapter})
    assert reattached.poll_job_command(job["job_id"]) == first
    assert len(store.list_notifications()) == 1


def test_empty_exit_marker_does_not_consume_command_or_notify_failure(tmp_path):
    adapter = _FakeAdapter()
    store = RemoteJobStore(tmp_path / "jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="s", provider="e2b", idempotency_key="one", spec={},
    )
    started = service.start_job_command(job["job_id"], "simulation")
    adapter.files[started["handle"]["exit_path"]] = b""
    with pytest.raises(RuntimeError, match="incomplete or invalid exit marker"):
        service.poll_job_command(job["job_id"])
    assert store.get_job(job["job_id"])["snapshot"]["background_command"] == started["handle"]
    assert store.list_notifications() == []
    adapter.files[started["handle"]["exit_path"]] = b"0"
    assert service.poll_job_command(job["job_id"])["exit_code"] == 0


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


def test_uncertain_submission_is_durable_and_never_automatically_retried(tmp_path) -> None:
    class _UncertainAdapter(_FakeAdapter):
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def create(self, spec):
            self.created_specs.append(spec)
            raise RemoteJobSubmissionUncertainError("Input upload failed after precreate; inspect the remote job.")

    adapter = _UncertainAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    kwargs = dict(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="batch-1", spec={},
    )

    job = service.submit_job(**kwargs)
    replay = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter}).submit_job(**kwargs)

    assert job["status"] == replay["status"] == "lost"
    assert replay["job_id"] == job["job_id"]
    assert job["external_id"] is None
    assert "after precreate" in replay["error"]
    assert replay["snapshot"]["provider_status"] == "submission_uncertain"
    assert len(adapter.created_specs) == 1


@pytest.mark.parametrize("outcome", ["unknown", "unreachable", "failed", "prepared"])
def test_batch_initial_probe_preserves_uncertainty_and_errors(tmp_path, outcome) -> None:
    class _BatchAdapter(_FakeAdapter):
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def status(self, external_id):
            if outcome == "unreachable":
                raise RuntimeError("probe unavailable")
            return RemoteJobStatus(
                normalized_status="failed" if outcome == "failed" else "queued" if outcome == "prepared" else None,
                snapshot={"status_name": outcome},
                error="queue timeout" if outcome == "failed" else None,
            )

    adapter = _BatchAdapter()
    service = RemoteJobService(
        RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"bohr_batchjob": adapter},
    )
    kwargs = dict(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="batch-1", spec={},
    )
    job = service.submit_job(**kwargs)
    assert job["status"] == ("failed" if outcome == "failed" else "queued")
    if outcome == "failed":
        assert job["error"] == "queue timeout"
    if outcome == "unreachable":
        assert "probe unavailable" in job["error"]
        assert job["snapshot"]["provider_status"] == "unreachable"
    replay = service.submit_job(**kwargs)
    assert replay["external_id"] == "sandbox-123"
    assert len(adapter.created_specs) == 1


def test_unconfirmed_cancellation_never_claims_termination(tmp_path) -> None:
    class _UnconfirmedAdapter(_FakeAdapter):
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def cancel(self, external_id):
            raise RuntimeError("Stop accepted but not confirmed; inspect the job before retrying.")

    service = RemoteJobService(
        RemoteJobStore(tmp_path / "remote-jobs.db"),
        adapter_overrides={"bohr_batchjob": _UnconfirmedAdapter()},
    )
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="batch-1", spec={},
    )
    result = service.terminate_job(job["job_id"])
    assert result["status"] == "lost"
    assert "not confirmed" in result["error"]
    assert result["external_id"] == "sandbox-123"


def test_legacy_records_are_not_replayed_converted_or_resubmitted(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store)
    legacy = store.create_job(
        owner_id="alice", session_id="session-1", node_id="relax", provider="bohr_job",
        idempotency_key="legacy", specification={},
    )
    store.transition_job(legacy["job_id"], "submitting")
    store.transition_job(legacy["job_id"], "succeeded", external_id="legacy-123")
    store.transition_job(legacy["job_id"], "collecting")
    store.transition_job(legacy["job_id"], "collected")

    with pytest.raises(RetiredProviderError, match="no longer supported"):
        service.submit_job(
            owner_id="alice", session_id="session-1", provider="bohr_job",
            idempotency_key="legacy", spec={},
        )
    with pytest.raises(RetiredProviderError, match="already tracks legacy job"):
        service.submit_job(
            owner_id="alice", session_id="session-1", node_id="relax", provider="bohr_batchjob",
            idempotency_key="new-key", spec={},
        )
    with pytest.raises(RetiredProviderError, match="no longer supported"):
        service.collect_job_outputs(legacy["job_id"], tmp_path / "out")
    with pytest.raises(RetiredProviderError, match="no longer supported"):
        service.terminate_job(legacy["job_id"])
    persisted = store.get_job(legacy["job_id"])
    assert persisted["external_id"] == "legacy-123"
    assert persisted["provider"] == "bohr_job"
    assert persisted["status"] == "collected"
    assert len(store.list_jobs(owner_id="alice", session_id="session-1")) == 1


def test_batch_collection_rejects_dangling_destination_symlink_before_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "matcreator.control_plane.providers.bohr_batchjob.run_bohr_json",
        lambda *args, **kwargs: pytest.fail("CLI must not run for an existing destination"),
    )
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="batch-1", specification={},
    )
    store.transition_job(job["job_id"], "submitting")
    store.transition_job(job["job_id"], "succeeded", external_id="batch-123")
    destination = tmp_path / "link"
    destination.symlink_to(tmp_path / "missing")
    adapter = BohrBatchJobAdapter()
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})

    result = service.collect_job_outputs(job["job_id"], destination)

    # A local collection failure must NOT durably fail a succeeded job.
    assert result["status"] == "succeeded"
    assert "must not exist" in result["error"]
    assert destination.is_symlink()
    assert not (tmp_path / "missing").exists()


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
        provider = "bohr_batchjob"
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
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_batchjob",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "name": "n", "machine_type": "c2", "image": "img", "command": "cmd"},
    )
    assert job["status"] == "queued"

    reconciled = service.reconcile_job(job["job_id"])
    assert reconciled["status"] == "succeeded"
    assert reconciled["snapshot"]["phase"] == "completed"


def test_collect_job_outputs_is_idempotent(tmp_path) -> None:
    class _BatchAdapter(_FakeAdapter):
        provider = "bohr_batchjob"
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
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_batchjob",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "name": "n", "machine_type": "c2", "image": "img", "command": "cmd"},
    )
    service.reconcile_job(job["job_id"])

    collected = service.collect_job_outputs(job["job_id"], tmp_path / "out")
    assert collected["status"] == "collected"
    assert len(collected["artifacts"]) == 1
    assert adapter.collect_calls == ["sandbox-123"]

    replay = service.collect_job_outputs(job["job_id"], tmp_path / "out")
    assert replay["status"] == "collected"
    assert adapter.collect_calls == ["sandbox-123"]


def test_failed_collection_leaves_job_succeeded_and_retryable(tmp_path) -> None:
    class _FlakyBatchAdapter(_FakeAdapter):
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

        def __init__(self) -> None:
            super().__init__()
            self.collect_calls: list[str] = []

        def status(self, external_id: str) -> RemoteJobStatus:
            return RemoteJobStatus(normalized_status="succeeded", snapshot={"phase": "completed"})

        def collect_outputs(self, external_id: str, destination_dir):
            self.collect_calls.append(external_id)
            if len(self.collect_calls) == 1:
                raise ValueError("Batch Job output destination must not exist; choose a new directory")
            return [{"source": external_id, "destination": str(destination_dir)}]

    adapter = _FlakyBatchAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"bohr_batchjob": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_batchjob",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "name": "n", "machine_type": "c2", "image": "img", "command": "cmd"},
    )
    service.reconcile_job(job["job_id"])

    failed_attempt = service.collect_job_outputs(job["job_id"], tmp_path / "occupied")
    assert failed_attempt["status"] == "succeeded"
    assert "must not exist" in failed_attempt["error"]

    retried = service.collect_job_outputs(job["job_id"], tmp_path / "fresh")
    assert retried["status"] == "collected"
    assert retried["error"] is None
    assert len(retried["artifacts"]) == 1
    assert adapter.collect_calls == ["sandbox-123", "sandbox-123"]


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
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

    adapter = _BatchAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), adapter_overrides={"bohr_batchjob": adapter})
    job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="session-1:node-1:1",
        spec={"project_id": 1, "name": "n", "machine_type": "c2", "image": "img", "command": "cmd"},
    )

    try:
        service.start_job_command(job["job_id"], "echo hi")
        assert False, "expected CapabilityError"
    except Exception as exc:
        assert "does not support 'interactive_exec'" in str(exc)
