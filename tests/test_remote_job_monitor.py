from __future__ import annotations

import asyncio
import threading

from matcreator.control_plane.providers.base import RemoteJobAdapter, RemoteJobCapability, RemoteJobStatus
from matcreator.control_plane.remote_job_monitor import RemoteJobMonitor
from matcreator.control_plane.remote_job_service import RemoteJobService
from matcreator.control_plane.remote_jobs import RemoteJobStore


class _FakeAdapter(RemoteJobAdapter):
    provider = "e2b"
    capabilities = frozenset({RemoteJobCapability.PAUSE})
    poll_interval_seconds = 1.0

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.probes: list[str] = []

    def create(self, spec: dict) -> str:
        return "sandbox-123"

    def status(self, external_id: str) -> RemoteJobStatus:
        self.probes.append(external_id)
        if not self.reachable:
            raise RuntimeError("sandbox unavailable")
        return RemoteJobStatus(normalized_status=None, snapshot={"provider_status": "reachable", "sandbox_id": external_id})

    def cancel(self, external_id: str) -> None:
        pass

    def pause(self, external_id: str) -> None:
        pass


def _create_running_job(tmp_path, adapter: _FakeAdapter):
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": adapter})
    job = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec={
            "template": "doc-compiler",
            "api_key": "secret",
            "api_url": "https://e2b.example",
            "project_id": "project-42",
        },
    )
    return store, service, job


def test_monitor_reconciles_running_job_after_restart(tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, job = _create_running_job(tmp_path, adapter)
    monitor = RemoteJobMonitor(store, service, interval_seconds=1)

    updates = asyncio.run(monitor.reconcile_once())

    assert [item["job_id"] for item in updates] == [job["job_id"]]
    # One status() call happens inside submit_job itself (initial probe), one
    # more from the explicit reconcile_once() call above.
    assert adapter.probes == ["sandbox-123", "sandbox-123"]
    assert store.get_job(job["job_id"])["snapshot"]["provider_status"] == "reachable"


def test_monitor_backs_off_unreachable_job_and_skips_paused_jobs(tmp_path) -> None:
    adapter = _FakeAdapter(reachable=False)
    store, service, job = _create_running_job(tmp_path, adapter)
    monitor = RemoteJobMonitor(store, service, interval_seconds=1, max_backoff_seconds=4)

    first = asyncio.run(monitor.reconcile_once())
    second = asyncio.run(monitor.reconcile_once())

    assert first[0]["snapshot"]["provider_status"] == "unreachable"
    assert second == []
    paused = store.transition_job(job["job_id"], "pause_requested")
    store.transition_job(job["job_id"], "paused", expected_revision=paused["state_revision"])
    monitor._next_due.clear()
    assert asyncio.run(monitor.reconcile_once()) == []


def test_monitor_reconciles_jobs_across_multiple_providers(tmp_path) -> None:
    """A batch-style provider with a longer poll interval is reconciled the
    same way as an interactive one — the monitor never branches on provider
    name, only on each adapter's declared poll_interval_seconds."""

    class _BatchAdapter(RemoteJobAdapter):
        provider = "bohr_batchjob"
        capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})
        poll_interval_seconds = 60.0

        def __init__(self) -> None:
            self.probes: list[str] = []

        def create(self, spec: dict) -> str:
            return "bohr-1"

        def status(self, external_id: str) -> RemoteJobStatus:
            self.probes.append(external_id)
            return RemoteJobStatus(normalized_status=None, snapshot={"phase": "running"})

        def cancel(self, external_id: str) -> None:
            pass

    e2b_adapter = _FakeAdapter()
    batch_adapter = _BatchAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, adapter_overrides={"e2b": e2b_adapter, "bohr_batchjob": batch_adapter})
    e2b_job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec={"template": "t", "api_key": "k", "api_url": "u", "project_id": "p"},
    )
    batch_job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="bohr_batchjob",
        idempotency_key="session-1:node-2:1",
        spec={"project_id": 1, "name": "n", "machine_type": "c2", "image": "img", "command": "cmd"},
    )

    monitor = RemoteJobMonitor(store, service, interval_seconds=1)
    updates = asyncio.run(monitor.reconcile_once())

    reconciled_ids = {item["job_id"] for item in updates}
    assert reconciled_ids == {e2b_job["job_id"], batch_job["job_id"]}
    assert monitor._next_due[batch_job["job_id"]] > monitor._next_due[e2b_job["job_id"]]


