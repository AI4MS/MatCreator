from __future__ import annotations

import asyncio

import httpx
import pytest

from matcreator.control_plane.benchmark_client import BenchmarkApiError, BenchmarkClient, sanitize_bank_id


def test_client_uses_run_scoped_contract_and_downloads_data(tmp_path) -> None:
    async def exercise() -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path == "/questions":
                return httpx.Response(200, json=[{"id": "question-1"}])
            if request.url.path == "/sessions":
                return httpx.Response(200, json={"session_id": "session-1"})
            if request.url.path == "/runs":
                return httpx.Response(200, json={"run_id": "run-1", "question_ids": ["question-1"]})
            if request.url.path == "/runs/run-1/tasks/question-1":
                return httpx.Response(200, json={"id": "question-1", "prompt": "Solve this"})
            if request.url.path == "/questions/question-1/data/input.dat":
                return httpx.Response(200, content=b"input")
            if request.url.path == "/submit/question-1":
                return httpx.Response(200, json={"attempt_id": "attempt-1", "job_id": "job-1", "status": "grading"})
            if request.url.path == "/grading-jobs/job-1":
                return httpx.Response(200, json={"job_id": "job-1", "status": "completed"})
            if request.url.path == "/results":
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {
                                "question_id": "question-1",
                                "overall_weighted_score": 1.0,
                            }
                        ]
                    },
                )
            return httpx.Response(404, json={"detail": "not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = BenchmarkClient("https://bench.example/", "token-1", client=http_client)
            assert (await client.list_questions(domain="catalysis"))["questions"] == [{"id": "question-1"}]
            assert (await client.create_session("matcreator-v1"))["session_id"] == "session-1"
            assert (await client.create_run("session-1", {"question_ids": ["question-1"]}))["run_id"] == "run-1"
            assert (await client.get_task("run-1", "question-1"))["prompt"] == "Solve this"
            output = await client.download_data_file("question-1", "input.dat", tmp_path / "input.dat")
            artifact = tmp_path / "result.txt"
            artifact.write_text("result", encoding="utf-8")
            submission = await client.submit_attempt(
                run_id="run-1",
                question_id="question-1",
                idempotency_key="submission-key",
                meta={"answer": "done", "num_turns": 2, "is_error": False, "tool_calls": []},
                artifacts=[artifact],
            )
            assert submission["job_id"] == "job-1"
            assert (await client.get_grading_job("job-1"))["status"] == "completed"
            assert (await client.get_results(question_id="question-1", session_id="session-1"))["weighted_score"] == 1.0

        assert output.read_bytes() == b"input"
        assert calls[1].headers["X-API-Token"] == "token-1"
        assert calls[2].url.params["session_id"] == "session-1"
        assert calls[5].headers["Idempotency-Key"] == "submission-key"
        assert calls[7].url.path == "/results"
        assert calls[7].url.params["session_id"] == "session-1"

    asyncio.run(exercise())


def test_client_raises_with_server_detail() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(422, json={"detail": "no questions"}))
        ) as http_client:
            client = BenchmarkClient("https://bench.example", "token-1", client=http_client)
            with pytest.raises(BenchmarkApiError, match="no questions") as error:
                await client.list_questions()
            assert error.value.status_code == 422

    asyncio.run(exercise())


def test_client_registers_development_token_without_existing_token() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(201, json={"token": "development-token"})
                if request.url.path == "/token" else httpx.Response(404)
            )
        ) as http_client:
            token = await BenchmarkClient.register_token("https://bench.example/", client=http_client)
        assert token == "development-token"

    asyncio.run(exercise())


def test_client_preserves_catalog_envelope_metadata() -> None:
    async def exercise() -> None:
        payload = {
            "items": [{"id": "question-1"}],
            "total": 42,
            "offset": 10,
            "limit": 5,
            "facets": {"tags": ["wf_batch"]},
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as http_client:
            catalog = await BenchmarkClient("https://bench.example", "token-1", client=http_client).list_questions()
        assert catalog["questions"] == payload["items"]
        assert catalog["total"] == 42
        assert catalog["facets"] == {"tags": ["wf_batch"]}

    asyncio.run(exercise())


def test_sanitize_bank_id_produces_a_safe_stable_identifier() -> None:
    assert sanitize_bank_id("alice") == "user-alice"
    assert sanitize_bank_id("alice@example.com") == "user-alice-example.com"
    assert sanitize_bank_id("") == "user-unknown"


def test_client_creates_and_lists_custom_banks() -> None:
    async def exercise() -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if request.url.path == "/banks" and request.method == "POST":
                return httpx.Response(201, json={"bank_id": "user-alice", "display_name": "alice's questions"})
            if request.url.path == "/banks" and request.method == "GET":
                return httpx.Response(200, json={"banks": [{"bank_id": "user-alice"}]})
            return httpx.Response(404, json={"detail": "not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = BenchmarkClient("https://bench.example", "token-1", client=http_client)
            created = await client.create_bank("user-alice", display_name="alice's questions")
            assert created["bank_id"] == "user-alice"
            banks = await client.list_banks()
            assert banks == [{"bank_id": "user-alice"}]

        assert calls[0].headers["X-API-Token"] == "token-1"
        assert calls[1].headers["X-API-Token"] == "token-1"

    asyncio.run(exercise())


def test_client_ensure_bank_is_idempotent_when_bank_already_exists() -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/banks" and request.method == "POST":
                return httpx.Response(409, json={"detail": "bank already exists"})
            if request.url.path == "/banks" and request.method == "GET":
                return httpx.Response(200, json={"banks": [{"bank_id": "user-alice", "display_name": "old"}]})
            return httpx.Response(404, json={"detail": "not found"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = BenchmarkClient("https://bench.example", "token-1", client=http_client)
            bank = await client.ensure_bank("user-alice")
            assert bank == {"bank_id": "user-alice", "display_name": "old"}

    asyncio.run(exercise())


def test_client_ensure_bank_reraises_when_not_a_conflict() -> None:
    async def exercise() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(401, json={"detail": "unauthorized"}))
        ) as http_client:
            client = BenchmarkClient("https://bench.example", "token-1", client=http_client)
            with pytest.raises(BenchmarkApiError, match="unauthorized"):
                await client.ensure_bank("user-alice")

    asyncio.run(exercise())


def test_client_publishes_question_with_declared_data_files(tmp_path) -> None:
    async def exercise() -> None:
        captured: dict[str, httpx.Request] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/banks/user-alice/questions":
                captured["request"] = request
                return httpx.Response(201, json={"question_id": "question-1", "bank_id": "user-alice"})
            return httpx.Response(404, json={"detail": "not found"})

        source = tmp_path / "parameters.json"
        source.write_text('{"encut": 320}', encoding="utf-8")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            client = BenchmarkClient("https://bench.example", "token-1", client=http_client)
            result = await client.publish_question(
                "user-alice",
                question={"id": "question-1"},
                data_files=[("inputs/parameters.json", source)],
            )
            assert result["question_id"] == "question-1"

        request = captured["request"]
        assert request.headers["X-API-Token"] == "token-1"
        body = request.content.decode("utf-8", errors="ignore")
        assert 'name="question"' in body
        assert 'name="data_files"; filename="inputs/parameters.json"' in body

    asyncio.run(exercise())
