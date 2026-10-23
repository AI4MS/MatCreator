from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from itertools import combinations
from types import SimpleNamespace

import httpx
import pytest

from matcreator.xrd.jev_controller import AgentEventType, RunStatus, AgentStateError, TypeSafeModel, execute_typesafe, request_choice
AgentConfig=SimpleNamespace
def ScopedToolDefinition(capability_id, name, description='', parameters=(), input_schema=None, **kw):
    return SimpleNamespace(capability_id=capability_id,name=name,description=description,parameters=parameters,input_schema=input_schema,**kw)
def ToolParameter(*args):return args


ACTIONS = ("read", "start", "decision", "evaluate", "baseline", "finish", "stop")
CONFIG = AgentConfig(agent_id="jev-agent", name="Jev", system_instruction="Select the next XRD combination using measured evidence.", model="oaw:model:jev")
CONTEXT = SimpleNamespace(run_id="run-jev")
MODEL = TypeSafeModel("jev-1.13.0", api_key="secret-jev")


class FakeHarness:
    """Deterministic scoped-broker fixture; no scientific worker or network."""

    def __init__(self, remaining=2, status="idle", baseline=True, max_phases=2, candidate_ids=None):
        self.calls = []
        self.remaining = remaining
        self.status = status
        self.baseline = baseline
        self.baseline_status = "pending" if baseline else "disabled"
        self.budget = max(1, remaining)
        self.max_phases = max_phases
        self.candidate_ids = candidate_ids or ["A", "B"]
        self.tried = set()
        self.cloud_request_count = 0
        self.cloud_request_limit = self.budget * max_phases
        self.last_token = None
        self.definitions = [ScopedToolDefinition(
            capability_id="operation:xrd_multiphase_" + action,
            name="xrd_multiphase_" + action, description="Scoped XRD " + action,
            parameters=(ToolParameter("target", str, "Harness selector."),),
            input_schema={"type": "object", "properties": {"target": {"type": "string", "enum": ["harness"]}}, "required": ["target"]},
        ) for action in ACTIONS]
        self.definitions.append(ScopedToolDefinition("operation:delete_data", "delete_data", "Unrelated destructive operation."))

    def state(self):
        return {"run_id": "fit-run", "status": self.status, "remaining": self.remaining,
                "budget": self.budget, "max_phases": self.max_phases,
                "cloud_request_count": self.cloud_request_count, "cloud_request_limit": self.cloud_request_limit,
                "evaluate_baseline": self.baseline, "baseline_status": self.baseline_status,
                "baseline_incumbent": {"secret": "DO-NOT-SEND-BO-RESULTS"},
                "incumbent": {"candidate_ids": ["candidate-A", "candidate-B"], "score": 83.25},
                "pywpem_review": {"status": "completed"}}

    def decision(self, draft=None):
        draft = list(draft or [])
        auto = len(draft) == self.max_phases
        choices = {}
        if not auto:
            if self.cloud_request_count >= self.cloud_request_limit:
                raise AgentStateError("持久化云请求预算耗尽")
            legal = [set(c) for n in range(1, self.max_phases + 1) for c in combinations(self.candidate_ids, n) if tuple(sorted(c)) not in self.tried]
            for candidate in self.candidate_ids:
                if candidate not in draft and any(set(draft + [candidate]) <= c for c in legal):
                    choices["add:" + candidate] = {"kind": "add", "candidate_id": candidate, "description": "Add " + candidate}
            if draft and tuple(sorted(draft)) not in self.tried:
                choices["submit"] = {"kind": "submit", "candidate_ids": draft, "description": "Fit the current draft"}
            self.cloud_request_count += 1
        self.last_token = f"decision-{self.remaining}-{self.cloud_request_count}-{'-'.join(draft)}"
        return {"decision_id": self.last_token, "selection_token": self.last_token, "auto_submit": auto,
                "candidate_ids": draft,
                "state": {"status": self.status, "remaining": self.remaining, "budget": self.budget, "max_phases": self.max_phases,
                          "draft_candidate_ids": draft, "pool": [{"candidate_id": c} for c in self.candidate_ids],
                          "cloud_request_count": self.cloud_request_count, "cloud_request_limit": self.cloud_request_limit,
                          "objective": "measured-score", "trials": []},
                "choices": choices, "menu_policy": "all_reachable_actions"}

    async def list_tools(self, agent_id):
        assert agent_id == CONFIG.agent_id
        return self.definitions

    async def invoke_tool(self, agent_id, capability_id, arguments):
        assert agent_id == CONFIG.agent_id
        assert arguments["target"] == "harness"
        assert capability_id.startswith("operation:xrd_multiphase_")
        action = capability_id.removeprefix("operation:xrd_multiphase_")
        self.calls.append((action, deepcopy(arguments)))
        if action == "start":
            assert self.status == "idle"
            self.status = "running"
        elif action == "decision":
            return self.decision(arguments["draft_candidate_ids"])
        elif action == "evaluate":
            assert self.remaining > 0
            assert arguments["selection_token"] == self.last_token
            ids = arguments["candidate_ids"]
            assert tuple(sorted(ids)) not in self.tried
            path = arguments["construction_path"]
            assert path[0]["draft_before"] == []
            assert path[-1]["draft_after"] == ids
            assert path[-1]["kind"] in {"submit", "auto_submit"}
            assert sum(p["kind"] == "add" for p in path) == len(ids)
            self.tried.add(tuple(sorted(ids)))
            self.remaining -= 1
        elif action == "baseline":
            assert self.remaining == 0
            assert self.baseline
            self.baseline_status = "completed"
        elif action == "finish":
            assert self.remaining == 0
            assert not self.baseline or self.baseline_status == "completed"
            self.status = "completed"
        elif action == "stop":
            self.status = "failed" if arguments.get("reason") == "controller_failed" else "cancelled"
        return self.state()


