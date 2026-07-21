from __future__ import annotations

import asyncio
from pathlib import Path

from matcreator.control_plane.evaluation_manager import EvaluationManager
from matcreator.control_plane.evaluation_runtime import RuntimeOutcome
from matcreator.control_plane.evaluation_service import EvaluationService
from matcreator.control_plane.evaluations import EvaluationStore


def test_manager_runs_queued_attempts_and_collects_grades(tmp_path) -> None:
    class FakeLauncher:
        async def run(self, spec):
            (spec.workspace / "result.txt").write_text(spec.session_id, encoding="utf-8")
            return RuntimeOutcome(0, "", "", 0.01, {"answer": "done", "num_turns": 1})

    class FakeClient:
        async def create_session(self, model_name: str) -> dict:
            return {"session_id": "bench-session"}

        async def create_run(self, session_id: str, selection: dict) -> dict:
            return {"run_id": "bench-run", "question_ids": selection["question_ids"]}

        async def get_task(self, run_id: str, question_id: str) -> dict:
            return {"prompt": f"Solve {question_id}", "data_files": []}

        async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path:
            raise AssertionError("No files expected")

        async def submit_attempt(self, **kwargs) -> dict:
            return {"attempt_id": f"remote-{kwargs['question_id']}", "job_id": f"job-{kwargs['question_id']}"}

        async def get_grading_job(self, job_id: str) -> dict:
            return {"job_id": job_id, "status": "completed"}

        async def get_results(self, *, question_id: str, session_id: str) -> dict:
            return {"question_id": question_id, "weighted_score": 1.0}

    async def exercise() -> None:
        store = EvaluationStore(tmp_path / "evaluations.db")
        service = EvaluationService(store, tmp_path / "workspaces", launcher=FakeLauncher())
        campaign = service.create_campaign(
            owner_id="alice",
            model_name="matcreator-v1",
            question_ids=["question-1", "question-2"],
            max_parallelism=2,
        )
        await service.start_campaign(campaign["campaign_id"], FakeClient())
        manager = EvaluationManager(max_concurrent_attempts=2, poll_seconds=0)
        await manager.start(campaign["campaign_id"], service, FakeClient())
        task = manager._tasks[campaign["campaign_id"]]
        await task

        assert store.get_campaign(campaign["campaign_id"])["status"] == "completed"
        assert [attempt["status"] for attempt in store.list_attempts(campaign["campaign_id"])] == ["completed", "completed"]

    asyncio.run(exercise())


def test_manager_marks_completed_job_without_result_as_failed(tmp_path) -> None:
    class FakeLauncher:
        async def run(self, spec):
            return RuntimeOutcome(0, "", "", 0.01, {"answer": "done", "num_turns": 1})

    class FakeClient:
        async def create_session(self, model_name: str) -> dict:
            return {"session_id": "bench-session"}

        async def create_run(self, session_id: str, selection: dict) -> dict:
            return {"run_id": "bench-run", "question_ids": selection["question_ids"]}

        async def get_task(self, run_id: str, question_id: str) -> dict:
            return {"prompt": "Solve", "data_files": []}

        async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path:
            raise AssertionError("No files expected")

        async def submit_attempt(self, **kwargs) -> dict:
            return {"attempt_id": "remote-attempt", "job_id": "job-1"}

        async def get_grading_job(self, job_id: str) -> dict:
            return {"job_id": job_id, "status": "completed"}

        async def get_results(self, *, question_id: str, session_id: str) -> dict:
            from matcreator.control_plane.benchmark_client import BenchmarkApiError

            raise BenchmarkApiError("GET", "/results/question-1", 404, "No result found")

    async def exercise() -> None:
        store = EvaluationStore(tmp_path / "evaluations.db")
        service = EvaluationService(store, tmp_path / "workspaces", launcher=FakeLauncher())
        campaign = service.create_campaign(owner_id="alice", model_name="matcreator-v1", question_ids=["question-1"])
        await service.start_campaign(campaign["campaign_id"], FakeClient())
        manager = EvaluationManager(max_concurrent_attempts=1, poll_seconds=0)
        await manager.start(campaign["campaign_id"], service, FakeClient())
        await manager._tasks[campaign["campaign_id"]]

        attempt = store.list_attempts(campaign["campaign_id"])[0]
        assert attempt["status"] == "failed"
        assert "result record is missing" in attempt["error"]
        assert store.get_campaign(campaign["campaign_id"])["status"] == "failed"

    asyncio.run(exercise())


