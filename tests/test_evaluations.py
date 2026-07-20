from __future__ import annotations

import pytest

from matcreator.control_plane.evaluations import EvaluationStore
from matcreator.control_plane.evaluation_service import EvaluationService
from matcreator.control_plane.evaluation_runtime import RuntimeOutcome


def test_evaluation_store_persists_campaign_attempt_and_events(tmp_path) -> None:
    store = EvaluationStore(tmp_path / "evaluations.db")
    campaign = store.create_campaign(
        owner_id="alice",
        model_name="matcreator-v1",
        configuration={"max_parallelism": 2, "question_ids": ["question-1"]},
    )

    started = store.transition_campaign(
        campaign["campaign_id"],
        "starting",
        benchmark_session_id="bench-session",
        benchmark_run_id="bench-run",
    )
    assert started["benchmark_run_id"] == "bench-run"

    attempt = store.create_attempt(
        campaign_id=campaign["campaign_id"],
        question_id="question-1",
        runtime_session_id="runtime-session",
        idempotency_key="attempt-key",
        workspace_path="/tmp/workspace",
    )
    assert store.create_attempt(
        campaign_id=campaign["campaign_id"],
        question_id="question-1",
        runtime_session_id="runtime-session",
        idempotency_key="attempt-key",
    )["attempt_id"] == attempt["attempt_id"]

    store.transition_attempt(attempt["attempt_id"], "runtime_starting")
    running = store.transition_attempt(attempt["attempt_id"], "running")
    assert running["workspace_path"] == "/tmp/workspace"
    task = store.set_task_payload(attempt["attempt_id"], {"prompt": "Solve this", "data_files": ["input.dat"]})
    assert task["task_payload"]["prompt"] == "Solve this"
    linked = store.set_managed_run_id(attempt["attempt_id"], "managed-run-1")
    assert linked["managed_run_id"] == "managed-run-1"
    assert store.get_attempt(attempt["attempt_id"])["managed_run_id"] == "managed-run-1"
    assert [event["event_type"] for event in store.list_events(campaign["campaign_id"])] == [
        "campaign_created",
        "campaign_status",
        "attempt_created",
        "attempt_status",
        "attempt_status",
        "attempt_task_loaded",
        "attempt_managed_run_started",
    ]


def test_evaluation_store_rejects_invalid_transitions_and_idempotency_collisions(tmp_path) -> None:
    store = EvaluationStore(tmp_path / "evaluations.db")
    campaign = store.create_campaign(owner_id="alice", model_name="matcreator-v1", configuration={})
    attempt = store.create_attempt(
        campaign_id=campaign["campaign_id"],
        question_id="question-1",
        runtime_session_id="runtime-session",
        idempotency_key="attempt-key",
    )

    with pytest.raises(ValueError, match="Illegal evaluation transition"):
        store.transition_campaign(campaign["campaign_id"], "completed")
    with pytest.raises(ValueError, match="Illegal evaluation transition"):
        store.transition_attempt(attempt["attempt_id"], "completed")
    with pytest.raises(ValueError, match="belongs to different work"):
        store.create_attempt(
            campaign_id=campaign["campaign_id"],
            question_id="question-2",
            runtime_session_id="runtime-session-2",
            idempotency_key="attempt-key",
        )


def test_evaluation_store_lists_active_campaigns(tmp_path) -> None:
    store = EvaluationStore(tmp_path / "evaluations.db")
    active = store.create_campaign(owner_id="alice", model_name="matcreator-v1", configuration={})
    terminal = store.create_campaign(owner_id="bob", model_name="matcreator-v1", configuration={})
    store.transition_campaign(terminal["campaign_id"], "cancelled")

    assert [campaign["campaign_id"] for campaign in store.list_active_campaigns()] == [active["campaign_id"]]


def test_question_sets_are_private_or_shared_and_owner_writable(tmp_path) -> None:
    store = EvaluationStore(tmp_path / "evaluations.db")
    private = store.create_question_set(
        owner_id="alice", name="Private set", question_ids=["question-1", "question-1"], visibility="private"
    )
    shared = store.create_question_set(
        owner_id="alice", name="Shared set", question_ids=["question-2"], visibility="shared"
    )
    assert private["question_ids"] == ["question-1"]
    assert [item["set_id"] for item in store.list_question_sets(viewer_id="bob")] == [shared["set_id"]]
    with pytest.raises(KeyError):
        store.delete_question_set(set_id=private["set_id"], owner_id="bob")
    updated = store.update_question_set(
        set_id=private["set_id"], owner_id="alice", name="Renamed", question_ids=["question-3"], visibility="shared"
    )
    assert updated["visibility"] == "shared"


