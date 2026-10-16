"""Finite Jev decisions for the ordinary Agent card's scoped XRD harness.

Jev is not a chat or tool-calling model. This controller owns the lifecycle and
Jev constructs each combination through ADD/SUBMIT actions from the complete
legal candidate pool, with no quality-ranked combination shortlist.
The broker reauthorizes every operation; no resource or plugin is called direct.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
import json
import math
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

# MC adapter: service authorizes the single workspace before each operation.
from types import SimpleNamespace
AgentStateError = ValueError
AgentCapabilityProvider = AgentConfig = InvocationContext = ScopedToolDefinition = Any
class AgentEventType:
    TOOL_STARTED='tool_started'; TOOL_COMPLETED='tool_completed'; MESSAGE='message'; COMPLETED='completed'
class RunStatus:
    SUCCEEDED='succeeded'
def AgentEvent(agent_id, run_id, kind, payload, **kwargs):
    return SimpleNamespace(agent_id=agent_id, run_id=run_id, kind=kind, type=kind, payload=payload, **kwargs)


_ACTIONS = ("read", "start", "decision", "evaluate", "baseline", "finish", "stop")
_PREFIX = "xrd_multiphase_"
_MAX_DECISIONS = 100
_MAX_REQUEST_BYTES = 512 * 1024


@dataclass(frozen=True)
class TypeSafeModel:
    model: str
    base_url: str = ""
    api_key: str = field(default="", repr=False)

    @property
    def endpoint(self) -> str:
        base = (self.base_url or "https://api.typesafe.ai").rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AgentStateError("TypeSafe 连接地址无效，请检查模型设置。")
        return base + ("/systemone" if parsed.path.endswith("/v1") else "/v1/systemone")


def _scoped_family(definitions: Sequence[ScopedToolDefinition]):
    selected = {}
    targets = None
    for action in _ACTIONS:
        name = _PREFIX + action
        matches = [item for item in definitions if item.name == name]
        if len(matches) != 1 or matches[0].capability_id != "operation:" + name:
            raise AgentStateError("Jev 是结构化决策模型，不支持自由聊天。请连接一个完整的多相筛选 Harness，再从多相搜索启动此 Agent。")
        definition = matches[0]
        schema = definition.input_schema or {}
        target_schema = schema.get("properties", {}).get("target", {})
        allowed = target_schema.get("enum", [])
        if "target" not in schema.get("required", []) or not isinstance(allowed, list) or not allowed or any(not isinstance(value, str) or not value for value in allowed):
            raise AgentStateError("Jev 需要带有明确 target 选择器的多相工具授权。")
        targets = set(allowed) if targets is None else targets.intersection(allowed)
        selected[action] = definition
    if len(targets or ()) != 1:
        raise AgentStateError("Jev 必须唯一连接一个具有完整工具授权的多相筛选 Harness。")
    # Reuse the same schema/name validation as ADK, without constructing or
    # executing any of the Agent's unrelated capabilities.
    # The complete family and single target are validated above.
    return selected, next(iter(targets))


def _remaining(state: Mapping[str, Any]) -> int:
    value = state.get("remaining")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= _MAX_DECISIONS:
        raise AgentStateError("多相 Harness 返回了无效的剩余评估预算。")
    return value


def _finite_probability(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _max_phases(state: Mapping[str, Any]) -> int:
    value = state.get("max_phases")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 4:
        raise AgentStateError("多相 Harness 返回了无效的物相数上限。")
    return value


def _ids(value, *, allow_empty=False):
    return (isinstance(value, list) and (allow_empty or bool(value)) and len(value) <= 4
            and all(isinstance(item, str) and item for item in value) and len(set(value)) == len(value))


def _decision_menu(decision: Any):
    if not isinstance(decision, dict) or not isinstance(decision.get("state"), dict):
        raise AgentStateError("多相 Harness 未返回结构化决策上下文。")
    token = decision.get("selection_token", decision.get("decision_id"))
    choices = decision.get("choices")
    state = decision["state"]
    draft = state.get("draft_candidate_ids")
    maximum = _max_phases(state)
    pool = state.get("pool")
    if not isinstance(pool, list) or not 1 <= len(pool) <= 30 or any(not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str) for item in pool):
        raise AgentStateError("多相 Harness 返回了无效的候选池。")
    pool_ids = {item["candidate_id"] for item in pool}
    if not _ids(draft, allow_empty=True) or len(draft) > maximum or not set(draft).issubset(pool_ids):
        raise AgentStateError("多相 Harness 返回了无效的构造草案。")
    if not isinstance(token, str) or not token or not isinstance(choices, dict) or len(choices) > len(pool_ids) + 1:
        raise AgentStateError("多相 Harness 的决策菜单或令牌无效。")
    if decision.get("auto_submit") is True:
        if choices or len(draft) != maximum or decision.get("candidate_ids") != draft:
            raise AgentStateError("多相 Harness 的自动提交状态无效。")
        return token, choices
    if not choices or len(draft) == maximum:
        raise AgentStateError("多相 Harness 没有合法构造动作，或已达到物相数上限。")
    additions = set()
    submit_count = 0
    for key, value in choices.items():
        if not isinstance(key, str) or not key or not isinstance(value, dict) or not isinstance(value.get("description"), str):
            raise AgentStateError("多相 Harness 的构造动作无效。")
        if value.get("kind") == "add":
            candidate = value.get("candidate_id")
            if not isinstance(candidate, str) or candidate not in pool_ids or candidate in draft or candidate in additions:
                raise AgentStateError("多相 Harness 的 ADD 动作重复或超出候选池。")
            additions.add(candidate)
        elif value.get("kind") == "submit":
            if not draft or value.get("candidate_ids") != draft or submit_count:
                raise AgentStateError("多相 Harness 的 SUBMIT 动作与草案不一致。")
            submit_count += 1
        else:
            raise AgentStateError("多相 Harness 返回了未知构造动作。")
    return token, choices


def compact_xrd_state(state: dict) -> dict:
    """Decision-only evidence; authoritative data and legality remain in the harness."""
    pool = state.get('pool', [])
    codes = {p['candidate_id']: p.get('phase_code', f'P{i+1}') for i, p in enumerate(pool)}
    def num(v):
        return round(v, 3) if isinstance(v, (int, float)) and math.isfinite(v) else None
    observed = state.get('observed_peaks', [])
    residual = sorted((state.get('incumbent') or {}).get('residual_peaks', []), key=lambda p: -p.get('unexplained_intensity', 0))[:8]
    def trial(t):
        return [[codes.get(c, '?') for c in t.get('candidate_ids', [])], num(t.get('objective')),
                num(t.get('validation', {}).get('metrics', {}).get('rwp_percent')), t.get('status'), t.get('converged')]
    candidates = []
    for p in pool:
        refs = p.get('full_reference_peaks', [])
        matched = sorted({f'E{i+1}' for i, e in enumerate(observed) if any(abs(m.get('observed', -999)-e['two_theta']) < .002 for m in p.get('matched_peak_positions', []))})
        covered = [f'R{i+1}' for i, r in enumerate(residual) if any(abs(x[0]-r['two_theta']) <= .15 for x in refs)]
        missing = []
        for angle, strength in sorted(refs, key=lambda x: -x[1]):
            if strength < 10 or any(abs(angle-e['two_theta']) <= .15 for e in observed):
                continue
            if any(abs(angle-x[0]) <= .05 for x in missing):
                continue
            missing.append([num(angle), num(strength)])
            if len(missing) == 6:
                break
        candidates.append([codes[p['candidate_id']], p.get('formula', '')[:100], num(p.get('search_score')), matched, covered, missing])
    incumbent = state.get('incumbent')
    return {'template': 'xrd_compact_v1', 'max_phases': state.get('max_phases'), 'remaining': state.get('remaining'),
        'draft': [codes.get(c, '?') for c in state.get('draft_candidate_ids', [])],
        'columns': {'peaks': ['id','two_theta_deg','intensity'], 'candidates': ['id','formula','initial_score','matched_experimental_peaks','residual_coverage','strong_reference_peaks_not_in_peak_summary'], 'history': ['phases','objective_lower_better','validation_Rwp_percent','status','converged']},
        'experimental_peaks': [[f'E{i+1}',num(p['two_theta']),num(p.get('intensity'))] for i,p in enumerate(observed)],
        'residual_peaks': [[f'R{i+1}',num(p['two_theta']),num(p.get('unexplained_intensity'))] for i,p in enumerate(residual)],
        'candidates': candidates, 'history': [trial(t) for t in state.get('trials', [])],
        'best': trial(incumbent) if incumbent else None,
        'best_area_fractions': [[codes.get(p['candidate_id'],'?'),num(p.get('profile_area_fraction'))] for p in (incumbent or {}).get('phase_contributions', [])],
        'scope': 'All candidates retained. Initial score is not fitted objective. Reference absence is only a peak-summary proxy (0.15 deg tolerance, top 6 distinct peaks >=10 relative intensity), not proof of physical absence. Residual coverage uses 0.15 deg tolerance. Keep weak impurity hypotheses. Area fractions are not mass fractions. Full patterns and complete evidence remain in numerical evaluator.'}


async def request_choice(client: httpx.AsyncClient, model: TypeSafeModel, instruction: str, decision: dict, *, review_menu=False) -> dict:
    token, choices = (decision['decision_id'], decision['choices']) if review_menu else _decision_menu(decision)
    if not choices:
        raise AgentStateError("已达物相数上限，应直接提交，不应额外调用 Jev。")
    request = {
        "model": model.model,
        # Do not forward read/start summaries, other optimizer arms, runtime
        # prompt, conversation history or unrestricted tool definitions.
        "state": decision['state'] if review_menu else compact_xrd_state(decision["state"]),
        "questions": {"next_action": {"type": "choice", "instructions": instruction,
            "criteria": {key: value["description"] for key, value in choices.items()}}},
    }
    try:
        serialized = json.dumps(request, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError):
        raise AgentStateError("Jev 决策摘要含不可序列化或非有限数值。") from None
    if len(serialized) > 24000:
        raise AgentStateError("Jev 紧凑输入超过 24 KB 保守预算；未发送请求，请减少历史预算或简化 Agent 指令。")
    started = time.monotonic()
    try:
        response = await client.post(model.endpoint, headers={"Authorization": "Bearer " + model.api_key}, json=request)
    except httpx.RequestError as exc:
        # Provider errors may embed request bodies/headers. Report only type.
        raise AgentStateError(f"Jev 请求失败（{type(exc).__name__}）；已停止本轮，不会自动重试或替换优化器。") from None
    if response.status_code != 200:
        try:
            detail = response.json().get('detail', {})
            error_type = detail.get('error_type', '') if isinstance(detail, dict) else ''
        except (ValueError, AttributeError):
            error_type = ''
        safe_detail = '：输入超过服务端 token 上限' if error_type == 'max_tokens_exceeded' else ''
        raise AgentStateError(f"Jev 服务返回 HTTP {response.status_code}{safe_detail}；本轮已停止。")
    try:
        if len(response.content) > 2_000_000:
            raise ValueError()
        raw = response.json()
        answer = raw["answers"]["next_action"]
        choice = answer["choice"]
        probabilities = answer["probabilities"]
        confidence = answer["confidence"]
        actual_model = raw["model"]
        usage = raw["usage"]
        if answer.get("type") != "choice" or choice not in choices or not _finite_probability(confidence):
            raise ValueError()
        # The API quantizes probabilities: real responses can sum to 0.99.
        # Its explicit Choice is authoritative even when not the largest
        # reported probability. Preserve both without renormalizing or silently
        # substituting an argmax selection; record the discrepancy for audit.
        if not isinstance(probabilities, dict) or set(probabilities) != set(choices) or not all(_finite_probability(value) for value in probabilities.values()) or abs(sum(probabilities.values()) - 1) > .01 + 1e-8:
            raise ValueError()
        if not isinstance(actual_model, str) or not actual_model or len(actual_model) > 200 or not isinstance(usage, dict):
            raise ValueError()
        if model.model != "jev-latest" and actual_model != model.model:
            raise ValueError()
        counts = {key: usage[key] for key in ("input_tokens", "output_tokens")}
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in counts.values()):
            raise ValueError()
    except (KeyError, ValueError, TypeError, AttributeError):
        raise AgentStateError("Jev 返回了不合法的 Choice 或概率数据；未提交拟合，也不会自动回退。") from None
    return {"decision_id": decision.get("decision_id", token), "selection_token": token,
            "choice": choice, "kind": choices[choice]["kind"],
            **({"candidate_id": choices[choice]["candidate_id"]} if choices[choice]["kind"] == "add"
               else {"candidate_ids": list(choices[choice]["candidate_ids"])}), "model": actual_model,
            "confidence": confidence, "probabilities": probabilities, "usage": counts,
            "probability_sum": sum(probabilities.values()),
            "choice_is_argmax": probabilities[choice] + 1e-8 >= max(probabilities.values()),
            "latency_seconds": round(time.monotonic() - started, 6), "menu_policy": decision.get("menu_policy")}


def _summary(state: Mapping[str, Any]) -> str:
    best = state.get("incumbent") or {}
    labels = best.get("labels") or best.get("candidate_ids") or []
    if not best:
        return "多相搜索已结束，但没有成功的实测组合。"
    review = state.get("pywpem_review") or {}
    return (f"Jev 多相搜索完成。实测最佳组合：{' + '.join(map(str, labels))}；"
            f"得分 {best.get('score', '未提供')}；联合复核：{review.get('status', '未启用')}。"
            "模型置信度不是物相确认概率；预算完成不代表精修收敛。")


async def execute_typesafe(
    provider: AgentCapabilityProvider,
    config: AgentConfig,
    context: InvocationContext,
    model: TypeSafeModel,
    definitions: Sequence[ScopedToolDefinition],
    *,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[AgentEvent]:
    family, target = _scoped_family(definitions)
    if not model.api_key or model.api_key == "oaw-no-auth":
        raise AgentStateError("TypeSafe / Jev 需要 API key，请在模型设置中配置。")
    state: dict = {}
    owns_running = False
    result: Any = None

    def event(kind, payload, **kwargs):
        return AgentEvent(config.agent_id, context.run_id, kind, payload, **kwargs)

    async def invoke(action, **arguments):
        nonlocal result, owns_running
        definition = family[action]
        args = {"target": target, **arguments}
        yield event(AgentEventType.TOOL_STARTED, {"name": definition.name, "arguments": args})
        result = await provider.invoke_tool(config.agent_id, definition.capability_id, args)
        if not isinstance(result, dict) or result.get("ok") is False:
            raise AgentStateError(f"多相工具 {definition.name} 未返回有效结果。")
        if action in {"read", "start", "finish"}:
            # Account for stop while suspended at TOOL_COMPLETED, before the
            # controller resumes and consumes this result.
            owns_running = result.get("status") == "running"
        # Decisions are summarized, never copied wholesale into the event log.
        summary = {key: result.get(key) for key in ("status", "remaining", "run_id", "decision_id") if key in result}
        yield event(AgentEventType.TOOL_COMPLETED, {"name": definition.name, "response": summary})

    async def cleanup(reason):
        try:
            await asyncio.wait_for(provider.invoke_tool(config.agent_id, family["stop"].capability_id, {"target": target, "reason": reason}), timeout=15)
        except Exception as exc:
            return type(exc).__name__
        return None

    async def recommend_reviews():
        nonlocal state
        candidates = state.get('review_candidates')
        if not candidates or (state.get('review_recommendations') or {}).get('status') == 'completed':
            return
        selected, audits = [], []
        recommendation = {'status': 'completed', 'combinations': selected, 'audit': audits, 'model': model.model}
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as review_client:
                for index in range(min(3, len(candidates))):
                    choices = {f'C{i + 1}': {'kind': 'submit', 'candidate_ids': t['candidate_ids'],
                        'description': json.dumps({k: t[k] for k in ('labels', 'score', 'metrics', 'converged') if k in t}, ensure_ascii=False)}
                        for i, t in enumerate(candidates) if t['candidate_ids'] not in selected}
                    decision = {'decision_id': f"{state['run_id']}:review:{index + 1}", 'choices': choices,
                        'state': {'task': 'Select three distinct measured combinations for expensive PyWPEM review, one at a time.',
                            'already_selected': selected, 'scope': 'Screening scores are approximate, not phase identity probabilities. Preserve plausible impurity alternatives; area fractions are not mass fractions.'}}
                    audit = await request_choice(client if client is not None and not client.is_closed else review_client, model,
                        'Select the most useful remaining combination for joint refinement. Prioritize strong measured fit, then complementary phase hypotheses and simpler competing explanations. Do not select three near-duplicate explanations merely by rank.',
                        decision, review_menu=True)
                    selected.append(audit['candidate_ids'])
                    audits.append(audit)
        except Exception as exc:
            recommendation.update(status='failed', error=str(exc) or type(exc).__name__)
        async for item in invoke('finish', run_id=state['run_id'], review_recommendations=recommendation):
            yield item
        state = result

    try:
        async for item in invoke("read"):
            yield item
        state = result
        if state.get("status") == "idle":
            # start can begin a worker before returning or being cancelled.
            owns_running = True
            async for item in invoke("start"):
                yield item
            state = result
        if state.get("status") == "completed" or (state.get('review_candidates') and state.get('pywpem_review', {}).get('status') in {'failed', 'cancelled', 'interrupted'}):
            async for item in recommend_reviews():
                yield item
            text = _summary(state)
            yield event(AgentEventType.MESSAGE, {"text": text, "final": True})
            yield event(AgentEventType.COMPLETED, {"text": text}, run_status=RunStatus.SUCCEEDED)
            return
        if state.get("status") != "running":
            raise AgentStateError("当前多相任务已经停止或失败。请在检索与比对中重新配置并启动，Jev 不会擅自重启旧任务。")
        owns_running = True
        remaining = _remaining(state)
        initial_remaining = remaining
        maximum = _max_phases(state)
        decision_ids = set()
        cloud_calls = 0
        last_reservation = state.get("cloud_request_count")
        cloud_limit = state.get("cloud_request_limit")
        budget = state.get("budget")
        if remaining and (isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= _MAX_DECISIONS
                          or isinstance(last_reservation, bool) or not isinstance(last_reservation, int)
                          or isinstance(cloud_limit, bool) or not isinstance(cloud_limit, int)
                          or not 0 <= last_reservation <= cloud_limit <= budget * maximum):
            raise AgentStateError("多相 Harness 未提供有效的持久化云请求预算。")
        async with AsyncExitStack() as stack:
            if client is None:
                client = await stack.enter_async_context(httpx.AsyncClient(timeout=30.0, follow_redirects=False))
            for _ in range(initial_remaining):
                draft = []
                construction_path = []
                for construction_step in range(1, maximum + 2):
                    async for item in invoke("decision", draft_candidate_ids=list(draft)):
                        yield item
                    decision = result
                    token, choices = _decision_menu(decision)
                    decision_state = decision["state"]
                    if token in decision_ids or decision_state.get("status") != "running" or _remaining(decision_state) != remaining or _max_phases(decision_state) != maximum or decision_state["draft_candidate_ids"] != draft:
                        raise AgentStateError("多相构造状态已重复、过期或变更；已停止，避免重复计费。")
                    decision_ids.add(token)
                    if decision.get("auto_submit") is True:
                        construction_path.append({"kind": "auto_submit", "decision_id": decision.get("decision_id", token),
                            "selection_token": token, "draft_before": list(draft), "draft_after": list(draft),
                            "construction_step": construction_step})
                        break
                    if construction_step > maximum or cloud_calls >= initial_remaining * maximum:
                        raise AgentStateError("Jev 已达到本轮构造请求上限，未进行额外云调用。")
                    count = decision_state.get("cloud_request_count", decision.get("cloud_request_count"))
                    limit = decision_state.get("cloud_request_limit", decision.get("cloud_request_limit"))
                    if (isinstance(count, bool) or not isinstance(count, int) or isinstance(limit, bool)
                            or not isinstance(limit, int) or count != last_reservation + 1 or limit != cloud_limit
                            or not count <= limit <= budget * maximum):
                        raise AgentStateError("多相 Harness 的持久化云请求预留无效或已达上限。")
                    last_reservation = count
                    cloud_calls += 1
                    yield event(AgentEventType.MESSAGE, {
                        "text": "Jev 正在逐步选择物相，或提交当前草案。", "final": False,
                        "structured_decision_request": {"decision_id": decision.get("decision_id", token),
                            "requested_model": model.model, "action_count": len(choices), "draft_candidate_ids": list(draft),
                            "construction_step": construction_step, "cloud_request_count": count, "cloud_request_limit": limit},
                    })
                    audit = await request_choice(client, model, config.system_instruction, decision)
                    after = draft + [audit["candidate_id"]] if audit["kind"] == "add" else list(draft)
                    audit.update(draft_before=list(draft), draft_after=list(after), construction_step=construction_step,
                                 cloud_request_count=count, cloud_request_limit=limit)
                    construction_path.append(audit)
                    yield event(AgentEventType.MESSAGE, {
                        "text": f"Jev 构造动作：{audit['choice']}。", "final": False, "structured_decision": audit,
                    })
                    draft = after
                    if audit["kind"] == "submit":
                        break
                else:
                    raise AgentStateError("Jev 构造未在物相数上限内提交。")
                async for item in invoke("evaluate", candidate_ids=draft, selection_token=token, construction_path=construction_path,
                                         reason=f"Jev {model.model} 逐步 ADD/SUBMIT 构造；决策 {token}"):
                    yield item
                state = result
                next_remaining = _remaining(state)
                if state.get("status") != "running" or next_remaining != remaining - 1:
                    raise AgentStateError("多相 Harness 未按约定消耗一次评估预算；已停止，避免额外调用。")
                remaining = next_remaining
            if remaining:
                raise AgentStateError("Jev 达到本轮有限决策上限，尚未完成评估。")
        if not isinstance(state.get("evaluate_baseline"), bool):
            raise AgentStateError("多相 Harness 未声明是否启用 BO 基线。")
        if state["evaluate_baseline"] and state.get("baseline_status") not in {"completed", "failed"}:
            async for item in invoke("baseline"):
                yield item
            state = result
        async for item in invoke("finish"):
            yield item
        state = result
        if state.get("status") != "completed":
            raise AgentStateError("多相 Harness 未确认完成；不能将此次运行标记为成功。")
        owns_running = False
        async for item in recommend_reviews():
            yield item
        text = _summary(state)
        yield event(AgentEventType.MESSAGE, {"text": text, "final": True})
        yield event(AgentEventType.COMPLETED, {"text": text}, run_status=RunStatus.SUCCEEDED)
    except BaseException as exc:
        if owns_running:
            reason = "controller_cancelled" if isinstance(exc, (asyncio.CancelledError, GeneratorExit)) else "controller_failed"
            cleanup_error = await cleanup(reason)
            if cleanup_error:
                note = f"多相工具停止未确认（{cleanup_error}），请检查 Harness 运行状态。"
                if isinstance(exc, asyncio.CancelledError):
                    exc.add_note(note)
                elif isinstance(exc, Exception):
                    raise AgentStateError(f"{exc}；{note}") from None
        raise
