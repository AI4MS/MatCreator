from __future__ import annotations

import sys
from pathlib import Path

import asyncio
from collections import deque

from fastapi.testclient import TestClient


WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

import main


def test_create_evaluation_campaign_reads_json_body(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(main, "_evaluation_store_for_owner", lambda _owner: main.EvaluationStore(tmp_path / "evaluations.db"))
    monkeypatch.setattr(main, "_evaluation_workspace_for_owner", lambda _owner: tmp_path / "workspaces")
    client = TestClient(main.app)

    response = client.post(
        "/api/evaluations/campaigns?user_id=user",
        json={
            "model_name": "matcreator",
            "question_ids": ["question-1"],
            "max_parallelism": 1,
            "max_turns": 50,
            "timeout_seconds": 600,
            "flash": False,
        },
    )

    assert response.status_code == 201, response.json()
    assert response.json()["configuration"]["question_ids"] == ["question-1"]


def test_managed_evaluation_runtime_creates_adk_session_and_run(monkeypatch, tmp_path) -> None:
    class FakeRun:
        run_id = "managed-run-1"
        status = "completed"
        error = None
        events = deque()

    calls = []

    async def fake_prepare(**kwargs):
        calls.append(("session", kwargs))

    async def fake_start(**kwargs):
        calls.append(("run", kwargs))
        run = FakeRun()
        run.task = asyncio.create_task(asyncio.sleep(0))
        return run

    monkeypatch.setattr(main, "_prepare_evaluation_adk_session", fake_prepare)
    monkeypatch.setattr(main, "_start_managed_run", fake_start)

    async def exercise():
        workspace = tmp_path / "evaluation"
        prompt = workspace / "prompt.txt"
        workspace.mkdir()
        prompt.write_text("Benchmark prompt", encoding="utf-8")
        linked = []
        outcome = await main._ManagedAdkEvaluationRuntime("user").run(
            main.RuntimeSpec(
                workspace=workspace,
                runtime_home=workspace / ".runtime",
                prompt_path=prompt,
                session_id="eval-session",
                max_turns=10,
                timeout_seconds=10,
                on_managed_run_started=lambda run_id: _link(linked, run_id),
            )
        )
        assert outcome.exit_code == 0
        assert linked == ["managed-run-1"]

    async def _link(linked, run_id):
        linked.append(run_id)

    asyncio.run(exercise())
    assert calls[0][0] == "session"
    assert calls[0][1]["session_id"] == "eval-session"
    assert calls[0][1]["workspace"] == tmp_path / "evaluation"
    assert calls[1][0] == "run"
    assert calls[1][1]["payload"]["new_message"]["parts"][0]["text"] == "Benchmark prompt"


def test_cancel_evaluation_campaign_rejects_terminal_campaign(monkeypatch, tmp_path) -> None:
    store = main.EvaluationStore(tmp_path / "evaluations.db")
    campaign = store.create_campaign(owner_id="user", model_name="matcreator", configuration={})
    store.transition_campaign(campaign["campaign_id"], "cancelled")
    monkeypatch.setattr(main, "_evaluation_store_for_owner", lambda _owner: store)
    monkeypatch.setattr(main, "_evaluation_workspace_for_owner", lambda _owner: tmp_path / "workspaces")
    client = TestClient(main.app)

    response = client.post(f"/api/evaluations/campaigns/{campaign['campaign_id']}/cancel?user_id=user")

    assert response.status_code == 409, response.json()
    assert "active evaluation campaigns" in response.json()["detail"]


def test_question_set_routes_enforce_shared_read_and_owner_delete(monkeypatch, tmp_path) -> None:
    store = main.EvaluationStore(tmp_path / "evaluations.db")
    monkeypatch.setattr(main, "_evaluation_store_for_owner", lambda _owner: store)
    client = TestClient(main.app)

    created = client.post(
        "/api/evaluations/question-sets?user_id=alice",
        json={"name": "Crystal checks", "question_ids": ["question-1"], "visibility": "shared"},
    )

    assert created.status_code == 201, created.json()
    set_id = created.json()["set_id"]
    visible = client.get("/api/evaluations/question-sets?user_id=bob")
    assert visible.status_code == 200, visible.json()
    assert [item["set_id"] for item in visible.json()["question_sets"]] == [set_id]
    assert client.delete(f"/api/evaluations/question-sets/{set_id}?user_id=bob").status_code == 404
    assert client.delete(f"/api/evaluations/question-sets/{set_id}?user_id=alice").status_code == 204


def test_catalog_forwards_single_capability_and_task_type_filters(monkeypatch) -> None:
    class FakeBenchmarkClient:
        async def list_questions(self, **filters):
            assert filters["capability"] == "data_handling"
            assert filters["task_type"] == "simulation"
            return {
                "questions": [
                    {"id": "match", "capability": ["data_handling", "scientific_reasoning"], "task_type": "simulation"},
                ],
                "total": 1,
                "offset": 0,
                "limit": 100,
                "facets": {},
            }

    async def benchmark_client_for_owner(_owner: str):
        return FakeBenchmarkClient()

    monkeypatch.setattr(main, "_benchmark_client_for_owner", benchmark_client_for_owner)
    client = TestClient(main.app)

    response = client.get("/api/evaluations/catalog?capability=data_handling&task_type=simulation")

    assert response.status_code == 200, response.json()
    assert [question["id"] for question in response.json()["questions"]] == ["match"]
    assert response.json()["total"] == 1


def test_catalog_uses_maximum_page_size_by_default(monkeypatch) -> None:
    class FakeBenchmarkClient:
        async def list_questions(self, **filters):
            assert filters["limit"] == 500
            return {"questions": [], "total": None, "offset": 0, "limit": 500, "facets": {}}

    async def benchmark_client_for_owner(_owner: str):
        return FakeBenchmarkClient()

    monkeypatch.setattr(main, "_benchmark_client_for_owner", benchmark_client_for_owner)

    response = TestClient(main.app).get("/api/evaluations/catalog")

    assert response.status_code == 200, response.json()


def test_session_evaluation_question_draft_returns_bounded_observable_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "event_count": 3,
            "artifact_count": 1,
            "artifacts": ["/tmp/result.json"],
            "graph": {
                "nodes": [
                    {
                        "step_number": 1,
                        "action": "Relax structure",
                        "summary": "Converged geometry.",
                        "status": "success",
                        "tool_call_count": 2,
                        "artifact_count": 1,
                    },
                    {"step_number": 2, "action": "Retry", "status": "failed"},
                ]
            },
        },
    )

    response = TestClient(main.app).post(
        "/api/sessions/session-1/evaluation-question-draft?user_id=alice"
    )

    assert response.status_code == 200, response.json()
    draft = response.json()
    assert draft["status"] == "draft"
    assert draft["source"] == {
        "session_id": "session-1",
        "owner_id": "alice",
        "event_count": 3,
        "artifact_count": 1,
    }
    assert draft["question"]["rubrics"] == []
    assert draft["evidence"]["successful_steps"] == [
        {
            "step_number": 1,
            "action": "Relax structure",
            "summary": "Converged geometry.",
            "tool_call_count": 2,
            "artifact_count": 1,
        }
    ]
    assert draft["evidence"]["artifacts"] == ["/tmp/result.json"]
    assert draft["publication"]["status"] == "local_preview"


