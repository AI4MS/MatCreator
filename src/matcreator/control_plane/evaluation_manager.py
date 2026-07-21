"""Concurrent in-process scheduling for durable evaluation campaigns."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from .benchmark_client import BenchmarkApiError
from .evaluation_service import BenchmarkExecutionClient, EvaluationService


logger = logging.getLogger(__name__)


class BenchmarkGradingClient(BenchmarkExecutionClient, Protocol):
    async def get_grading_job(self, job_id: str) -> dict[str, Any]: ...

    async def get_results(self, *, question_id: str, session_id: str) -> dict[str, Any]: ...


class EvaluationManager:
    """Launch queued attempts with global and campaign-level concurrency limits."""

    def __init__(self, *, max_concurrent_attempts: int = 4, poll_seconds: float = 2.0) -> None:
        if max_concurrent_attempts < 1:
            raise ValueError("max_concurrent_attempts must be positive")
        self._global_semaphore = asyncio.Semaphore(max_concurrent_attempts)
        self._poll_seconds = poll_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        campaign_id: str,
        service: EvaluationService,
        client: BenchmarkGradingClient,
    ) -> None:
        async with self._lock:
            existing = self._tasks.get(campaign_id)
            if existing and not existing.done():
                return
            self._tasks[campaign_id] = asyncio.create_task(
                self._run_campaign(campaign_id, service, client)
            )

    async def cancel_campaign(
        self,
        campaign_id: str,
        service: EvaluationService,
        *,
        cancel_managed_run: Callable[[str], Awaitable[None]],
    ) -> dict[str, Any]:
        campaign = service.store.get_campaign(campaign_id)
        if campaign is None:
            raise KeyError(f"Evaluation campaign '{campaign_id}' was not found")
        if campaign["status"] == "cancelling":
            return campaign
        if campaign["status"] not in {"starting", "active"}:
            raise ValueError("Only active evaluation campaigns can be cancelled")

        campaign = service.store.transition_campaign(campaign_id, "cancelling")
        for attempt in service.store.list_attempts(campaign_id):
            status = attempt["status"]
            if status == "queued":
                service.store.transition_attempt(
                    attempt["attempt_id"], "cancelled", error="Evaluation cancelled before runtime start."
                )
            elif status == "runtime_starting":
                service.store.transition_attempt(
                    attempt["attempt_id"], "cancelled", error="Evaluation cancelled before agent runtime start."
                )
            elif status == "running":
                service.store.transition_attempt(attempt["attempt_id"], "cancelling")
                if attempt.get("managed_run_id"):
                    await cancel_managed_run(attempt["managed_run_id"])
        return campaign

    async def recover_missing_result_campaign(
        self,
        campaign_id: str,
        service: EvaluationService,
        client: BenchmarkGradingClient,
    ) -> bool:
        """Recover only completed remote grades failed by the old result endpoint."""
        campaign = service.store.get_campaign(campaign_id)
        if campaign is None or campaign["status"] != "failed":
            return False
        attempts = service.store.list_attempts(campaign_id)
        if not attempts:
            return False
        recoverable = "Could not retrieve benchmark grading result: Benchmark grading job completed"
        candidates = [
            attempt
            for attempt in attempts
            if attempt["status"] == "failed" and str(attempt.get("error") or "").startswith(recoverable)
        ]
        if not candidates:
            return False
        for attempt in candidates:
            job = await client.get_grading_job(attempt["grading_job_id"])
            if job.get("status") != "completed":
                continue
            result = await client.get_results(
                question_id=attempt["question_id"],
                session_id=campaign["benchmark_session_id"],
            )
            service.store.recover_missing_result_attempt(attempt["attempt_id"], result)
        if all(attempt["status"] == "completed" for attempt in service.store.list_attempts(campaign_id)):
            service.store.recover_completed_campaign(campaign_id)
        return True

    async def _run_campaign(
        self,
        campaign_id: str,
        service: EvaluationService,
        client: BenchmarkGradingClient,
    ) -> None:
        campaign = service.store.get_campaign(campaign_id)
        if campaign is None or campaign["status"] not in {"active", "cancelling"}:
            return
        campaign_limit = int(campaign["configuration"]["max_parallelism"])
        campaign_semaphore = asyncio.Semaphore(campaign_limit)

        async def execute(attempt_id: str) -> None:
            async with self._global_semaphore, campaign_semaphore:
                await service.execute_attempt(
                    campaign_id=campaign_id,
                    attempt_id=attempt_id,
                    client=client,
                )

        queued = [attempt["attempt_id"] for attempt in service.store.list_attempts(campaign_id) if attempt["status"] == "queued"]
        await asyncio.gather(*(execute(attempt_id) for attempt_id in queued), return_exceptions=True)
        while True:
            grading = [attempt for attempt in service.store.list_attempts(campaign_id) if attempt["status"] == "grading"]
            if not grading:
                break
            results = await asyncio.gather(
                *(self._reconcile_attempt(service, campaign, attempt, client) for attempt in grading),
                return_exceptions=True,
            )
            for attempt, result in zip(grading, results, strict=True):
                if isinstance(result, Exception):
                    logger.exception(
                        "Evaluation grading reconciliation failed: campaign=%s attempt=%s question=%s",
                        campaign_id,
                        attempt["attempt_id"],
                        attempt["question_id"],
                        exc_info=result,
                    )
                    service.store.transition_attempt(
                        attempt["attempt_id"],
                        "failed",
                        error=f"Could not retrieve benchmark grading result: {result}",
                    )
            if any(attempt["status"] == "grading" for attempt in service.store.list_attempts(campaign_id)):
                await asyncio.sleep(self._poll_seconds)

        attempts = service.store.list_attempts(campaign_id)
        current_campaign = service.store.get_campaign(campaign_id)
        if current_campaign and current_campaign["status"] == "cancelling":
            if all(attempt["status"] in {"completed", "failed", "cancelled", "timed_out", "interrupted"} for attempt in attempts):
                service.store.transition_campaign(campaign_id, "cancelled")
            return
        status = "completed" if attempts and all(attempt["status"] == "completed" for attempt in attempts) else "failed"
        service.store.transition_campaign(campaign_id, status)

    async def _reconcile_attempt(
        self,
        service: EvaluationService,
        campaign: dict[str, Any],
        attempt: dict[str, Any],
        client: BenchmarkGradingClient,
    ) -> None:
        job_id = attempt.get("grading_job_id")
        if not job_id:
            service.store.transition_attempt(attempt["attempt_id"], "failed", error="Benchmark submission returned no grading job")
            return
        job = await client.get_grading_job(job_id)
        if job.get("status") == "failed":
            service.store.transition_attempt(attempt["attempt_id"], "failed", error=str(job.get("error") or "Grading failed"))
        elif job.get("status") == "completed":
            try:
                result = await client.get_results(
                    question_id=attempt["question_id"],
                    session_id=campaign["benchmark_session_id"],
                )
            except BenchmarkApiError as exc:
                if exc.status_code == 404:
                    raise RuntimeError(
                        "Benchmark grading job completed but the result record is missing. "
                        "Check the benchmark server logs for the grading job."
                    ) from exc
                raise
            service.store.transition_attempt(attempt["attempt_id"], "completed", result=result)

    def active_campaign_ids(self) -> set[str]:
        return {campaign_id for campaign_id, task in self._tasks.items() if not task.done()}