"""Planning-Execution Orchestrator for MatCreator.

Every invocation runs a planning-first loop:

  1. Planning phase  — always runs first. planning_agent (thinking_agent) handles
                       user intent and sets state["execution_approved"] = True
                       when the user-approved DAG should run. If the flag is not
                       set the turn was conversational; the loop exits and control
                       returns to the user.

  2. Execution phase — delegates the full plan to execution_agent (an LlmAgent that
                       spawns isolated step_executor sub-agents and may run steps in
                       parallel). Terminates early if execution_agent calls `to_planner`
                       (sets state["return_to_planner"] = True).
                       After all steps complete (or on early exit), loops back to
                       the planning phase within the same invocation.

"""

from __future__ import annotations

import json
import logging
import os
from contextlib import aclosing
from pathlib import Path
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from ...workspace import get_workspace_root, init_session_workdir
from ..graph_logger import AgentGraphLogger
from ..cancellation import clear_cancellation
from ...knowledge.extractor import run_knowledge_extractor
from ...knowledge.synthesizer import run_knowledge_synthesizer
from ...knowledge.kg_state import increment_exec_count, record_synthesizer_run
from ...knowledge_schedule import is_knowledge_run_due, knowledge_frequency
from ..execution_agent.recovery import reconcile_recovery_state
from ..execution_graph_state import get_execution_graph

logger = logging.getLogger(__name__)

