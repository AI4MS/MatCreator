"""Independent remote-job probes and durable completion notification delivery."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .remote_job_service import RemoteJobService
from .remote_jobs import RemoteJobStore

logger = logging.getLogger(__name__)


class RemoteJobMonitor:
    """Reconcile jobs without serializing unrelated probes or wakeup callbacks.

    Provider calls run in tracked threads and must use adapter-level timeouts.
    Shutdown joins them rather than abandoning threads that can still write state.
    Notification leases survive restart; the callback must idempotently schedule
    a run keyed by notification_id to cover a crash between scheduling and ack.
    """

    def __init__(
        self,
        store: RemoteJobStore,
        service: RemoteJobService,
        *,
        interval_seconds: float = 15,
        max_backoff_seconds: float = 300,
        max_concurrency: int = 4,
        callback_timeout_seconds: float = 30,
        on_job_finished: Callable[[dict[str, Any]], Awaitable[str | None]] | None = None,
    ) -> None:
        if interval_seconds <= 0 or max_backoff_seconds < interval_seconds:
            raise ValueError("invalid remote job monitor intervals")
        if max_concurrency < 1 or callback_timeout_seconds <= 0:
            raise ValueError("invalid remote job monitor concurrency or timeout")
        self.store = store
        self.service = service
        self.interval_seconds = interval_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.max_concurrency = max_concurrency
        self.callback_timeout_seconds = callback_timeout_seconds
        self.on_job_finished = on_job_finished
        self._next_due: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._stop = asyncio.Event()
        self._probes: dict[str, asyncio.Task] = {}
        self._deliveries: dict[str, asyncio.Task] = {}
        self._workers: set[asyncio.Task] = set()

    async def run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self._schedule_probes()
                    self._schedule_deliveries()
                except Exception:
                    logger.exception("Remote job monitor scheduling failed; retrying next tick")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_seconds)
                except TimeoutError:
                    pass
        finally:
            self._stop.set()
            # Cancelling asyncio.to_thread cannot stop the underlying thread.
            # Shield and join workers so shutdown cannot leave untracked writes.
            tasks = list(self._probes.values()) + list(self._deliveries.values())
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._workers:
                await asyncio.gather(*self._workers, return_exceptions=True)

    def stop(self) -> None:
        self._stop.set()

    def _base_interval(self, provider: str) -> float:
        try:
            return self.service.adapter_for(provider).poll_interval_seconds
        except KeyError:
            return self.interval_seconds

    def _delay(self, base: float, failures: int) -> float:
        return min(base * (2 ** min(max(failures - 1, 0), 20)), self.max_backoff_seconds)

    def _schedule_probes(self) -> list[asyncio.Task]:
        self._probes = {key: task for key, task in self._probes.items() if not task.done()}
        active = self.store.list_active_jobs()
        active_ids = {job["job_id"] for job in active}
        for job_id in set(self._next_due) - active_ids:
            self._next_due.pop(job_id, None)
            self._failures.pop(job_id, None)
        started = []
        for job in active:
            if len(self._probes) >= self.max_concurrency:
                break
            job_id = job["job_id"]
            if (
                job_id in self._probes or not job["external_id"]
                or job["status"] not in {"queued", "running", "submitting", "resuming"}
                or time.monotonic() < self._next_due.get(job_id, 0)
            ):
                continue
            task = asyncio.create_task(self._probe(job))
            self._probes[job_id] = task
            started.append(task)
        return started

    def _reconcile_and_poll(self, job_id: str) -> dict[str, Any]:
        updated = self.service.reconcile_job(job_id)
        if (
            updated["status"] in {"queued", "running", "resuming"}
            and updated["snapshot"].get("background_command")
            and updated["snapshot"].get("provider_status") not in {"unreachable", "unsupported"}
        ):
            self.service.poll_job_command(job_id)
            updated = self.store.get_job(job_id) or updated
        return updated

    async def _probe(self, job: dict[str, Any]) -> dict[str, Any] | None:
        job_id = job["job_id"]
        base = self.interval_seconds
        try:
            base = self._base_interval(job["provider"])
            worker = asyncio.create_task(asyncio.to_thread(self._reconcile_and_poll, job_id))
            self._workers.add(worker)
            try:
                updated = await asyncio.shield(worker)
            finally:
                if worker.done():
                    self._workers.discard(worker)
            if updated["snapshot"].get("provider_status") in {"unreachable", "unsupported"}:
                self._failures[job_id] = self._failures.get(job_id, 0) + 1
                logger.warning("Remote job probe unavailable job_id=%s: %s", job_id, updated.get("error"))
                delay = self._delay(base, self._failures[job_id])
            else:
                self._failures.pop(job_id, None)
                delay = base
            self._next_due[job_id] = time.monotonic() + delay
            if not self._stop.is_set():
                self._schedule_deliveries()
            return updated
        except Exception:
            logger.exception("Remote job probe failed job_id=%s; retrying with backoff", job_id)
            self._failures[job_id] = self._failures.get(job_id, 0) + 1
            self._next_due[job_id] = time.monotonic() + self._delay(base, self._failures[job_id])
            return None

    def _schedule_deliveries(self) -> list[asyncio.Task]:
        self._deliveries = {key: task for key, task in self._deliveries.items() if not task.done()}
        if self.on_job_finished is None or self._stop.is_set():
            return []
        started = []
        for notification in self.store.list_pending_notifications():
            if len(self._deliveries) >= self.max_concurrency:
                break
            notification_id = notification["notification_id"]
            if notification_id in self._deliveries:
                continue
            task = asyncio.create_task(self._deliver(notification_id))
            self._deliveries[notification_id] = task
            started.append(task)
        return started

    async def _deliver(self, notification_id: str) -> None:
        notification = None
        try:
            notification = self.store.claim_notification(
                notification_id, lease_seconds=self.callback_timeout_seconds + 30,
            )
            if notification is None:
                return
            callback = self.on_job_finished
            if callback is None:
                self.store.defer_notification(notification_id, notification["claim_token"])
                return
            run_id = await asyncio.wait_for(callback(notification), timeout=self.callback_timeout_seconds)
            if run_id is None:
                self.store.defer_notification(
                    notification_id, notification["claim_token"], delay_seconds=self.interval_seconds,
                )
            else:
                if not isinstance(run_id, str) or not run_id:
                    raise ValueError("on_job_finished must return a nonempty run_id or None")
                self.store.finish_notification(notification_id, notification["claim_token"], run_id)
        except Exception as exc:
            logger.exception("Remote job notification failed notification_id=%s job_id=%s",
                             notification_id, notification and notification["job_id"])
            if notification is not None:
                try:
                    self.store.fail_notification(
                        notification_id, notification["claim_token"], f"{type(exc).__name__}: {exc}",
                        delay_seconds=self._delay(self.interval_seconds, notification["failures"] + 1),
                    )
                except Exception:
                    logger.exception("Could not persist notification failure notification_id=%s", notification_id)

    async def reconcile_once(self) -> list[dict[str, Any]]:
        """Wait for this bounded batch; the long-running loop never waits on a fleet."""
        tasks = self._schedule_probes()
        self._schedule_deliveries()
        results = await asyncio.gather(*tasks)
        self._schedule_deliveries()
        if self._deliveries:
            await asyncio.gather(*self._deliveries.values())
        return [result for result in results if result is not None]