def test_session_evaluation_question_draft_propagates_missing_session(monkeypatch) -> None:
    def missing_session(_session_id, _user_id):
        raise main.HTTPException(status_code=404, detail="Session not found")

    monkeypatch.setattr(main, "_load_session_log_export", missing_session)

    response = TestClient(main.app).post(
        "/api/sessions/missing/evaluation-question-draft?user_id=alice"
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_session_question_generation_requires_llm_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")
    monkeypatch.setattr(main, "load_config", lambda: {})
    monkeypatch.delenv("LLM_MODEL", raising=False)

    response = TestClient(main.app).post(
        "/api/sessions/session-1/evaluation-question-drafts?user_id=alice"
    )

    assert response.status_code == 422, response.json()
    assert response.json()["detail"] == "The builtin_llm question generator requires an LLM model"


def test_session_question_generation_stages_valid_review_draft(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "event_count": 1,
            "artifact_count": 1,
            "artifacts": ["/workspace/run.in"],
            "graph": {"nodes": [{"status": "success", "action": "Created run.in"}]},
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")

    async def generate(_evidence):
        return {
            "id": "session_run_in",
            "task_type": "simulation",
            "capabilities": ["tool_utilization"],
            "domain": "agnostic",
            "difficulty": "easy",
            "intent": "Generate a run input file.",
            "human_prompt_seed": "Create run.in.",
            "tags": ["generated_from_session"],
            "data_files": [],
            "reference_answers": [{"key": "run_in", "value": "run.in"}],
            "scoring_checklist": [{
                "id": "run_in",
                "criterion": "Generate run.in.",
                "verify": "artifact_exists",
                "weight": 1.0,
                "capability": "tool_utilization",
            }],
        }

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )

    response = TestClient(main.app).post(
        "/api/sessions/session-1/evaluation-question-drafts?user_id=alice"
    )

    assert response.status_code == 201, response.json()
    draft = response.json()
    assert draft["status"] == "ready_for_review"
    assert draft["validation_errors"] == []
    assert "id: session_run_in" in draft["question_yaml"]
    staged = Path(draft["staging_path"])
    assert staged.is_relative_to(tmp_path / "staging")
    assert (staged / "question.yaml").is_file()


def test_session_question_generation_rejects_unknown_verifier(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")

    async def generate(_evidence):
        return {
            "id": "invalid-question",
            "task_type": "simulation",
            "capabilities": ["tool_utilization"],
            "domain": "agnostic",
            "difficulty": "easy",
            "intent": "Invalid verifier test.",
            "human_prompt_seed": "Create a file.",
            "reference_answers": [{"key": "structure", "value": "output.cif"}],
            "scoring_checklist": [{
                "id": "structure",
                "criterion": "Create a parseable structure.",
                "verify": "unsupported_verifier",
            }],
        }

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )

    response = TestClient(main.app).post(
        "/api/sessions/session-1/evaluation-question-drafts?user_id=alice"
    )

    assert response.status_code == 201, response.json()
    assert response.json()["status"] == "invalid"
    assert any("unsupported_verifier" in error for error in response.json()["validation_errors"])


def test_session_question_draft_can_be_edited_approved_and_exported(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")
    monkeypatch.setattr(main, "_benchmark_question_bank_root", lambda: tmp_path / "question-bank")

    async def generate(_evidence):
        return {
            "id": "session_question",
            "task_type": "simulation",
            "capabilities": ["tool_utilization"],
            "domain": "agnostic",
            "difficulty": "easy",
            "intent": "Generate an input file.",
            "human_prompt_seed": "Create input.txt.",
            "reference_answers": [{"key": "input", "value": "input.txt"}],
            "scoring_checklist": [{
                "id": "input",
                "criterion": "Generate input.txt.",
                "verify": "artifact_exists",
            }],
        }

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )
    client = TestClient(main.app)
    created = client.post("/api/sessions/session-1/evaluation-question-drafts?user_id=alice")

    assert created.status_code == 201, created.json()
    draft_id = created.json()["draft_id"]
    loaded = client.get(f"/api/evaluation-question-drafts/{draft_id}?user_id=alice")
    assert loaded.status_code == 200, loaded.json()
    assert loaded.json()["evidence"]["source"]["session_id"] == "session-1"

    updated_yaml = created.json()["question_yaml"].replace("session_question", "reviewed_question")
    updated = client.put(
        f"/api/evaluation-question-drafts/{draft_id}?user_id=alice",
        json={"question_yaml": updated_yaml},
    )
    assert updated.status_code == 200, updated.json()
    assert updated.json()["status"] == "ready_for_review"
    assert updated.json()["question"]["id"] == "reviewed_question"

    approved = client.post(f"/api/evaluation-question-drafts/{draft_id}/approve?user_id=alice")
    assert approved.status_code == 200, approved.json()
    assert approved.json()["status"] == "approved"

    exported = client.post(f"/api/evaluation-question-drafts/{draft_id}/export?user_id=alice")
    assert exported.status_code == 200, exported.json()
    assert exported.json()["status"] == "exported"
    assert (tmp_path / "question-bank" / "reviewed_question" / "question.yaml").is_file()
    assert client.post(f"/api/evaluation-question-drafts/{draft_id}/export?user_id=alice").status_code == 409


def test_session_question_draft_can_be_published_to_custom_bank(monkeypatch, tmp_path) -> None:
    class FakeBenchmarkClient:
        def __init__(self) -> None:
            self.ensured: list[tuple[str, str | None]] = []
            self.published: list[dict] = []

        async def list_banks(self):
            return [{"bank_id": "user-alice"}] if self.ensured else []

        async def ensure_bank(self, bank_id, *, display_name=None):
            self.ensured.append((bank_id, display_name))
            return {"bank_id": bank_id, "display_name": display_name}

        async def publish_question(self, bank_id, *, question, data_files=None):
            self.published.append({"bank_id": bank_id, "question": question})
            return {"question_id": question["id"], "bank_id": bank_id}

    fake_client = FakeBenchmarkClient()

    async def benchmark_client_for_owner(_owner: str):
        return fake_client

    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")
    monkeypatch.setattr(main, "_benchmark_client_for_owner", benchmark_client_for_owner)

    async def generate(_evidence):
        return {
            "id": "session_question",
            "task_type": "simulation",
            "capabilities": ["tool_utilization"],
            "domain": "agnostic",
            "difficulty": "easy",
            "intent": "Generate an input file.",
            "human_prompt_seed": "Create input.txt.",
            "reference_answers": [{"key": "input", "value": "input.txt"}],
            "scoring_checklist": [{
                "id": "input",
                "criterion": "Generate input.txt.",
                "verify": "artifact_exists",
            }],
        }

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )
    client = TestClient(main.app)

    status = client.get("/api/evaluation-benchmark-bank?user_id=alice")
    assert status.status_code == 200, status.json()
    assert status.json() == {"bank_id": "user-alice", "exists": False, "bank": None}

    created = client.post("/api/sessions/session-1/evaluation-question-drafts?user_id=alice")
    draft_id = created.json()["draft_id"]
    client.post(f"/api/evaluation-question-drafts/{draft_id}/approve?user_id=alice")

    published = client.post(f"/api/evaluation-question-drafts/{draft_id}/publish?user_id=alice")
    assert published.status_code == 200, published.json()
    assert published.json()["status"] == "published"
    assert published.json()["published_bank_id"] == "user-alice"
    assert fake_client.ensured == [("user-alice", "alice's questions")]
    assert fake_client.published[0]["bank_id"] == "user-alice"
    assert client.post(f"/api/evaluation-question-drafts/{draft_id}/publish?user_id=alice").status_code == 409

    status_after = client.get("/api/evaluation-benchmark-bank?user_id=alice")
    assert status_after.json()["exists"] is True