def response_body(keys=None, choice=None):
    keys = list(keys or ["add:A", "add:B"])
    choice = choice or keys[0]
    probabilities = {key: (1. if key == choice else 0.) for key in keys}
    return {"model": "jev-1.13.0", "answers": {"next_action": {"type": "choice", "choice": choice,
            "confidence": .72, "probabilities": probabilities}}, "usage": {"input_tokens": 41, "output_tokens": 11}}


def respond_greedy(request):
    keys = json.loads(request.content)["questions"]["next_action"]["criteria"]
    return httpx.Response(200, json=response_body(keys))


async def collect(provider, client=None):
    return [event async for event in execute_typesafe(provider, CONFIG, CONTEXT, MODEL, provider.definitions, client=client)]


@pytest.mark.asyncio
async def test_bounded_lifecycle_scoped_tools_and_audit_no_baseline_leak():
    harness = FakeHarness()
    requests = []

    def respond(request):
        assert str(request.url) == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer secret-jev"
        body = json.loads(request.content)
        assert body["model"] == MODEL.model
        assert body["questions"]["next_action"]["instructions"] == CONFIG.system_instruction
        assert "DO-NOT-SEND-BO-RESULTS" not in request.content.decode()
        assert "delete_data" not in request.content.decode()
        requests.append(body)
        return respond_greedy(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        events = await collect(harness, client)
    assert len(requests) == harness.cloud_request_count == 4
    assert [name for name, _ in harness.calls] == ["read", "start", "decision", "decision", "decision", "evaluate", "decision", "decision", "evaluate", "baseline", "finish"]
    fits = [args for name, args in harness.calls if name == "evaluate"]
    assert [args["candidate_ids"] for args in fits] == [["A", "B"], ["A"]]
    assert fits[0]["construction_path"][-1]["kind"] == "auto_submit"
    assert fits[1]["construction_path"][-1]["kind"] == "submit"
    assert events[-1].run_status == RunStatus.SUCCEEDED
    assert "83.25" in events[-1].payload["text"]
    audit = [event.payload["structured_decision"] for event in events if "structured_decision" in event.payload]
    assert len(audit) == 4
    assert audit[0]["model"] == "jev-1.13.0"
    assert audit[0]["usage"] == {"input_tokens": 41, "output_tokens": 11}
    assert audit[0]["probabilities"]["add:A"] == 1
    assert audit[0]["latency_seconds"] >= 0
    assert "secret-jev" not in repr(events)
    assert "secret-jev" not in repr(MODEL)


@pytest.mark.asyncio
async def test_four_phase_capacity_submits_without_fifth_cloud_call():
    harness = FakeHarness(remaining=1, baseline=False, max_phases=4, candidate_ids=["A", "B", "C", "D"])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_greedy)) as client:
        events = await collect(harness, client)
    assert harness.cloud_request_count == harness.cloud_request_limit == 4
    args = next(args for name, args in harness.calls if name == "evaluate")
    assert args["candidate_ids"] == ["A", "B", "C", "D"]
    assert [step["kind"] for step in args["construction_path"]] == ["add", "add", "add", "add", "auto_submit"]
    assert len([e for e in events if "structured_decision_request" in e.payload]) == 4
    assert events[-1].run_status == RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_first_combination_can_submit_one_phase_without_fixed_initialization():
    harness = FakeHarness(remaining=1, baseline=False, max_phases=3)
    def submit_early(request):
        keys = json.loads(request.content)["questions"]["next_action"]["criteria"]
        return httpx.Response(200, json=response_body(keys, "submit" if "submit" in keys else next(iter(keys))))
    async with httpx.AsyncClient(transport=httpx.MockTransport(submit_early)) as client:
        await collect(harness, client)
    args = next(args for name, args in harness.calls if name == "evaluate")
    assert args["candidate_ids"] == ["A"]
    assert [step["kind"] for step in args["construction_path"]] == ["add", "submit"]
    assert harness.cloud_request_count == 2