def test_manager_recovers_completed_grade_in_mixed_failed_campaign(tmp_path) -> None:
    class FakeClient:
        async def get_grading_job(self, job_id: str) -> dict:
            return {"job_id": job_id, "status": "completed"}

        async def get_results(self, *, question_id: str, session_id: str) -> dict:
            return {"question_id": question_id, "weighted_score": 0.5}

    async def exercise() -> None:
        store = EvaluationStore(tmp_path / "evaluations.db")
        service = EvaluationService(store, tmp_path / "workspaces")
        campaign = service.create_campaign(owner_id="alice", model_name="matcreator-v1", question_ids=["question-1", "question-2"])
        store.transition_campaign(campaign["campaign_id"], "starting", benchmark_session_id="bench-session")
        store.transition_campaign(campaign["campaign_id"], "active")
        failed = store.create_attempt(
            campaign_id=campaign["campaign_id"], question_id="question-1", runtime_session_id="runtime-1", idempotency_key="key-1"
        )
        interrupted = store.create_attempt(
            campaign_id=campaign["campaign_id"], question_id="question-2", runtime_session_id="runtime-2", idempotency_key="key-2"
        )
        for attempt in (failed, interrupted):
            store.transition_attempt(attempt["attempt_id"], "runtime_starting")
            store.transition_attempt(attempt["attempt_id"], "running")
        store.transition_attempt(failed["attempt_id"], "submitting")
        store.transition_attempt(failed["attempt_id"], "grading", grading_job_id="job-1")
        store.transition_attempt(
            failed["attempt_id"], "failed",
            error="Could not retrieve benchmark grading result: Benchmark grading job completed but the result record is missing.",
        )
        store.transition_attempt(interrupted["attempt_id"], "interrupted", error="Runtime stopped")
        store.transition_campaign(campaign["campaign_id"], "failed")

        manager = EvaluationManager()
        assert await manager.recover_missing_result_campaign(campaign["campaign_id"], service, FakeClient())
        attempts = store.list_attempts(campaign["campaign_id"])
        assert [attempt["status"] for attempt in attempts] == ["completed", "interrupted"]
        assert store.get_campaign(campaign["campaign_id"])["status"] == "failed"

    asyncio.run(exercise())


def test_manager_cancels_running_attempt_without_submission(tmp_path) -> None:
    class BlockingLauncher:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, spec):
            self.started.set()
            await self.release.wait()
            return RuntimeOutcome(1, "", "", 0.01, {}, error="managed ADK run cancelled")

    class FakeClient:
        async def create_session(self, model_name: str) -> dict:
            return {"session_id": "bench-session"}

        async def create_run(self, session_id: str, selection: dict) -> dict:
            return {"run_id": "bench-run", "question_ids": selection["question_ids"]}

        async def get_task(self, run_id: str, question_id: str) -> dict:
            return {"prompt": "Solve", "data_files": []}

        async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path:
            raise AssertionError("No files expected")

        async def submit_attempt(self, **kwargs) -> dict:
            raise AssertionError("Cancelled attempt must not be submitted")

    async def exercise() -> None:
        store = EvaluationStore(tmp_path / "evaluations.db")
        launcher = BlockingLauncher()
        service = EvaluationService(store, tmp_path / "workspaces", launcher=launcher)
        campaign = service.create_campaign(owner_id="alice", model_name="matcreator-v1", question_ids=["question-1"])
        await service.start_campaign(campaign["campaign_id"], FakeClient())
        manager = EvaluationManager(max_concurrent_attempts=1, poll_seconds=0)
        await manager.start(campaign["campaign_id"], service, FakeClient())
        await launcher.started.wait()
        attempt = store.list_attempts(campaign["campaign_id"])[0]
        cancelled_runs = []

        await manager.cancel_campaign(
            campaign["campaign_id"],
            service,
            cancel_managed_run=lambda run_id: _record_cancel(cancelled_runs, launcher, run_id),
        )
        launcher.release.set()
        await manager._tasks[campaign["campaign_id"]]

        assert cancelled_runs == []
        assert store.get_attempt(attempt["attempt_id"])["status"] == "cancelled"
        assert store.get_campaign(campaign["campaign_id"])["status"] == "cancelled"

    async def _record_cancel(cancelled_runs, launcher, run_id):
        cancelled_runs.append(run_id)
        launcher.release.set()

    asyncio.run(exercise())