from __future__ import annotations

import os
import logging
import threading
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.adk.agents.callback_context import CallbackContext

from ...constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from .planning import validate_plan, validate_graph
from .intent import validate_intent
from .summarize import validate_summarize
from .session_summary import write_session_summary
from ...skill import ALL_SKILLS, PLANNING_SKILL_NAMES, ALL_SKILLS_TOOLSET, refresh_skills, seed_skills_to_graph
from .memory import (
    chat_with_knowledge_graph,
    get_related_skills,
    query_knowledge_graph,
    read_memory,
    run_synthesizer,
    save_to_knowledge_graph,
    search_skill_context,
    search_skills,
    update_memory,
)
from ...tools.workspace_tools import (
    get_user_skills_root,
    init_workspace_tool,
    run_bash,
    run_python
)
from ...workspace import get_session_workdir
from ...tools.util_tools import (
    show_artifact,
    show_plot,
    show_structure
)
from .history_tools import read_session_log


logger = logging.getLogger(__name__)

_model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
_model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
_model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)

def _seed_skills_background() -> None:
    try:
        seed_skills_to_graph()
    except Exception as _seed_exc:
        logger.warning("Failed to seed skills into knowledge graph: %s", _seed_exc)

threading.Thread(target=_seed_skills_background, name="seed-skills", daemon=True).start()


def load_skill(skill_name: str) -> dict:
    """Load skill information by name.

    Returns full SKILL.md instructions for concept and guide skills (planning
    reference material). Returns name and description only for execution skills
    — their tool-level details are only needed by the executor.

    Call this after search_skills to get the relevant content.

    Args:
        skill_name: Exact name as returned by search_skills.
    """
    normalized = (skill_name or "").strip()
    skill = next((s for s in ALL_SKILLS if s.name == normalized), None)
    if skill is None:
        lowered = normalized.lower()
        skill = next((s for s in ALL_SKILLS if s.name.lower() == lowered), None)

    if skill is None:
        return {
            "status": "error",
            "message": f"Skill '{skill_name}' not found.",
            "available_skills": sorted(s.name for s in ALL_SKILLS),
        }

    from ...config import get_disabled_skills
    if skill.name in get_disabled_skills():
        return {
            "status": "error",
            "message": f"Skill '{skill_name}' is disabled.",
        }

    if skill.name in PLANNING_SKILL_NAMES:
        return {
            "status": "ok",
            "skill": skill.name,
            "description": skill.description,
            "instructions": skill.instructions,
        }
    return {
        "status": "ok",
        "skill": skill.name,
        "description": skill.description,
    }


def confirm_plan_and_start_execution(tool_context: ToolContext) -> dict:
    """Signal that the user has approved the plan and execution should begin.

    Call this when the user explicitly confirms they want to proceed with the plan
    (e.g. "yes", "proceed", "go ahead").  The orchestrator will then delegate each
    graph node to the execution agent — do NOT execute nodes yourself after calling this.
    """
    from ..cancellation import clear_cancellation

    sid = tool_context.state.get("session_id") or tool_context._invocation_context.session.id
    clear_cancellation(sid)

    # Reset all node statuses so a fresh run starts clean
    graph = tool_context.state.get("execution_graph")
    if graph and isinstance(graph.get("nodes"), dict):
        for node in graph["nodes"].values():
            node["status"] = "pending"
            node["result"] = None
        tool_context.state["execution_graph"] = graph

    tool_context.state["execution_approved"] = True
    tool_context.state["_node_exec_counter"] = 0
    return {
        "status": "ok",
        "message": "Execution approved. STOP making further calls.",
    }


def resume_execution(tool_context: ToolContext) -> dict:
    """Resume execution from the current graph state.

    Call this when the user explicitly requests to continue execution after an interruption.
    """
    graph = tool_context.state.get("execution_graph")
    if not graph or not graph.get("nodes"):
        return {
            "status": "error",
            "message": "No execution_graph found in session state. Create/validate a graph first.",
        }

    pending = [nid for nid, n in graph["nodes"].items() if n.get("status") == "pending"]
    if not pending:
        return {
            "status": "error",
            "message": "No pending nodes remain — all nodes are complete or blocked.",
        }

    tool_context.state["return_to_planner"] = False
    tool_context.state["return_to_planner_reason"] = None
    tool_context.state["execution_approved"] = True

    return {
        "status": "ok",
        "message": (
            f"Execution resume approved. "
            f"The orchestrator will continue with {len(pending)} pending node(s)."
        ),
    }


# ---------------------------------------------------------------------------
# Mode helper
# ---------------------------------------------------------------------------