@pytest.mark.asyncio
async def test_persisted_request_budget_does_not_reset_when_resuming():
    harness = FakeHarness(remaining=1, status="running", baseline=False, max_phases=2)
    harness.cloud_request_count = 1  # Earlier interrupted attempt already reserved.
    seen = []
    def respond(request):
        seen.append(request)
        return respond_greedy(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AgentStateError, match="预算耗尽"):
            await collect(harness, client)
    assert len(seen) == 1
    assert harness.cloud_request_count == harness.cloud_request_limit == 2
    assert not any(name == "evaluate" for name, _ in harness.calls)
    assert harness.calls[-1][1]["reason"] == "controller_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["repeated-token", "duplicate-add", "stale-draft", "count-reset"])
async def test_mid_construction_contract_violation_stops_before_another_cloud_call(corruption):
    class BrokenDraftHarness(FakeHarness):
        def decision(self, draft=None):
            decision = super().decision(draft)
            if not draft:
                self.first_token = decision["selection_token"]
            else:
                if corruption == "repeated-token":
                    decision["selection_token"] = self.first_token
                elif corruption == "duplicate-add":
                    decision["choices"]["repeat"] = {"kind": "add", "candidate_id": draft[0], "description": "bad repeat"}
                elif corruption == "stale-draft":
                    decision["state"]["draft_candidate_ids"] = []
                else:
                    decision["state"]["cloud_request_count"] = 1
            return decision
    harness = BrokenDraftHarness(remaining=1, baseline=False)
    seen = []
    def respond(request):
        seen.append(request)
        return respond_greedy(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(AgentStateError):
            await collect(harness, client)
    assert len(seen) == 1
    assert not any(name == "evaluate" for name, _ in harness.calls)
    assert harness.status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("submit_early", [False, True])
async def test_runtime_path_replays_against_production_harness_protocol(submit_early):
    from matcreator.xrd.vendor.multiphase_decision import build_decision, validate_construction

    class ProductionProtocol(FakeHarness):
        def __init__(self):
            super().__init__(remaining=1, baseline=False)
            self.payload = {"candidates": [{"candidate_id": c, "label": c} for c in self.candidate_ids]}
            self.evidence = {"candidates": [{"candidate_id": c, "full_reference_peaks": [[21.12, 100], [77.71, .0123]]} for c in self.candidate_ids]}
            self.science_state = {"run_id": "real-protocol", "status": "running", "options": {"budget": 1, "max_phases": 2},
                                  "trials": [], "cloud_request_count": 0, "cloud_request_limit": 2, "decision_requests": []}
            self.verified = None

        def decision(self, draft=None):
            draft = list(draft or [])
            kwargs = {"objective": "measured-score", "claim_scope": "test", "draft_candidate_ids": draft, "evidence": self.evidence}
            decision = build_decision(self.science_state, self.payload, **kwargs)
            if not decision["auto_submit"]:
                self.cloud_request_count += 1
                self.science_state["cloud_request_count"] = self.cloud_request_count
                decision = build_decision(self.science_state, self.payload, **kwargs)
            self.last_token = decision["selection_token"]
            record = {"selection_token": self.last_token, "draft_candidate_ids": draft,
                      "cloud_request_count": self.cloud_request_count, "evaluation_index": 1, "status": "reserved"}
            self.science_state["decision_requests"].append(record)
            self.science_state["pending_decision"] = record
            return decision

        async def invoke_tool(self, agent_id, capability_id, arguments):
            if capability_id.endswith("_evaluate"):
                self.verified = validate_construction(self.science_state, self.payload, self.evidence,
                    arguments["candidate_ids"], arguments["selection_token"], arguments["construction_path"],
                    objective="measured-score", claim_scope="test")
            return await super().invoke_tool(agent_id, capability_id, arguments)

    harness = ProductionProtocol()
    def respond(request):
        body = json.loads(request.content)
        assert body["state"]["template"] == "xrd_compact_v1"
        assert len(body["state"]["candidates"]) == len(harness.candidate_ids)
        keys = body["questions"]["next_action"]["criteria"]
        choice = "submit" if submit_early and "submit" in keys else next((key for key in keys if key.startswith("add:")), "submit")
        return httpx.Response(200, json=response_body(keys, choice))
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await collect(harness, client)
    assert harness.verified["method"] == "jev_autoregressive_v2"
    assert harness.verified["cloud_requests"] == 2
    assert harness.verified["construction_path"][-1]["kind"] == ("submit" if submit_early else "auto_submit")


@pytest.mark.asyncio
async def test_existing_run_without_baseline_and_completed_run_do_not_restart():
    harness = FakeHarness(remaining=0, status="running", baseline=False)
    await collect(harness)
    assert [name for name, _ in harness.calls] == ["read", "finish"]
    harness.calls.clear()
    await collect(harness)
    assert [name for name, _ in harness.calls] == ["read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_does_not_silently_restart_cancelled_or_failed_runs(status):
    harness = FakeHarness(status=status)
    with pytest.raises(AgentStateError, match="不会擅自重启"):
        await collect(harness)
    assert [name for name, _ in harness.calls] == ["read"]


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["outside-menu", "missing-probability", "nan", "bad-sum", "missing-usage", "wrong-model"])
async def test_invalid_choice_never_evaluates_and_stops_worker(corruption):
    harness = FakeHarness(status="running")
    body = response_body()
    answer = body["answers"]["next_action"]
    if corruption == "outside-menu":
        answer["choice"] = "execute-python"
    elif corruption == "missing-probability":
        del answer["probabilities"]["add:A"]
    elif corruption == "nan":
        answer["confidence"] = "NaN"
    elif corruption == "bad-sum":
        answer["probabilities"]["add:A"] = .9
    elif corruption == "missing-usage":
        del body["usage"]
    else:
        body["model"] = "unrequested-model"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        with pytest.raises(AgentStateError, match="不合法"):
            await collect(harness, client)
    assert [name for name, _ in harness.calls] == ["read", "decision", "stop"]
    assert harness.calls[-1][1]["reason"] == "controller_failed"
    assert harness.status == "failed"


@pytest.mark.asyncio
async def test_timeout_not_retried_or_leaked_and_worker_stops():
    harness = FakeHarness(status="running")
    requests = []

    def fail(request):
        requests.append(request)
        raise httpx.ReadTimeout("secret-jev and private spectrum", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(AgentStateError, match="ReadTimeout") as raised:
            await collect(harness, client)
    assert len(requests) == 1
    assert "secret-jev" not in str(raised.value)
    assert "private spectrum" not in str(raised.value)
    assert harness.calls[-1][0] == "stop"
    assert harness.calls[-1][1]["reason"] == "controller_failed"


@pytest.mark.asyncio
async def test_cancellation_during_http_propagates_and_stops_scoped_worker():
    harness = FakeHarness(status="running")
    entered = asyncio.Event()

    async def pending(request):
        entered.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(pending)) as client:
        task = asyncio.create_task(collect(harness, client))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert [name for name, _ in harness.calls] == ["read", "decision", "stop"]
    assert harness.calls[-1][1]["reason"] == "controller_cancelled"


@pytest.mark.asyncio
async def test_non_consuming_harness_fails_closed_instead_of_looping():
    class BrokenHarness(FakeHarness):
        async def invoke_tool(self, agent_id, capability_id, arguments):
            result = await super().invoke_tool(agent_id, capability_id, arguments)
            if capability_id.endswith("_evaluate"):
                self.remaining += 1
                return self.state()
            return result

    harness = BrokenHarness(status="running")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_greedy)) as client:
        with pytest.raises(AgentStateError, match="未按约定"):
            await collect(harness, client)
    assert [name for name, _ in harness.calls].count("evaluate") == 1
    assert harness.calls[-1][0] == "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["missing-tool", "forged-scope", "multiple-targets", "missing-target"])
