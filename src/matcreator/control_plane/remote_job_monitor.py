"""Background reconciler for durable remote jobs."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .remote_job_service import RemoteJobService
from .remote_jobs import RemoteJobStore


class RemoteJobMonitor:
    """Probe active E2B sandboxes with bounded retry backoff.

    Job records are durable; this monitor's due times are intentionally process
    local. On a restart its empty schedule reconciles every active sandbox once.
    """

    def __init__(
        self,
        store: RemoteJobStore,
        service: RemoteJobService,
        *,
        interval_seconds: float = 15,
        max_backoff_seconds: float = 300,
    ) -> None:
        if interval_seconds <= 0 or max_backoff_seconds < interval_seconds:
            raise ValueError("invalid remote job monitor intervals")
        self.store = store
        self.service = service
        self.interval_seconds = interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._next_due: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.reconcile_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()

    async def reconcile_once(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        updates: list[dict[str, Any]] = []
        active_ids: set[str] = set()
        for job in self.store.list_active_jobs(provider="e2b"):
            job_id = job["job_id"]
            active_ids.add(job_id)
            if job["status"] not in {"queued", "running", "submitting", "resuming"}:
                continue
            if now < self._next_due.get(job_id, 0):
                continue
            updated = await asyncio.to_thread(self.service.reconcile_e2b, job_id)
            updates.append(updated)
            if updated["snapshot"].get("provider_status") == "unreachable":
                failures = self._failures.get(job_id, 0) + 1
                self._failures[job_id] = failures
                delay = min(self.interval_seconds * (2 ** (failures - 1)), self.max_backoff_seconds)
            else:
                self._failures.pop(job_id, None)
                delay = self.interval_seconds
            self._next_due[job_id] = time.monotonic() + delay

        stale_ids = set(self._next_due) - active_ids
        for job_id in stale_ids:
            self._next_due.pop(job_id, None)
            self._failures.pop(job_id, None)
        return updates