def _get_agent_mode(state: dict) -> str:
    """Return the active agent mode: 'flash', 'bench', or 'normal'."""
    mode = state.get("agent_mode")
    if mode in ("normal", "bench", "flash"):
        return mode
    return "bench" if state.get("benchmark_mode", False) else "normal"


# ---------------------------------------------------------------------------
# Flash mode: ad-hoc step execution
# ---------------------------------------------------------------------------


async def run_flash_step(
    action: str,
    suggested_skills: list[str],
    tool_context: ToolContext,
    label: Optional[str] = None,
) -> dict:
    """Execute a step ad-hoc. No DAG or plan required.

    Use when direct computation, scripts, or skill-level execution is needed.
    Multiple independent calls in one response turn run concurrently.

    Args:
        action: What to do (same semantics as a DAG node action).
        suggested_skills: Skill names to preload in the executor.
        label: Optional display name shown in the agent graph.
    """
    from ..execution_agent.step_executor_runner import run_step_executor

    counter = tool_context.state.get("_flash_step_counter", 0) + 1
    tool_context.state["_flash_step_counter"] = counter
    node_id = label.lower().replace(" ", "_")[:40] if label else f"flash_{counter}"

    return await run_step_executor(
        step_number=counter,
        action=action,
        suggested_skills=suggested_skills,
        workspace_dir="",
        prior_context=None,
        node_id=node_id,
        tool_context=tool_context,
    )


# ---------------------------------------------------------------------------
# Instructions
# ---------------------------------------------------------------------------

_FLASH_INSTRUCTION = """
You are MatCreator, an AI assistant for computational materials science.
**Flash mode**: act as a direct, conversational assistant.

## Context
- Goal: {goal}

## How to work
- Call `run_flash_step` for computation, or skill execution.
  Multiple independent calls in one response turn run concurrently.
- Call `search_skills` / `load_skill` to discover or load a skill.
- Use `query_knowledge_graph` to retrieve L1/L2 planning knowledge and past memory.
- After selecting a skill, call `search_skill_context` for its attached L3/L4 details.
- For skill creation or evaluation requests, load the `skill-creation` guide and
  call `run_flash_step` with `suggested_skills=["skill-creation"]`.
- After completing work, call `save_to_knowledge_graph` to persist key findings.
- Use `chat_with_knowledge_graph` when the user wants to inspect or update the Know-Do Graph directly.

## Rules
- Be concise and responsive.
- Do NOT call `validate_graph` or `confirm_plan_and_start_execution`.
- Quote exact error messages and propose concrete solutions when something fails.
"""

_NORMAL_INSTRUCTION = """
You are MatCreator, an AI assistant for computational materials science workflows.
Your role here is **PLANNING ONLY**: you are responsible only for planning; all concrete execution steps must be delegated to the execution agent.

## Context
- Goal: {goal}
- Execution graph: {execution_graph}
- Summarize: {summarize}

## Default workflow
1. Determine the user's goal, then call `validate_intent` with your interpretation.
   Call `query_knowledge_graph` with the user's goal to retrieve relevant past knowledge and lessons.
   Call `search_skills` with the user's goal to discover relevant skills and guides.
   After selecting an L1/L2 node, call `search_skill_context` to conditionally search
   only that node's attached L3 heuristics and L4 constraints.
   Use `get_related_skills` to discover its dependencies or closely related workflows.
2. Always draft an execution graph, then call `validate_graph` to validate and commit it.
   Present the plan to the user as a Markdown table with columns:
   **Node ID | Label | Action | Depends On**
   (where "Depends On" lists predecessor node IDs, or "—" for root nodes).
{confirmation_instruction}
4. If the user asks to create or test a skill:
   a. Call `search_skills` / `load_skill` for the `skill-creation` guide.
   b. Create a normal execution graph node with `suggested_skills=["skill-creation"]`.
   c. The node action must say that generated skills belong under the user skills root
      returned by `get_user_skills_root`, not under the workspace skills directory.
5. After completing a node, use `save_to_knowledge_graph` to persist key lessons or findings.
6. Once execution has fully completed, call `write_session_summary` with the global narrative.
7. When the user requests direct Know-Do Graph interaction, call
   `chat_with_knowledge_graph`.

## DAG Planning Guidelines
- **Node IDs**: use descriptive snake_case prefixed with `step_`, e.g. `step_download_data`.
- **Edges**: `[predecessor_id, successor_id]` — predecessor must complete before successor starts.
  Independent nodes (no shared data, no ordering constraint) need no edge and will execute in parallel.
- **Keep graphs small**: 2–4 nodes for simple tasks, 5–7 for complex ones.
  Merge operations that belong to the same skill or logical unit into a single node.
- **validate_graph input shape**:
  ```json
  {{
    "nodes": {{
      "step_download_data": {{
        "node_id": "step_download_data",
        "label": "Download Data",
        "action": "Download VASP output files from the remote server.",
        "suggested_skills": ["filesystem"]
      }},
      "step_relax": {{
        "node_id": "step_relax",
        "label": "Relax Geometry",
        "action": "Run VASP geometry relaxation in the workspace.",
        "suggested_skills": ["vasp"]
      }}
    }},
    "edges": [["step_download_data", "step_relax"]],
    "additional_notes": "Requires VASP 6.x."
  }}
  ```

## Rules
- NEVER execute plan nodes.
- Keep responses concise; reference absolute file paths where relevant.
- When you encounter an error, quote the exact message and propose concrete solutions.
- You may call `run_synthesizer` when the knowledge graph seems stale or after heavy knowledge accumulation.

## Reviewing execution history
- Use the current `execution_graph` state for normal replanning; avoid re-running
    nodes that already succeeded.
- As a last-resort debug path, call `read_session_log(view="overview")` first to inspect
    the coarse executor graph. Only then call `read_session_log(view="detail", step_id="...")`
    or `node_id="..."` for one executor of interest; do not request bulk detail by default.
"""