async def test_capability_family_rejects_ambiguous_or_forged_authorization(case):
    harness = FakeHarness()
    if case == "missing-tool":
        harness.definitions = harness.definitions[1:]
    elif case == "forged-scope":
        first = harness.definitions[0]
        harness.definitions[0] = ScopedToolDefinition("operation:delete_data", first.name, first.description, first.parameters, first.input_schema)
    else:
        for definition in harness.definitions[:-1]:
            if case == "multiple-targets":
                definition.input_schema["properties"]["target"]["enum"].append("another")
            else:
                del definition.input_schema["properties"]["target"]
    with pytest.raises(AgentStateError):
        await collect(harness)
    assert not harness.calls


@pytest.mark.asyncio
async def test_http_error_body_is_not_exposed():
    harness = FakeHarness()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, text="secret-jev private data"))) as client:
        with pytest.raises(AgentStateError, match="HTTP 401") as raised:
            await collect(harness, client)
    assert "secret-jev" not in str(raised.value)
    assert "private data" not in str(raised.value)


@pytest.mark.asyncio
async def test_oversized_context_never_sent():
    decision = FakeHarness().decision()
    decision["state"]["large"] = "a" * (512 * 1024)
    def unexpected(request):
        pytest.fail("Oversized context must be rejected before the network request")
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(AgentStateError, match="24 KB"):
            await request_choice(client, MODEL, "x" * 25000, decision)


