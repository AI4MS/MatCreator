"""Plan-builder sub-agent for the ThinkingAgent.

This agent is the structural equivalent of `database_agent/sql_agent`:
it accepts a structured planning request via `PlanBuilderInput` and returns
a fully-formed `ExecutionPlan` JSON without any custom orchestration logic.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, List

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event
from google.adk.models.lite_llm import LiteLlm
from pydantic import BaseModel, Field, ValidationError, field_validator

from ...constants import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
from ..skill import _load_skill_registry

_model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
_model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
_model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)
_plan_builder_max_attempts = int(os.environ.get("PLAN_BUILDER_MAX_ATTEMPTS", "10"))

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


class ExecutionPlan(BaseModel):
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
You are a plan-builder sub-agent. You produce a
detailed, actionable execution plan.

Input:
- goal: {goal}
- guides: {guides}
- skills: {skills}
- memory: {memory}
- current plan {plan}

Requirements:
- Use ONLY skill values that appear in skills.
- Keep each step specific and concise.
- List steps ONLY for the current stage.
- Every step must include: step_number, skill, action.

Strictly Follow the JSON output.
"""

_RETRY_INSTRUCTION_TEMPLATE = """

Previous output failed schema validation:
{validation_error}

Retry now and output ONLY a valid JSON object that conforms to the ExecutionPlan schema.
Do not include markdown fences or explanatory text.
"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class PlanBuilderAgent(LlmAgent):
    """Plan builder with final-output schema validation and retry."""

    @staticmethod
    def _extract_event_text(event: Event) -> str:
        """Extract plain text from ADK event content parts."""
        content = getattr(event, "content", None)
        if content is None:
            return ""
        parts = getattr(content, "parts", None) or []
        chunks: List[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
        return "\n".join(chunks)

    @staticmethod
    def _extract_json_candidate(raw_text: str) -> str:
        """Extract JSON candidate from raw text, tolerating fenced responses."""
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()

        match = re.search(r"\{[\s\S]*\}", text)
        return match.group(0) if match else text

    @classmethod
    def _validate_execution_plan(cls, event: Event | None) -> tuple[dict[str, Any] | None, str | None]:
        """Validate final event payload against ExecutionPlan schema."""
        if event is None:
            return None, "No final response event was produced."

        raw_text = cls._extract_event_text(event)
        if not raw_text:
            return None, "Final response event is empty."

        candidate = cls._extract_json_candidate(raw_text)

        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            return None, f"JSON decode error: {exc}"

        try:
            validated = ExecutionPlan.model_validate(parsed)
        except ValidationError as exc:
            return None, f"Schema validation error: {exc}"

        return validated.model_dump(), None

    async def _run_async_impl(self, ctx: InvocationContext):
        """Retry plan generation until final output matches ExecutionPlan schema."""
        base_instruction = self.instruction
        last_error = ""

        try:
            for attempt in range(1, _plan_builder_max_attempts + 1):
                logger.info(f"PlanBuilderAgent attempt {attempt}/{_plan_builder_max_attempts}")
                if attempt == 1:
                    self.instruction = base_instruction
                else:
                    self.instruction = (
                        base_instruction
                        + _RETRY_INSTRUCTION_TEMPLATE.format(validation_error=last_error)
                    )

                buffered_events: List[Event] = []
                final_event: Event | None = None

                async for event in super()._run_async_impl(ctx):
                    buffered_events.append(event)
                    if event.is_final_response() and getattr(event, "content", None):
                        final_event = event

                _, validation_error = self._validate_execution_plan(final_event)
                if validation_error is None:
                    for event in buffered_events:
                        yield event
                    return

                last_error = validation_error
                logger.warning(
                    "plan_builder_agent schema validation failed on attempt %d/%d: %s",
                    attempt,
                    _plan_builder_max_attempts,
                    validation_error,
                )

            raise ValueError(
                "Plan builder failed to produce valid ExecutionPlan JSON "
                f"after {_plan_builder_max_attempts} attempts. Last error: {last_error}"
            )
        finally:
            self.instruction = base_instruction


plan_builder_agent = PlanBuilderAgent(
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
    input_schema=PlanBuilderInput,
    output_schema=ExecutionPlan,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)