_MATCREATOR_INSTRUCTION = "{instruction_body}"

# ---------------------------------------------------------------------------
# before_agent_callback: inject dynamic context into session state
# ---------------------------------------------------------------------------

def before_agent_callback(callback_context: CallbackContext) -> None:
    """Refresh memory and state each invocation."""
    state = callback_context._invocation_context.session.state
    for key, default in [
        ("execution_graph", None),
        ("goal", None),
        ("summarize", None),
        ("trajectory_step", 0),
    ]:
        if key not in state:
            callback_context.state[key] = default

    # Ensure workspace_dir reflects the session's actual workdir (respects custom_workdir)
    session_id = state.get("session_id", "default")
    callback_context.state["workspace_dir"] = state.get("workdir") or str(get_session_workdir(session_id))

    agent_mode = _get_agent_mode(state)
    if agent_mode == "flash":
        callback_context.state["instruction_body"] = _FLASH_INSTRUCTION.format(
            goal=state.get("goal") or "Not set",
        )
    else:
        if agent_mode == "bench":
            confirmation_instruction = (
                "3. **Benchmark mode is active.** Immediately call `confirm_plan_and_start_execution` "
                "after `validate_graph` succeeds — do NOT wait for user confirmation."
            )
        else:
            confirmation_instruction = (
                '3. **Wait for explicit user confirmation** (e.g. "yes", "ok", "proceed") before proceeding.\n'
                "   When the user confirms, call `confirm_plan_and_start_execution` — do NOT execute nodes yourself."
            )
        callback_context.state["instruction_body"] = _NORMAL_INSTRUCTION.format(
            goal=state.get("goal") or "Not set",
            execution_graph=state.get("execution_graph"),
            summarize=state.get("summarize"),
            confirmation_instruction=confirmation_instruction,
        )

    return None

# ---------------------------------------------------------------------------
# MatCreator agent instance
# ---------------------------------------------------------------------------


thinking_agent = LlmAgent(
    name="MatCreator",
    model=LiteLlm(
        model=_model_name,
        base_url=_model_base_url,
        api_key=_model_api_key,
    ),
    description=(
        "MatCreator: plans and executes computational materials science workflows "
        "through natural conversation with the user."
    ),
    instruction=_MATCREATOR_INSTRUCTION,
    tools=[
        FunctionTool(validate_graph),
        #FunctionTool(validate_plan),   # kept for backward compatibility
        FunctionTool(validate_intent),
        FunctionTool(validate_summarize),
        FunctionTool(write_session_summary),
        FunctionTool(confirm_plan_and_start_execution),
        FunctionTool(resume_execution),
        FunctionTool(search_skills),
        FunctionTool(search_skill_context),
        FunctionTool(get_related_skills),
        FunctionTool(query_knowledge_graph),
        FunctionTool(save_to_knowledge_graph),
        FunctionTool(chat_with_knowledge_graph),
        FunctionTool(run_synthesizer),
        FunctionTool(read_memory),
        FunctionTool(update_memory),
        FunctionTool(init_workspace_tool),
        FunctionTool(refresh_skills),
        FunctionTool(get_user_skills_root),
        FunctionTool(run_python),
        FunctionTool(run_bash),
        FunctionTool(run_flash_step),
        FunctionTool(read_session_log),
        FunctionTool(load_skill),
        show_artifact,
        show_plot,
        show_structure,
        #ALL_SKILLS_TOOLSET,
    ],
    before_agent_callback=before_agent_callback,
)