@pytest.mark.asyncio
@pytest.mark.parametrize("probabilities, expected_argmax", [
    ({"add:A": .71, "add:B": .28}, True),
    ({"add:A": .2, "add:B": .8}, False),
])
async def test_quantized_probabilities_and_explicit_choice_preserved(probabilities, expected_argmax):
    # Real Jev 1.13.0 pilot responses included a probability sum of 0.99 and
    # a legal explicit Choice differing from the reported-probability argmax.
    body = response_body()
    body["answers"]["next_action"]["probabilities"] = probabilities
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as client:
        audit = await request_choice(client, MODEL, CONFIG.system_instruction, FakeHarness().decision())
    assert audit["choice"] == "add:A"
    assert audit["probabilities"] == probabilities
    assert audit["choice_is_argmax"] is expected_argmax
    assert audit["probability_sum"] == sum(probabilities.values())


@pytest.mark.asyncio
async def test_evaluation_cancellation_cleans_up_and_preserves_cancelled_error():
    entered = asyncio.Event()
    class PendingHarness(FakeHarness):
        async def invoke_tool(self, agent_id, capability_id, arguments):
            if capability_id.endswith("_evaluate"):
                entered.set()
                await asyncio.Event().wait()
            return await super().invoke_tool(agent_id, capability_id, arguments)
    harness = PendingHarness(status="running")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond_greedy)) as client:
        task = asyncio.create_task(collect(harness, client))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert harness.calls[-1][0] == "stop"