_DEFAULT_MEMORIZATION_FREQUENCY = 1
_DEFAULT_REVIEW_FREQUENCY = 10
_PLANNING_NODE_STATE_KEY = "_graph_planning_node_id"
_EXECUTION_STREAM_ATTEMPTS = max(
    1, int(os.environ.get("MATCREATOR_EXECUTION_JSON_RETRY_ATTEMPTS", "2"))
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_agent_mode(state: dict) -> str:
    """Return the active agent mode: 'flash', 'bench', or 'normal'."""
    mode = state.get("agent_mode")
    if mode in ("normal", "bench", "flash"):
        return mode
    return "bench" if state.get("benchmark_mode", False) else "normal"


def _validate_graph_ready(state: dict) -> tuple[bool, str]:
    """Return (ready, reason) — ready=True when at least one pending node exists."""
    graph = get_execution_graph(state)
    if not graph or not isinstance(graph, dict):
        return False, "No execution_graph in session state."
    nodes = graph.get("nodes") or {}
    if not nodes:
        return False, "Execution graph has no nodes."
    pending = [nid for nid, n in nodes.items() if n.get("status") == "pending"]
    if not pending:
        return False, f"No pending nodes (all {len(nodes)} node(s) are complete or blocked)."
    return True, f"{len(pending)} pending node(s) ready."


def _is_graph_complete(state: dict) -> bool:
    """Return True when every node in the graph reached 'success'."""
    nodes = (get_execution_graph(state) or {}).get("nodes") or {}
    return bool(nodes) and all(n.get("status") == "success" for n in nodes.values())


def _get_planning_node_id(state: dict, graph: AgentGraphLogger) -> str:
    """Return the graph node representing the session's current planner.

    The orchestrator keeps one planning-agent instance for the session.  A
    planning invocation is therefore activity on that agent, not evidence that
    a new agent was created.  Persisting its graph id in session state keeps
    execution/replanning loops attached to one node.  A future code path that
    deliberately replaces the planning agent can clear this state key before
    creating its replacement node.
    """
    node_id = state.get(_PLANNING_NODE_STATE_KEY)
    if isinstance(node_id, str) and node_id:
        return node_id

    # Preserve the original planner node when resuming a graph written before
    # this state key existed, rather than adding another planning node.
    existing_planners = [
        node for node in graph.nodes_of_type("planning")
        if isinstance(node.get("id"), str)
    ]
    if existing_planners:
        existing_planners.sort(key=lambda node: (node.get("start_time") or "", node["id"]))
        node_id = existing_planners[0]["id"]
    else:
        node_id = "planning_0"

    state[_PLANNING_NODE_STATE_KEY] = node_id
    return node_id


async def _stream_execution_with_recovery(
    execution_agent: BaseAgent,
    ctx: InvocationContext,
    *,
    max_attempts: int = _EXECUTION_STREAM_ATTEMPTS,
) -> AsyncGenerator[Event, None]:
    """Run an execution stream across recoverable model-output failures.

    Step executors persist their result before returning it to the execution
    orchestrator. If the orchestrator's next tool call contains malformed JSON,
    restart the orchestration stream after folding those durable results into
    the graph. The restarted agent therefore schedules only unfinished nodes.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            async with aclosing(execution_agent.run_async(ctx)) as execution_events:
                async for event in execution_events:
                    yield event
            return
        except json.JSONDecodeError:
            recovered = reconcile_recovery_state(
                ctx.session.state,
                ctx.session.state.get("workdir") or get_workspace_root(),
            )
            logger.warning(
                "[orchestrator] malformed execution tool arguments on attempt %d/%d; "
                "recovered state: %s",
                attempt,
                max_attempts,
                recovered,
                exc_info=True,
            )
            if attempt == max_attempts:
                raise


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class PlanningExecutionOrchestrator(BaseAgent):
    """Orchestrates the planning → execution loop.

    Attributes:
        planning_agent: Handles user intent, plan creation, and approval signalling.
        execution_agent: Executes one plan step at a time (skill load + tool calls).
    """

    planning_agent: BaseAgent
    execution_agent: BaseAgent

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        if not state.get("session_workdir_initialized"):
            custom_workdir = state.get("custom_workdir") or None
            if custom_workdir and os.environ.get("MATCREATOR_MODE") == "server":
                try:
                    candidate = Path(custom_workdir).expanduser().resolve()
                    if not candidate.is_relative_to(get_workspace_root()):
                        logger.warning("[orchestrator] custom_workdir %s rejected (outside WORKSPACE_ROOT); using default", custom_workdir)
                        custom_workdir = None
                except Exception:
                    custom_workdir = None

            workdir = init_session_workdir(ctx.session.id, custom_workdir=custom_workdir)
            state["session_id"] = ctx.session.id
            state["workdir"] = str(workdir)
            state["session_workdir_initialized"] = True

        graph = AgentGraphLogger(ctx.session.id)
        graph.log_node_start("orchestrator", "orchestrator", "Orchestrator")

        # Cheap and idempotent: repair the in-memory graph from durable step
        # attempt records before planning/execution resumes after a crash.
        recovered = reconcile_recovery_state(state, state.get("workdir") or get_workspace_root())
        if recovered:
            logger.warning("[orchestrator] recovered execution state: %s", recovered)

        loop_idx = graph.count_nodes_of_type("execution")
        planning_id = _get_planning_node_id(state, graph)

        while True:
            # ── Planning phase (always runs first) ───────────────────────────
            state["execution_approved"] = False
            logger.info("[orchestrator] entering planning phase")
            graph.log_node_start(planning_id, "planning", "Planning", "orchestrator")
            # Approval is a hard handoff boundary. Yield the successful tool
            # response first so clients can persist/render it, then close the
            # planner stream before it can start another model/tool round.
            async with aclosing(self.planning_agent.run_async(ctx)) as planning_events:
                async for event in planning_events:
                    yield event
                    if state.get("execution_approved", False):
                        logger.info("[orchestrator] approval received; ending planning phase")
                        break
            graph.log_node_complete(planning_id, "success")

            # Flash mode: thinking agent handles everything; skip execution phase
            if _get_agent_mode(state) == "flash":
                graph.log_node_complete("orchestrator", "success")
                break

            execution_approved: bool = state.get("execution_approved", False)

            # ── Execution phase ───────────────────────────────────────────────
            if execution_approved:
                ready, reason = _validate_graph_ready(state)
                if not ready:
                    logger.warning("[orchestrator] execution approved but: %s", reason)
                    state["execution_approved"] = False
                    continue  # loop back to planner

                execution_graph = get_execution_graph(state) or {}
                total_nodes = len(execution_graph.get("nodes") or {})
                pending_count = sum(
                    1 for n in execution_graph.get("nodes", {}).values()
                    if n.get("status") == "pending"
                )
                logger.info(
                    "[orchestrator] delegating %d/%d pending nodes to execution_orchestrator",
                    pending_count, total_nodes,
                )

                exec_id = f"execution_{loop_idx}"
                graph.log_node_start(
                    exec_id,
                    "execution",
                    f"Execution {loop_idx + 1}",
                    planning_id,
                    # This remains metadata: all execution nodes continue to
                    # point directly at the original planner. The frontend
                    # uses it to place one planning round on one vine layer.
                    batch_id=f"{planning_id}:round:{loop_idx}",
                )
                state["_graph_exec_node_id"] = exec_id

                async for event in _stream_execution_with_recovery(self.execution_agent, ctx):
                    yield event

                interrupted = state.get("return_to_planner", False)
                if interrupted:
                    reason = state.get("return_to_planner_reason", "unspecified")
                    logger.info(
                        "[orchestrator] execution interrupted — returning to planner (reason: %s)",
                        reason,
                    )
                    graph.log_node_complete(exec_id, "failed", summary=f"Interrupted: {reason}")
                    state["return_to_planner"] = False
                    state["return_to_planner_reason"] = None
                    clear_cancellation(state.get("session_id", ""))
                    if "cancel" in reason:
                        logger.warning(
                            "[CANCEL COMPLETE] Execution fully stopped for session %s — returning to planner",
                            state.get("session_id", ""),
                        )
                elif _is_graph_complete(state):
                    logger.info("[orchestrator] all %d nodes complete", total_nodes)
                    graph.log_node_complete(exec_id, "success")
                    state["_node_exec_counter"] = 0

                    # Knowledge processes are scheduled across completed executions.
                    exec_count = increment_exec_count()
                    memorization_frequency = knowledge_frequency(
                        "MATCREATOR_MEMORIZATION_FREQUENCY",
                        _DEFAULT_MEMORIZATION_FREQUENCY,
                        logger=logger,
                    )
                    review_frequency = knowledge_frequency(
                        "MATCREATOR_REVIEW_FREQUENCY",
                        _DEFAULT_REVIEW_FREQUENCY,
                        logger=logger,
                    )

                    if is_knowledge_run_due(exec_count, memorization_frequency):
                        try:
                            extraction_result = run_knowledge_extractor(ctx.session.id)
                            logger.info(
                                "[orchestrator] knowledge extractor: %s",
                                extraction_result.get("message"),
                            )
                        except Exception as _kg_exc:
                            logger.warning("[orchestrator] knowledge extraction failed: %s", _kg_exc)

                    if is_knowledge_run_due(exec_count, review_frequency):
                        try:
                            synth_result = run_knowledge_synthesizer()
                            record_synthesizer_run()
                            logger.info("[orchestrator] knowledge synthesizer: %s", synth_result.get("message"))
                        except Exception as _kg_exc:
                            logger.warning("[orchestrator] knowledge review failed: %s", _kg_exc)
                else:
                    logger.info("[orchestrator] execution returned with incomplete graph")
                    graph.log_node_complete(
                        exec_id,
                        "needs_replanning",
                        summary="Execution paused with graph nodes still pending, running, or waiting.",
                    )

                state["execution_approved"] = False
                state["current_step"] = None
                loop_idx += 1
                continue  # loop back to planner

            # ── No flag set — planner handled the turn conversationally ───────
            # Exit the loop; in benchmark mode the planning agent is instructed to
            # call confirm_plan_and_start_execution directly, so no fallback needed.
            graph.log_node_complete("orchestrator", "success")
            break
