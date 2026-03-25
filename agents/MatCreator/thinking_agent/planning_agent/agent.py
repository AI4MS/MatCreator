"""Plan-builder sub-agent for the ThinkingAgent.

This agent is the structural equivalent of `database_agent/sql_agent`:
it accepts a structured planning request via `PlanBuilderInput` and returns
a fully-formed `ExecutionPlan` JSON without any custom orchestration logic.
"""

from __future__ import annotations

import logging
import os
from typing import Any, List

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.function_tool import FunctionTool
from pydantic import BaseModel, Field, field_validator

from ...constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ..skill import _load_skill_registry, load_guide_content, load_skill_content

_model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
_model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
_model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)

logger = logging.getLogger()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanStep(BaseModel):
    """Single step in the execution plan."""

    step_number: int = Field(..., description="Sequential step number (1, 2, 3, ...)")
    skill: str = Field(
        ...,
        description="Skill name used by the executor to load relevant tools and instruction.",
        min_length=1,
    )
    action: str = Field(
        ...,
        description="Clear, concise description of what this step does (1-2 sentences)",
        max_length=500,
    )

    @field_validator("skill")
    @classmethod
    def _validate_skill_name(cls, value: str) -> str:
        allowed_names = set(_load_skill_registry().keys())
        if value not in allowed_names:
            allowed = ", ".join(sorted(allowed_names)) or "<none loaded>"
            raise ValueError(
                f"Invalid skill '{value}'. Allowed skills are: {allowed}"
            )
        return value


class _ExecutionPlan(BaseModel):
    """Structured execution plan for user approval."""
    stages: List[str] = Field(
        ..., description="Stages of execution, be general. Example: ['Evaluate the pre-trained model','Proceed to fine-tuning only if neccesary']"
    )
    current_stage: int = Field(..., description="The current stage of execution")
    steps: List[PlanStep] = Field(
        ...,
        #description="Ordered list of detailed execution steps, ONLY includes DETERMINED steps",
        description="Ordered list of detailed steps in the CURRENT stage, ONLY includes DETERMINED steps",
        min_items=1,
        max_items=10,
    )
    additional_notes: str = Field(
        ...,
        description="Any extra information or considerations for the user",
        max_length=500,
    )

class ExecutionPlan(BaseModel):
    """Structured execution plan for user approval."""
    steps: List[PlanStep] = Field(
        ...,
        description="Ordered list of detailed steps in the CURRENT stage, ONLY includes DETERMINED steps",
        min_items=1,
        max_items=10,
    )
    additional_notes: str = Field(
        ...,
        description="Any extra information or considerations for the user",
        max_length=500,
    )




class PlanBuilderInput(BaseModel):
    """Structured request passed from the ThinkingAgent to the plan-builder."""

    goal: str = Field(
        ...,
        description="Immediate goal in one sentence.",
    )
    comments: str = Field(
        ...,
        description="Additional comments or context for the plan-builder.",
    )


# ---------------------------------------------------------------------------
# Instruction
# ---------------------------------------------------------------------------

_PLAN_BUILDER_INSTRUCTION = """
You are a plan-builder. Produce an execution plan for the given goal.

Goal: {goal}
Available skills: {skills}
Guide summaries: {guides}
Memory: {memory}
Current plan (if updating): {plan}

Output ONLY a JSON object — no markdown fences, no extra text:
{{
  "steps": [
    {{"step_number": 1, "skill": "<skill_name>", "action": "<1-2 sentence description>"}}
  ],
  "additional_notes": "<any extra information for the user>"
}}

Rules:
- skill must be one of the names listed in Available skills.
- Include 1-10 steps.
- Use the `load_guide_content` tool to fetch the full body of any relevant guide(s) if needed to inform the plan.
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

def _plan_builder_before_tool(
    tool: Any, args: dict, tool_context: Any
) -> None:
    logger.info(
        "[plan_builder_agent] before_tool | tool=%s | args=%s",
        getattr(tool, "name", tool),
        args,
    )
    return None


def _plan_builder_after_tool(
    tool: Any, args: dict, tool_context: Any, tool_response: Any
) -> None:
    logger.info(
        "[plan_builder_agent] after_tool  | tool=%s | response=%s",
        getattr(tool, "name", tool),
        tool_response,
    )
    return None


plan_builder_agent = LlmAgent(
    name="plan_builder_agent",
    model=LiteLlm(
        model=_model_name,
        base_url=_model_base_url,
        api_key=_model_api_key
    ),
    description=(
        "Produces a detailed ExecutionPlan JSON. ALWAYS call it when creating/updating plans."
    ),
    instruction=_PLAN_BUILDER_INSTRUCTION,
    tools=[
        FunctionTool(load_guide_content),
        FunctionTool(load_skill_content),
    ],
    before_tool_callback=_plan_builder_before_tool,
    after_tool_callback=_plan_builder_after_tool,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