@pytest.mark.asyncio
@pytest.mark.parametrize("close_after", ["read", "finish"])
async def test_stream_closed_at_tool_completion_stops_only_active_worker(close_after):
    harness = FakeHarness(remaining=0, status="running", baseline=False)
    stream = execute_typesafe(harness, CONFIG, CONTEXT, MODEL, harness.definitions)
    async for event in stream:
        if event.type == AgentEventType.TOOL_COMPLETED and event.payload["name"] == "xrd_multiphase_" + close_after:
            break
    await stream.aclose()
    if close_after == "read":
        assert harness.calls[-1][0] == "stop"
        assert harness.status == "cancelled"
    else:
        assert harness.calls[-1][0] == "finish"
        assert harness.status == "completed"


@pytest.mark.parametrize("base_url, expected", [("", "https://api.typesafe.ai/v1/systemone"),
    ("https://api.typesafe.ai/v1/", "https://api.typesafe.ai/v1/systemone")])
def test_typesafe_endpoint_and_secret_repr(base_url, expected):
    model = TypeSafeModel("jev-latest", base_url=base_url, api_key="secret")
    assert model.endpoint == expected
    assert "secret" not in repr(model)


def test_compact_evidence_retains_weak_candidates_and_negative_evidence():
    from matcreator.xrd.jev_controller import compact_xrd_state
    source = {'pool':[{'candidate_id':'long-id','formula':'A','search_score':1,'full_reference_peaks':[[20,100],[40,80]],'matched_peak_positions':[{'observed':20}]}], 'observed_peaks':[{'two_theta':20,'intensity':100}], 'draft_candidate_ids':['long-id'], 'trials':[]}
    compact = compact_xrd_state(source)
    assert compact['draft'] == ['P1']
    assert compact['candidates'][0][2] == 1
    assert compact['candidates'][0][3] == ['E1']
    assert compact['candidates'][0][5] == [[40,80]]
    assert 'long-id' not in json.dumps(compact)
    assert source['pool'][0]['full_reference_peaks'] == [[20,100],[40,80]]


@pytest.mark.parametrize('failed', [False, True])
def test_post_search_recommends_three_without_starting_science(failed):
    class Reviews(FakeHarness):
        def __init__(self):
            super().__init__(remaining=0, status='completed', baseline=False)
            self.recommendation = None
        def state(self):
            return {**super().state(), 'review_candidates': [
                {'candidate_ids': [str(i)], 'labels': ['phase '+str(i)], 'status': 'completed', 'score': 80-i}
                for i in range(4)], 'review_recommendations': self.recommendation}
        async def invoke_tool(self, agent_id, capability_id, arguments):
            if 'review_recommendations' in arguments:
                self.recommendation = arguments['review_recommendations']
            return await super().invoke_tool(agent_id, capability_id, arguments)
    provider = Reviews()
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503) if failed else respond_greedy(request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [event async for event in execute_typesafe(provider, CONFIG, CONTEXT, MODEL, provider.definitions, client=client)]
    asyncio.run(run())
    assert [a for a, _ in provider.calls] == ['read', 'finish']
    assert provider.recommendation['status'] == ('failed' if failed else 'completed')
    assert len(calls) == (1 if failed else 3)
    if not failed:
        assert len({tuple(c) for c in provider.recommendation['combinations']}) == 3
        assert len(provider.recommendation['audit']) == 3
