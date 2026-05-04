from __future__ import annotations

import logging
import os

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from ...constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ..thinking_agent.agent import load_skill_context, clear_current_skill
from ..thinking_agent.summarize import validate_summarize
from ...tools.workspace_tools import (
    read_workspace_file,
    run_bash,
    run_python,
    run_skill_script,
    write_workspace_file,
)
from ...tools.util_tools import show_plot, show_structure, show_artifact

logger = logging.getLogger(__name__)

_model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
_model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
_model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)

# ---------------------------------------------------------------------------
# Instruction
# ---------------------------------------------------------------------------

_EXECUTION_AGENT_INSTRUCTION = """
You are the MatCreator Executor. Your role is to execute a SINGLE plan step precisely.

## Context
- Goal: {goal}
- Plan: {plan}
- Current step: {current_step}
- Active skill: {active_skill}
- Skill instruction: {skill_instruction}

## Execution protocol
1. Read `current_step` carefully — note its `skill` and `action` fields.
2. Call `load_skill_context(skill_name)` using the skill from the current step.
3. Follow the injected `skill_instruction` to execute the step action using the available tools.
4. When the step is complete, call `validate_summarize` with key_results, artifacts, and concise_summary.
5. Call `clear_current_skill` to release the skill context.

## Rules
- Execute ONLY the current step — do not attempt subsequent steps.
- If the required skill is not found, report the error and call `to_planner` with the reason.
- If execution fails unrecoverably, call `to_planner` with a clear reason before stopping.
- Always include absolute file paths in your summary.
- NEVER run code without user approval (already given when execution phase started).
"""

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


def to_planner(reason: str, tool_context: ToolContext) -> dict:
    """Signal the orchestrator to abort the current execution loop and return
    to the planning phase.

    Call this when the current step cannot proceed and replanning or user input
    is needed — e.g. a required skill is missing, a prerequisite step failed,
    the original plan is no longer valid, or a key parameter must be confirmed
    / provided by the user before execution can continue.

    Args:
        reason: A short human-readable explanation of why replanning is needed.

    Returns:
        A confirmation dict so the LLM knows the signal was recorded.
    """
    tool_context.state["return_to_planner"] = True
    tool_context.state["return_to_planner_reason"] = reason
    logger.info("[execution_agent] to_planner called — reason: %s", reason)
    return {"status": "ok", "message": f"Returning to planner: {reason}"}


def _exec_before_agent_callback(callback_context: CallbackContext) -> None:
    """Initialise execution-phase state keys if absent."""
    for key, default in [
        ("goal", None),
        ("plan", None),
        ("current_step", {}),
        ("active_skill", None),
        ("skill_instruction", None),
        ("summarize", None),
        ("trajectory_step", 0),
        ("return_to_planner", False),
        ("return_to_planner_reason", None),
    ]:
        if key not in callback_context.state:
            callback_context.state[key] = default


# ---------------------------------------------------------------------------
# Agent instance
# ---------------------------------------------------------------------------
from ...tools.remoteagent_tool import load_remote_a2a_agents


execution_agent = LlmAgent(
    name="execution_agent",
    model=LiteLlm(
        model=_model_name,
        base_url=_model_base_url,
        api_key=_model_api_key,
    ),
    include_contents="none",
    description=(
        "Executes a single plan step by loading the relevant skill context and running the "
        "appropriate domain tools. Called by the orchestrator once per step in sequence."
    ),
    instruction=_EXECUTION_AGENT_INSTRUCTION,
    tools=[
        FunctionTool(load_skill_context),
        FunctionTool(clear_current_skill),
        FunctionTool(to_planner),
        FunctionTool(validate_summarize),
        FunctionTool(run_python),
        FunctionTool(run_bash),
        FunctionTool(run_skill_script),
        FunctionTool(show_plot),
        FunctionTool(show_structure),
        FunctionTool(show_artifact),
        #*TOOLSETS,
    ],
    before_agent_callback=_exec_before_agent_callback,
    sub_agents=load_remote_a2a_agents()
)
