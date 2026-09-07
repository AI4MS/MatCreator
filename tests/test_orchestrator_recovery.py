from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from matcreator.agents.orchestrator import agent as orchestrator_module


class _RecoverableExecutionAgent:
    def __init__(self) -> None:
        self.calls = 0

    def run_async(self, _ctx):
        self.calls += 1
        call = self.calls

        async def events():
            if call == 1:
                yield "parallel-results"
                raise json.JSONDecodeError("malformed tool arguments", "{", 1)
            yield "continued-execution"

        return events()


def test_execution_stream_recovers_durable_results_before_retry(monkeypatch):
    state = {"workdir": "/workspace", "execution_graph": {"nodes": {}}}
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))
    execution_agent = _RecoverableExecutionAgent()
    recovery_calls = []

    def reconcile(recovered_state, workspace):
        recovery_calls.append((recovered_state, workspace))
        recovered_state["durable_results_reconciled"] = True
        return [{"node_id": "step_parallel_a", "status": "success"}]

    monkeypatch.setattr(orchestrator_module, "reconcile_recovery_state", reconcile)

    async def collect_events():
        return [
            event
            async for event in orchestrator_module._stream_execution_with_recovery(
                execution_agent, ctx, max_attempts=2
            )
        ]

    events = asyncio.run(collect_events())

    assert events == ["parallel-results", "continued-execution"]
    assert execution_agent.calls == 2
    assert recovery_calls == [(state, "/workspace")]
    assert state["durable_results_reconciled"] is True


def test_execution_stream_does_not_retry_unrelated_failures(monkeypatch):
    class FailingAgent:
        def run_async(self, _ctx):
            async def events():
                raise RuntimeError("executor failed")
                yield

            return events()

    monkeypatch.setattr(
        orchestrator_module,
        "reconcile_recovery_state",
        lambda *_args, **_kwargs: pytest.fail("recovery must not run"),
    )
    ctx = SimpleNamespace(session=SimpleNamespace(state={"workdir": "/workspace"}))

    async def consume_events():
        async for _ in orchestrator_module._stream_execution_with_recovery(
            FailingAgent(), ctx, max_attempts=2
        ):
            pass

    with pytest.raises(RuntimeError, match="executor failed"):
        asyncio.run(consume_events())