def test_store_recovers_missing_result_failure(tmp_path) -> None:
    store = EvaluationStore(tmp_path / "evaluations.db")
    campaign = store.create_campaign(owner_id="alice", model_name="matcreator-v1", configuration={})
    store.transition_campaign(campaign["campaign_id"], "starting")
    store.transition_campaign(campaign["campaign_id"], "active")
    attempt = store.create_attempt(
        campaign_id=campaign["campaign_id"],
        question_id="question-1",
        runtime_session_id="runtime-1",
        idempotency_key="attempt-1",
    )
    store.transition_attempt(attempt["attempt_id"], "runtime_starting")
    store.transition_attempt(attempt["attempt_id"], "running")
    store.transition_attempt(attempt["attempt_id"], "submitting")
    store.transition_attempt(attempt["attempt_id"], "grading")
    store.transition_attempt(
        attempt["attempt_id"],
        "failed",
        error="Could not retrieve benchmark grading result: Benchmark grading job completed but the result record is missing.",
    )
    store.transition_campaign(campaign["campaign_id"], "failed")

    recovered = store.recover_missing_result_attempt(attempt["attempt_id"], {"weighted_score": 1.0})
    assert recovered["status"] == "completed"
    assert recovered["result"]["weighted_score"] == 1.0
    assert store.recover_completed_campaign(campaign["campaign_id"])["status"] == "completed"


def test_service_freezes_remote_run_and_creates_question_scoped_attempts(tmp_path) -> None:
    class FakeClient:
        async def create_session(self, model_name: str) -> dict:
            assert model_name == "matcreator-v1"
            return {"session_id": "benchmark-session"}

        async def create_run(self, session_id: str, selection: dict) -> dict:
            assert session_id == "benchmark-session"
            assert selection == {"question_ids": ["question-1", "question-2"]}
            return {"run_id": "benchmark-run", "question_ids": ["question-1", "question-2"]}

    async def exercise() -> None:
        store = EvaluationStore(tmp_path / "evaluations.db")
        service = EvaluationService(store, tmp_path / "workspaces")
        campaign = service.create_campaign(
            owner_id="alice",
            model_name="matcreator-v1",
            question_ids=["question-1", "question-2", "question-1"],
            max_parallelism=2,
        )

        active = await service.start_campaign(campaign["campaign_id"], FakeClient())
        attempts = store.list_attempts(campaign["campaign_id"])

        assert active["status"] == "active"
        assert active["benchmark_session_id"] == "benchmark-session"
        assert [attempt["question_id"] for attempt in attempts] == ["question-1", "question-2"]
        assert all(attempt["status"] == "queued" for attempt in attempts)
        assert all(campaign["campaign_id"] in attempt["workspace_path"] for attempt in attempts)
        assert len({attempt["runtime_session_id"] for attempt in attempts}) == 2

    import asyncio

    asyncio.run(exercise())


def test_service_executes_isolated_attempt_and_submits_result(tmp_path) -> None:
    class FakeLauncher:
        async def run(self, spec):
            assert spec.workspace.is_relative_to(tmp_path / "workspaces" / campaign_id)
            assert spec.workspace.name == spec.session_id
            assert spec.runtime_home.is_relative_to(spec.workspace.parent / ".runtime")
            (spec.workspace / "answer.txt").write_text("artifact", encoding="utf-8")
            return RuntimeOutcome(0, "", "", 1.0, {"answer": "done", "num_turns": 3})

    class FakeClient:
        async def create_session(self, model_name: str) -> dict:
            return {"session_id": "benchmark-session"}

        async def create_run(self, session_id: str, selection: dict) -> dict:
            return {"run_id": "benchmark-run", "question_ids": ["question-1"]}

        async def get_task(self, run_id: str, question_id: str) -> dict:
            return {"prompt": "Solve this", "data_files": [{"filename": "input.dat"}]}

        async def download_data_file(self, question_id: str, filename: str, destination: Path) -> Path:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text("input", encoding="utf-8")
            return destination

        async def submit_attempt(self, **kwargs) -> dict:
            assert kwargs["idempotency_key"]
            assert kwargs["meta"]["answer"] == "done"
            assert [artifact.name for artifact in kwargs["artifacts"]] == ["answer.txt"]
            return {"attempt_id": "benchmark-attempt", "job_id": "grading-job"}

    async def exercise() -> None:
        nonlocal campaign_id
        store = EvaluationStore(tmp_path / "evaluations.db")
        service = EvaluationService(store, tmp_path / "workspaces", launcher=FakeLauncher())
        campaign = service.create_campaign(owner_id="alice", model_name="matcreator-v1", question_ids=["question-1"])
        campaign_id = campaign["campaign_id"]
        await service.start_campaign(campaign_id, FakeClient())
        attempt = store.list_attempts(campaign_id)[0]
        submitted = await service.execute_attempt(campaign_id=campaign_id, attempt_id=attempt["attempt_id"], client=FakeClient())
        assert submitted["status"] == "grading"
        assert submitted["task_payload"] == {"prompt": "Solve this", "data_files": ["input.dat"]}
        assert submitted["benchmark_attempt_id"] == "benchmark-attempt"
        assert submitted["grading_job_id"] == "grading-job"

    campaign_id = ""
    import asyncio

    asyncio.run(exercise())