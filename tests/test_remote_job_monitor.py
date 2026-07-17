from __future__ import annotations

import asyncio

from matcreator.control_plane.remote_job_monitor import RemoteJobMonitor
from matcreator.control_plane.remote_job_service import E2BConnectionConfig, RemoteJobService
from matcreator.control_plane.remote_jobs import RemoteJobStore


class _FakeE2BAdapter:
    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.probes: list[str] = []

    def create(self, _spec):
        return "sandbox-123"

    def probe(self, sandbox_id: str):
        self.probes.append(sandbox_id)
        if not self.reachable:
            raise RuntimeError("sandbox unavailable")
        return {"provider_status": "reachable", "sandbox_id": sandbox_id}


def _create_running_job(tmp_path, adapter: _FakeE2BAdapter):
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, e2b_adapter=adapter)
    job = service.submit_e2b(
        owner_id="alice",
        session_id="session-1",
        idempotency_key="session-1:node-1:1",
        connection=E2BConnectionConfig(
            api_key="secret",
            api_url="https://e2b.example",
            project_id="project-42",
            template="doc-compiler",
        ),
    )
    return store, service, job


def test_monitor_reconciles_running_job_after_restart(tmp_path) -> None:
    adapter = _FakeE2BAdapter()
    store, service, job = _create_running_job(tmp_path, adapter)
    monitor = RemoteJobMonitor(store, service, interval_seconds=1)

    updates = asyncio.run(monitor.reconcile_once())

    assert [item["job_id"] for item in updates] == [job["job_id"]]
    assert adapter.probes == ["sandbox-123"]
    assert store.get_job(job["job_id"])["snapshot"]["provider_status"] == "reachable"


def test_monitor_backs_off_unreachable_job_and_skips_paused_jobs(tmp_path) -> None:
    adapter = _FakeE2BAdapter(reachable=False)
    store, service, job = _create_running_job(tmp_path, adapter)
    monitor = RemoteJobMonitor(store, service, interval_seconds=1, max_backoff_seconds=4)

    first = asyncio.run(monitor.reconcile_once())
    second = asyncio.run(monitor.reconcile_once())

    assert first[0]["snapshot"]["provider_status"] == "unreachable"
    assert second == []
    assert adapter.probes == ["sandbox-123"]
    paused = store.transition_job(job["job_id"], "pause_requested")
    store.transition_job(job["job_id"], "paused", expected_revision=paused["state_revision"])
    monitor._next_due.clear()
    assert asyncio.run(monitor.reconcile_once()) == []