def test_session_question_draft_uploads_data_file_and_exports_it(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "staging")
    monkeypatch.setattr(main, "_benchmark_question_bank_root", lambda: tmp_path / "question-bank")

    async def generate(_evidence):
        return {
            "id": "question_with_data",
            "task_type": "simulation",
            "capabilities": ["tool_utilization"],
            "domain": "agnostic",
            "difficulty": "easy",
            "intent": "Generate an input file.",
            "human_prompt_seed": "Read inputs/parameters.json and create input.txt.",
            "data_files": [{"key": "parameters", "path": "inputs/parameters.json"}],
            "reference_answers": [{"key": "input", "value": "input.txt"}],
            "scoring_checklist": [{
                "id": "input",
                "criterion": "Generate input.txt.",
                "verify": "artifact_exists",
            }],
        }

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )
    client = TestClient(main.app)
    created = client.post("/api/sessions/session-1/evaluation-question-drafts?user_id=alice")
    assert created.status_code == 201, created.json()
    draft_id = created.json()["draft_id"]

    uploaded = client.post(
        f"/api/evaluation-question-drafts/{draft_id}/data-files?user_id=alice",
        data={"path": "inputs/parameters.json"},
        files={"file": ("parameters.json", b'{"encut": 320}\n', "application/json")},
    )
    assert uploaded.status_code == 200, uploaded.json()
    assert client.post(f"/api/evaluation-question-drafts/{draft_id}/approve?user_id=alice").status_code == 200

    exported = client.post(f"/api/evaluation-question-drafts/{draft_id}/export?user_id=alice")
    assert exported.status_code == 200, exported.json()
    assert (
        tmp_path / "question-bank" / "question_with_data" / "inputs" / "parameters.json"
    ).read_bytes() == b'{"encut": 320}\n'