def test_retired_provider_does_not_stop_monitoring_other_jobs(tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, active = _create_running_job(tmp_path, adapter)
    legacy = store.create_job(
        owner_id="alice", session_id="session-1", provider="bohr_job",
        idempotency_key="legacy", specification={},
    )
    store.transition_job(legacy["job_id"], "submitting")
    store.transition_job(legacy["job_id"], "running", external_id="old-numeric-id")
    monitor = RemoteJobMonitor(store, service, interval_seconds=1)

    updates = asyncio.run(monitor.reconcile_once())

    assert {job["job_id"] for job in updates} == {active["job_id"], legacy["job_id"]}
    persisted = store.get_job(legacy["job_id"])
    assert persisted["status"] == "running"
    assert persisted["external_id"] == "old-numeric-id"
    assert persisted["snapshot"]["provider_supported"] is False
    assert "no longer supported" in persisted["error"]
    assert monitor._failures[legacy["job_id"]] == 1
    assert adapter.probes == ["sandbox-123", "sandbox-123"]


def test_monitor_busy_defers_then_restart_delivers_once(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("matcreator.control_plane.remote_jobs.time.time", lambda: clock[0])
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    store.transition_job(job["job_id"], "succeeded")
    seen = []

    async def busy(notification):
        seen.append(notification["notification_id"])
        return None

    asyncio.run(RemoteJobMonitor(store, service, interval_seconds=1, on_job_finished=busy).reconcile_once())
    assert store.list_notifications()[0]["delivery_status"] == "pending"
    assert store.list_pending_notifications() == []
    clock[0] += 2

    async def accepted(notification):
        seen.append(notification["notification_id"])
        return "run-123"

    monitor = RemoteJobMonitor(RemoteJobStore(store.path), service, on_job_finished=accepted)
    asyncio.run(monitor.reconcile_once())
    asyncio.run(monitor.reconcile_once())
    assert len(seen) == 2
    assert seen[0] == seen[1]
    assert store.list_notifications()[0]["run_id"] == "run-123"


def test_monitor_callback_failure_backoff_is_durable(tmp_path):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    store.transition_job(job["job_id"], "failed")

    async def broken(notification):
        raise RuntimeError("callback unavailable")

    monitor = RemoteJobMonitor(store, service, on_job_finished=broken)
    asyncio.run(monitor.reconcile_once())
    asyncio.run(monitor.reconcile_once())
    notification, = store.list_notifications()
    assert notification["attempts"] == 1
    assert notification["failures"] == 1
    assert "callback unavailable" in notification["last_error"]


def test_monitor_one_probe_error_does_not_abort_other_jobs(tmp_path, monkeypatch):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    second = store.create_job(owner_id="bob", session_id="s", provider="e2b", idempotency_key="other")
    store.transition_job(second["job_id"], "submitting")
    store.transition_job(second["job_id"], "running", external_id="other")
    reconcile = service.reconcile_job

    def probe(job_id):
        if job_id == job["job_id"]:
            raise RuntimeError("revision race")
        return reconcile(job_id)

    monkeypatch.setattr(service, "reconcile_job", probe)
    monitor = RemoteJobMonitor(store, service)
    updates = asyncio.run(monitor.reconcile_once())
    assert [item["job_id"] for item in updates] == [second["job_id"]]
    assert monitor._failures[job["job_id"]] == 1


def test_running_monitor_delivers_other_job_while_probe_blocked_and_joins_shutdown(tmp_path, monkeypatch):
    store, service, slow = _create_running_job(tmp_path, _FakeAdapter())
    fast = store.create_job(owner_id="bob", session_id="s", provider="e2b", idempotency_key="other")
    store.transition_job(fast["job_id"], "submitting")
    store.transition_job(fast["job_id"], "running", external_id="other")
    release = threading.Event()
    started = threading.Event()

    def probe(job_id):
        if job_id == slow["job_id"]:
            started.set()
            assert release.wait(timeout=5)
            return store.get_job(job_id)
        return store.transition_job(job_id, "succeeded")

    monkeypatch.setattr(service, "reconcile_job", probe)

    async def scenario():
        delivered = asyncio.Event()

        async def accepted(notification):
            assert notification["job_id"] == fast["job_id"]
            delivered.set()
            return "run"

        monitor = RemoteJobMonitor(store, service, interval_seconds=0.01, on_job_finished=accepted)
        task = asyncio.create_task(monitor.run())
        try:
            await asyncio.wait_for(delivered.wait(), timeout=2)
            assert started.is_set()
            assert not release.is_set()
            monitor.stop()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
            monitor.stop()
            await asyncio.wait_for(task, timeout=2)
        assert not monitor._workers
        assert all(task.done() for task in monitor._probes.values())

    asyncio.run(scenario())


def test_callback_timeout_does_not_block_polling(tmp_path):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    store.transition_job(job["job_id"], "failed")

    async def blocked(notification):
        await asyncio.Event().wait()

    monitor = RemoteJobMonitor(store, service, callback_timeout_seconds=0.01, on_job_finished=blocked)
    asyncio.run(monitor.reconcile_once())
    notification, = store.list_notifications()
    assert "TimeoutError" in notification["last_error"]
    assert notification["delivery_status"] == "pending"


def test_monitor_skips_inflight_submission(tmp_path):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    inflight = store.create_job(owner_id="bob", session_id="s", provider="e2b", idempotency_key="other")
    store.transition_job(inflight["job_id"], "submitting")
    monitor = RemoteJobMonitor(store, service)
    updates = asyncio.run(monitor.reconcile_once())
    assert [item["job_id"] for item in updates] == [job["job_id"]]


def test_monitors_share_lease_without_duplicate_callback(tmp_path):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    store.transition_job(job["job_id"], "succeeded")
    calls = []

    async def callback(notification):
        calls.append(notification["notification_id"])
        await asyncio.sleep(0.01)
        return "one-run"

    async def scenario():
        first = RemoteJobMonitor(store, service, on_job_finished=callback)
        second = RemoteJobMonitor(RemoteJobStore(store.path), service, on_job_finished=callback)
        await asyncio.gather(first.reconcile_once(), second.reconcile_once())

    asyncio.run(scenario())
    assert len(calls) == 1


def test_monitor_polls_command_after_lifecycle_probe(tmp_path, monkeypatch):
    store, service, job = _create_running_job(tmp_path, _FakeAdapter())
    handle = {"exit_path": "marker.exit", "log_path": "marker.log", "started_at": 1}
    store.merge_observation(job["job_id"], snapshot={"background_command": handle})
    seen = []

    def poll(job_id):
        assert store.get_job(job_id)["snapshot"]["background_command"] == handle
        store.record_command_completion(job_id, handle=handle, result={"exit_code": 0})

    async def callback(notification):
        seen.append(notification)
        return "command-run"

    monkeypatch.setattr(service, "poll_job_command", poll)
    monitor = RemoteJobMonitor(store, service, on_job_finished=callback)
    asyncio.run(monitor.reconcile_once())
    assert seen[0]["kind"] == "command"
    assert store.get_job(job["job_id"])["status"] == "running"
