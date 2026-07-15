from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


ACTIVE_RUN_STATUSES = frozenset({"starting", "running", "cancelling"})
TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class ManagedRun:
    run_id: str
    owner_id: str
    session_id: str
    status: str = "starting"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None
    events: deque[tuple[int, str]] = field(default_factory=deque)
    latest_sequence: int = 0
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    task: asyncio.Task[None] | None = None

    @property
    def earliest_sequence(self) -> int:
        return self.events[0][0] if self.events else self.latest_sequence + 1

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "owner_id": self.owner_id,
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "earliest_sequence": self.earliest_sequence,
            "latest_sequence": self.latest_sequence,
            "error": self.error,
        }


Producer = Callable[[ManagedRun], Awaitable[None]]


class ManagedRunRegistry:
    def __init__(self, *, replay_limit: int = 500) -> None:
        if replay_limit < 1:
            raise ValueError("replay_limit must be positive")
        self.replay_limit = replay_limit
        self._runs: dict[str, ManagedRun] = {}
        self._active_by_session: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        owner_id: str,
        session_id: str,
        producer: Producer,
    ) -> ManagedRun:
        key = (owner_id, session_id)
        async with self._lock:
            active_id = self._active_by_session.get(key)
            if active_id and self._runs[active_id].status in ACTIVE_RUN_STATUSES:
                raise RuntimeError("A run is already active for this session")
            run = ManagedRun(
                run_id=uuid.uuid4().hex,
                owner_id=owner_id,
                session_id=session_id,
            )
            self._runs[run.run_id] = run
            self._active_by_session[key] = run.run_id
            run.task = asyncio.create_task(self._execute(run, producer))
            return run

    async def _execute(self, run: ManagedRun, producer: Producer) -> None:
        await self.set_status(run, "running")
        try:
            await producer(run)
        except asyncio.CancelledError:
            await self.set_status(run, "cancelled")
        except Exception as exc:
            await self.set_status(run, "failed", error=str(exc))
        else:
            terminal = "cancelled" if run.status == "cancelling" else "completed"
            await self.set_status(run, terminal)
        finally:
            async with self._lock:
                key = (run.owner_id, run.session_id)
                if self._active_by_session.get(key) == run.run_id:
                    self._active_by_session.pop(key, None)

    async def publish(self, run: ManagedRun, payload: str) -> int:
        async with run.condition:
            run.latest_sequence += 1
            sequence = run.latest_sequence
            run.events.append((sequence, payload))
            while len(run.events) > self.replay_limit:
                run.events.popleft()
            run.updated_at = time.time()
            run.condition.notify_all()
            return sequence

    async def set_status(
        self,
        run: ManagedRun,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        if status not in ACTIVE_RUN_STATUSES | TERMINAL_RUN_STATUSES:
            raise ValueError(f"Unsupported run status: {status}")
        async with run.condition:
            run.status = status
            run.error = error
            run.updated_at = time.time()
            run.condition.notify_all()

    def get(self, run_id: str) -> ManagedRun | None:
        return self._runs.get(run_id)

    def active_for(self, owner_id: str, session_id: str) -> ManagedRun | None:
        run_id = self._active_by_session.get((owner_id, session_id))
        return self._runs.get(run_id) if run_id else None

    def active_for_session(self, session_id: str) -> list[ManagedRun]:
        return [
            run
            for run in self._runs.values()
            if run.session_id == session_id and run.status in ACTIVE_RUN_STATUSES
        ]

    def active_runs(self, owner_id: str | None = None) -> list[ManagedRun]:
        return [
            run
            for run in self._runs.values()
            if run.status in ACTIVE_RUN_STATUSES
            and (owner_id is None or run.owner_id == owner_id)
        ]

    async def request_cancel(self, run: ManagedRun) -> None:
        if run.status in ACTIVE_RUN_STATUSES:
            await self.set_status(run, "cancelling")
            if run.task and not run.task.done():
                run.task.cancel()

    async def subscribe(
        self,
        run: ManagedRun,
        *,
        after: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        cursor = max(0, after)
        while True:
            snapshot = None
            async with run.condition:
                earliest = run.earliest_sequence
                if run.events and cursor < earliest - 1:
                    snapshot = {
                        "type": "snapshot_required",
                        "earliest_sequence": earliest,
                        "latest_sequence": run.latest_sequence,
                    }
                    cursor = run.latest_sequence

                pending = [event for event in run.events if event[0] > cursor]
                status = run.status
                if not pending and status not in TERMINAL_RUN_STATUSES:
                    await run.condition.wait()
                    continue

            if snapshot is not None:
                yield snapshot
            for sequence, payload in pending:
                cursor = sequence
                yield {"type": "event", "sequence": sequence, "data": payload}

            if status in TERMINAL_RUN_STATUSES and cursor >= run.latest_sequence:
                yield {
                    "type": "terminal",
                    "status": status,
                    "latest_sequence": run.latest_sequence,
                    "error": run.error,
                }
                return

    async def shutdown(self) -> None:
        tasks = [run.task for run in self._runs.values() if run.task and not run.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
