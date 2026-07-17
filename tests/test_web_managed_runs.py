from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
if str(WEB_DIR) not in sys.path:
    sys.path.insert(0, str(WEB_DIR))

from managed_runs import ManagedRunRegistry


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