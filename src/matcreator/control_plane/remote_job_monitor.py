"""Background reconciler for durable remote jobs."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .providers.base import provider_query_timeout_seconds
from .remote_job_service import RemoteJobService
from .remote_jobs import RemoteJobStore

logger = logging.getLogger(__name__)


class RemoteJobMonitor:
    """Probe active remote jobs of any registered provider with bounded retry backoff.

    Job records are durable; this monitor's due times are intentionally process
    local. On a restart its empty schedule reconciles every active job once.
    Each provider adapter declares its own ``poll_interval_seconds`` (see
    ``providers/base.py``), so a batch/HPC-style provider can poll far less
    often than an interactive sandbox with no change needed here — adding a
    provider is a pure plugin.
    """

    def __init__(
        self,
        store: RemoteJobStore,
        service: RemoteJobService,
        *,
        interval_seconds: float = 15,
        max_backoff_seconds: float = 300,
        provider_timeout_seconds: float | None = None,
    ) -> None:
        if interval_seconds <= 0 or max_backoff_seconds < interval_seconds:
            raise ValueError("invalid remote job monitor intervals")
        self.store = store
        self.service = service
        self.interval_seconds = interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.provider_timeout_seconds = provider_query_timeout_seconds(
            provider_timeout_seconds
        )
        self._next_due: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        # ``wait_for`` cannot stop a synchronous provider call already running
        # in a worker thread. Keep its Task so later rounds never start a
        # duplicate probe for the same durable job while that call is still
        # unwinding.
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
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

    def _base_interval(self, provider: str) -> float:
        """Return the owning adapter's preferred poll cadence for one job.

        Resolved through ``self.service.adapter_for`` so this honors the same
        adapter overrides (e.g. in tests) as every other service operation,
        instead of querying the global registry directly. Falls back to this
        monitor's own tick interval if the provider is unregistered (e.g. a
        record left over from a removed plugin), so a missing adapter never
        breaks reconciliation of other jobs.
        """
        try:
            return self.service.adapter_for(provider).poll_interval_seconds
        except KeyError:
            return self.interval_seconds

    def _schedule_backoff(self, job_id: str, base_interval: float) -> None:
        failures = self._failures.get(job_id, 0) + 1
        self._failures[job_id] = failures
        delay = min(
            base_interval * (2 ** (failures - 1)),
            self.max_backoff_seconds,
        )
        self._next_due[job_id] = time.monotonic() + delay

    def _record_timeout(
        self, job: dict[str, Any], base_interval: float
    ) -> dict[str, Any]:
        """Persist an audit observation without changing lifecycle state."""

        job_id = job["job_id"]
        timeout = self.provider_timeout_seconds
        message = f"provider reconciliation timed out after {timeout:g}s"
        latest = self.store.get_job(job_id) or job
        # A provider thread may have completed just after the outer timeout.
        # Never replace a real terminal error with the monitor's uncertainty.
        error = (
            message
            if latest.get("status") in {"queued", "running", "submitting", "resuming"}
            else latest.get("error")
        )
        try:
            updated = self.store.merge_observation(
                job_id,
                snapshot={
                    "provider_query": {
                        "operation": "reconcile",
                        "status": "timed_out",
                        "timeout_seconds": timeout,
                        "observed_at": time.time(),
                    }
                },
                error=error,
            )
        except Exception as exc:
            logger.warning(
                "Remote job monitor could not persist timeout for %s: %s",
                job_id,
                exc,
            )
            updated = latest
        self._schedule_backoff(job_id, base_interval)
        logger.warning("Remote job monitor timed out reconciling %s", job_id)
        return {**updated, "monitor_error": message}

    async def _reconcile_job(
        self, job: dict[str, Any], base_interval: float
    ) -> dict[str, Any]:
        job_id = job["job_id"]
        loop = asyncio.get_running_loop()
        task = self._inflight.get(job_id)
        if task is not None and task.get_loop() is not loop:
            # This only occurs when a monitor object is reused across closed
            # event loops (mostly small ``asyncio.run`` based tests). A closed
            # loop has already cancelled its pending Tasks.
            self._inflight.pop(job_id, None)
            task = None
        if task is None:
            task = asyncio.create_task(
                asyncio.to_thread(self.service.reconcile_job, job_id),
                name=f"remote-job-reconcile-{job_id}",
            )
            # Retrieving a late exception prevents an unobserved-Task warning;
            # the result remains available for the next due monitor round.
            task.add_done_callback(
                lambda completed: (
                    None if completed.cancelled() else completed.exception()
                )
            )
            self._inflight[job_id] = task
        try:
            updated = await asyncio.wait_for(
                asyncio.shield(task),
                timeout=self.provider_timeout_seconds,
            )
        except TimeoutError:
            return self._record_timeout(job, base_interval)
        except Exception as exc:
            # One malformed legacy record, missing adapter, or concurrent
            # revision must not terminate monitoring for every other job.
            self._schedule_backoff(job_id, base_interval)
            logger.warning("Remote job monitor could not reconcile %s: %s", job_id, exc)
            return {**job, "monitor_error": str(exc)}
        finally:
            if task.done():
                self._inflight.pop(job_id, None)

        if (updated.get("snapshot") or {}).get("provider_status") == "unreachable":
            self._schedule_backoff(job_id, base_interval)
        else:
            self._failures.pop(job_id, None)
            self._next_due[job_id] = time.monotonic() + base_interval
        return updated

    async def reconcile_once(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        active_ids: set[str] = set()
        due_jobs: list[tuple[dict[str, Any], float]] = []
        for job in self.store.list_active_jobs():
            job_id = job["job_id"]
            active_ids.add(job_id)
            if job["status"] not in {
                "queued",
                "running",
                "submitting",
                "resuming",
                "terminate_requested",
            }:
                continue
            if now < self._next_due.get(job_id, 0):
                continue
            base_interval = self._base_interval(job["provider"])
            due_jobs.append((job, base_interval))

        # All due jobs receive the same wall-clock timeout window. A hung
        # provider therefore delays only its own result, not every job after
        # it in a serial loop.
        updates = list(
            await asyncio.gather(
                *(
                    self._reconcile_job(job, base_interval)
                    for job, base_interval in due_jobs
                )
            )
        )

        stale_ids = set(self._next_due) - active_ids
        for job_id in stale_ids:
            self._next_due.pop(job_id, None)
            self._failures.pop(job_id, None)

        # A late provider result applies its own durable observation inside
        # ``RemoteJobService``. Once a job is no longer active, retain no
        # completed Task bookkeeping; a still-running Task remains referenced
        # solely to prevent duplicate probes until it exits.
        for job_id, task in list(self._inflight.items()):
            if job_id not in active_ids and task.done():
                self._inflight.pop(job_id, None)
        return updates
