from __future__ import annotations

import asyncio

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
        provider = "bohr_job"
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
    service = RemoteJobService(store, adapter_overrides={"e2b": e2b_adapter, "bohr_job": batch_adapter})
    e2b_job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-1:1",
        spec={"template": "t", "api_key": "k", "api_url": "u", "project_id": "p"},
    )
    batch_job = service.submit_job(
        owner_id="alice", session_id="session-1", provider="bohr_job",
        idempotency_key="session-1:node-2:1",
        spec={"project_id": 1, "job_name": "n", "machine_type": "c2", "image_address": "img", "command": "cmd"},
    )

    monitor = RemoteJobMonitor(store, service, interval_seconds=1)
    updates = asyncio.run(monitor.reconcile_once())

    reconciled_ids = {item["job_id"] for item in updates}
    assert reconciled_ids == {e2b_job["job_id"], batch_job["job_id"]}
    assert monitor._next_due[batch_job["job_id"]] > monitor._next_due[e2b_job["job_id"]]
