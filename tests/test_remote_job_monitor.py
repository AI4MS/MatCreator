from __future__ import annotations

import asyncio
import threading
import time

import pytest

from matcreator.control_plane.providers.base import (
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobStatus,
)
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


def test_one_reconcile_exception_does_not_stop_other_jobs(tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, first = _create_running_job(tmp_path, adapter)
    second = service.submit_job(
        owner_id="alice", session_id="session-1", provider="e2b",
        idempotency_key="session-1:node-2:1",
        spec={"template": "t", "api_key": "k", "api_url": "u", "project_id": "p"},
    )
    original = service.reconcile_job

    def flaky(job_id: str):
        if job_id == first["job_id"]:
            raise RuntimeError("concurrent revision changed")
        return original(job_id)

    service.reconcile_job = flaky  # type: ignore[method-assign]
    monitor = RemoteJobMonitor(store, service, interval_seconds=1)

    updates = asyncio.run(monitor.reconcile_once())

    assert {item["job_id"] for item in updates} == {first["job_id"], second["job_id"]}
    failed = next(item for item in updates if item["job_id"] == first["job_id"])
    assert failed["monitor_error"] == "concurrent revision changed"
    assert first["job_id"] in monitor._next_due
    assert second["job_id"] in monitor._next_due
    assert adapter.probes == ["sandbox-123", "sandbox-123", "sandbox-123"]


def test_provider_timeout_configuration_is_finite(monkeypatch, tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, _ = _create_running_job(tmp_path, adapter)
    monkeypatch.setenv("MATCREATOR_REMOTE_PROVIDER_QUERY_TIMEOUT_SECONDS", "0.25")

    monitor = RemoteJobMonitor(store, service, interval_seconds=1)

    assert monitor.provider_timeout_seconds == 0.25
    for invalid in ("0", "-1", "nan", "inf", "not-a-number"):
        monkeypatch.setenv(
            "MATCREATOR_REMOTE_PROVIDER_QUERY_TIMEOUT_SECONDS", invalid
        )
        with pytest.raises(ValueError, match="positive finite"):
            RemoteJobMonitor(store, service, interval_seconds=1)


def test_hung_job_times_out_without_blocking_or_duplicate_probe(tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, first = _create_running_job(tmp_path, adapter)
    second = service.submit_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-2:1",
        spec={"template": "t", "api_key": "k", "api_url": "u", "project_id": "p"},
    )
    release = threading.Event()
    calls: list[str] = []
    fast_completed = threading.Event()

    def reconcile(job_id: str):
        calls.append(job_id)
        if job_id == first["job_id"]:
            release.wait(timeout=2)
            return store.get_job(job_id)
        fast_completed.set()
        return store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "fast_probe": True},
            error=None,
        )

    service.reconcile_job = reconcile  # type: ignore[method-assign]
    monitor = RemoteJobMonitor(
        store,
        service,
        interval_seconds=1,
        provider_timeout_seconds=0.05,
    )

    async def exercise() -> tuple[list[dict], float]:
        started = time.monotonic()
        try:
            updates = await monitor.reconcile_once()
            elapsed = time.monotonic() - started
            assert fast_completed.is_set()
            assert calls.count(first["job_id"]) == 1
            assert calls.count(second["job_id"]) == 1

            # Force the timed-out job due while its original thread is still
            # blocked. The same in-flight Task is awaited; no second provider
            # call is launched.
            monitor._next_due[first["job_id"]] = 0
            await monitor.reconcile_once()
            assert calls.count(first["job_id"]) == 1
            return updates, elapsed
        finally:
            release.set()
            await asyncio.sleep(0.05)

    updates, elapsed = asyncio.run(exercise())

    assert elapsed < 0.25
    by_id = {item["job_id"]: item for item in updates}
    assert by_id[second["job_id"]]["snapshot"]["fast_probe"] is True
    assert "timed out" in by_id[first["job_id"]]["monitor_error"]
    persisted = store.get_job(first["job_id"])
    assert persisted["status"] == "running"
    provider_query = persisted["snapshot"]["provider_query"]
    assert provider_query["operation"] == "reconcile"
    assert provider_query["status"] == "timed_out"
    assert provider_query["timeout_seconds"] == 0.05
    assert provider_query["observed_at"] > 0
    assert "timed out" in persisted["error"]
    assert store.list_events(first["job_id"])[-1]["event_type"] == "observed"
    assert all(
        event["payload"].get("to") not in {"failed", "lost", "terminated"}
        for event in store.list_events(first["job_id"])
    )


def test_monitor_recovers_a_stale_terminate_request(tmp_path) -> None:
    adapter = _FakeAdapter()
    store, service, running = _create_running_job(tmp_path, adapter)
    cancelled: list[str] = []
    adapter.cancel = lambda external_id: cancelled.append(external_id)  # type: ignore[method-assign]
    requested = store.transition_job(
        running["job_id"],
        "terminate_requested",
        snapshot={
            **running["snapshot"],
            "termination_control": {"status": "cancel_in_flight", "claimed_at": 0},
        },
    )
    monitor = RemoteJobMonitor(
        store,
        service,
        interval_seconds=1,
        provider_timeout_seconds=0.5,
    )

    updates = asyncio.run(monitor.reconcile_once())

    assert [update["job_id"] for update in updates] == [requested["job_id"]]
    assert updates[0]["status"] == "terminated"
    assert cancelled == ["sandbox-123"]
