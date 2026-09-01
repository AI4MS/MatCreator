from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

from managed_runs import ManagedRunRegistry, SseRecordBuffer, is_sse_done, sse_error_message


def test_sse_record_buffer_frames_split_events_and_done_marker() -> None:
    records = SseRecordBuffer()

    assert records.feed('data: {"partial":') == []
    assert records.feed('true}\n\ndata: [DO') == ['data: {"partial":true}\n\n']
    assert records.feed('NE]\r\n\r\n') == ['data: [DONE]\r\n\r\n']
    assert is_sse_done('data: [DONE]\r\n\r\n')
    assert records.flush() == []


def test_sse_error_message_recognizes_adk_generator_failures() -> None:
    record = (
        'data: {"error":"JSONDecodeError: broken tool arguments",'
        '"error_details":{"error_type":"JSONDecodeError",'
        '"error_message":"Expecting comma at column 327"}}\n\n'
    )

    assert sse_error_message(record) == "JSONDecodeError: Expecting comma at column 327"
    assert sse_error_message('data: {"content":{"parts":[{"text":"ok"}]}}\n\n') is None
    assert sse_error_message('data: {not-json}\n\n') == (
        "The agent backend returned a malformed streaming event."
    )


def test_subscriber_disconnect_does_not_cancel_producer() -> None:
    async def exercise() -> None:
        registry = ManagedRunRegistry()
        release = asyncio.Event()

        async def producer(run) -> None:
            await registry.publish(run, "first")
            await release.wait()
            await registry.publish(run, "second")

        run = await registry.start(owner_id="alice", session_id="session-1", producer=producer)
        subscriber = registry.subscribe(run).__aiter__()
        first = await subscriber.__anext__()
        assert first == {"type": "event", "sequence": 1, "data": "first"}
        await subscriber.aclose()

        assert run.task is not None and not run.task.done()
        release.set()
        await run.task

        assert run.status == "completed"
        replay = [item async for item in registry.subscribe(run, after=1)]
        assert replay == [
            {"type": "event", "sequence": 2, "data": "second"},
            {
                "type": "terminal",
                "status": "completed",
                "latest_sequence": 2,
                "error": None,
            },
        ]

    asyncio.run(exercise())


def test_registry_enforces_one_run_per_session_but_allows_other_sessions() -> None:
    async def exercise() -> None:
        registry = ManagedRunRegistry()
        release = asyncio.Event()

        async def producer(_run) -> None:
            await release.wait()

        first = await registry.start(owner_id="alice", session_id="session-1", producer=producer)
        with pytest.raises(RuntimeError, match="already active"):
            await registry.start(owner_id="alice", session_id="session-1", producer=producer)
        second = await registry.start(owner_id="alice", session_id="session-2", producer=producer)

        release.set()
        await asyncio.gather(first.task, second.task)

    asyncio.run(exercise())


def test_latest_run_failure_lookup_is_scoped_to_owner_and_session() -> None:
    async def exercise() -> None:
        registry = ManagedRunRegistry()

        async def failing(_run) -> None:
            raise RuntimeError("broken response JSON")

        failed = await registry.start(owner_id="alice", session_id="session-1", producer=failing)
        await failed.task
        other = await registry.start(owner_id="alice", session_id="session-2", producer=failing)
        await other.task

        assert registry.latest_for("alice", "session-1") is failed
        assert registry.latest_for("alice", "session-2") is other
        assert registry.latest_for("bob", "session-1") is None

    asyncio.run(exercise())


def test_replay_gap_requests_a_session_snapshot() -> None:
    async def exercise() -> None:
        registry = ManagedRunRegistry(replay_limit=2)

        async def producer(run) -> None:
            for payload in ("one", "two", "three"):
                await registry.publish(run, payload)

        run = await registry.start(owner_id="alice", session_id="session-1", producer=producer)
        await run.task

        replay = [item async for item in registry.subscribe(run)]
        assert replay[0] == {
            "type": "snapshot_required",
            "earliest_sequence": 2,
            "latest_sequence": 3,
        }
        assert replay[1] == {
            "type": "terminal",
            "status": "completed",
            "latest_sequence": 3,
            "error": None,
        }

    asyncio.run(exercise())