def test_session_question_draft_can_be_listed_and_refined(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        main,
        "_load_session_log_export",
        lambda session_id, user_id: {
            "session_id": session_id,
            "owner_id": user_id,
            "graph": {"nodes": []},
            "events": [{"type": "tool"}],
        },
    )
    monkeypatch.setattr(main, "_session_question_staging_root", lambda _owner: tmp_path / "evals")
    monkeypatch.setattr(
        main,
        "_legacy_session_question_staging_root",
        lambda _owner: tmp_path / "legacy-evals",
    )
    generated = {
        "id": "refined_question",
        "task_type": "simulation",
        "capabilities": ["tool_utilization"],
        "domain": "agnostic",
        "difficulty": "easy",
        "intent": "Generate a refined input file.",
        "human_prompt_seed": "Create input.txt.",
        "reference_answers": [{"key": "input", "value": "input.txt"}],
        "scoring_checklist": [{
            "id": "input",
            "criterion": "Generate input.txt.",
            "verify": "artifact_exists",
        }],
    }

    async def generate(_evidence):
        return generated

    monkeypatch.setattr(
        main,
        "_session_question_generator",
        lambda: main.CallableSessionQuestionGenerator(generate),
    )
    client = TestClient(main.app)
    created = client.post("/api/sessions/session-1/evaluation-question-drafts?user_id=alice")
    assert created.status_code == 201, created.json()
    draft_id = created.json()["draft_id"]

    invalid_yaml = created.json()["question_yaml"].replace(
        "verify: artifact_exists", "verify: unsupported_verifier"
    )
    invalid = client.put(
        f"/api/evaluation-question-drafts/{draft_id}?user_id=alice",
        json={"question_yaml": invalid_yaml},
    )
    assert invalid.status_code == 200, invalid.json()
    assert invalid.json()["status"] == "invalid"

    refined = client.post(
        f"/api/evaluation-question-drafts/{draft_id}/refine?user_id=alice",
        json={"instruction": "Fix the verifier."},
    )
    assert refined.status_code == 200, refined.json()
    assert refined.json()["status"] == "ready_for_review"
    assert refined.json()["refinement_count"] == 1
    assert Path(refined.json()["staging_path"]).is_relative_to(tmp_path / "evals")

    listed = client.get("/api/evaluation-question-drafts?user_id=alice")
    assert listed.status_code == 200, listed.json()
    assert listed.json()["drafts"] == [
        {
            "draft_id": draft_id,
            "question_id": "refined_question",
            "intent": "Generate a refined input file.",
            "status": "ready_for_review",
            "source_session_id": "session-1",
            "validation_errors": [],
            "refinement_count": 1,
            "updated_at": listed.json()["drafts"][0]["updated_at"],
            "staging_path": refined.json()["staging_path"],
            "published_bank_id": None,
        }
    ]