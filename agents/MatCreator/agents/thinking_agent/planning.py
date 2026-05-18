"""Plan validation tool for the ThinkingAgent.

Provides a Pydantic-backed ``validate_plan`` function tool that the thinking
agent calls after generating a plan to validate schema conformance and
persist it to session state.
"""

from __future__ import annotations

import logging
from typing import List

from google.adk.tools.tool_context import ToolContext
from pydantic import BaseModel, Field, ValidationError, field_validator

from ...skill import ALL_SKILLS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanStep(BaseModel):
    """Single step in the execution plan."""

    step_number: int = Field(..., description="Sequential step number (1, 2, 3, ...)")
    suggested_skills: List[str] = Field(
        ...,
        description="Ordered list of skill names likely needed for this step. The executor may load additional skills as needed.",
        min_items=1,
    )
    action: str = Field(
        ...,
        description="Clear, concise description of what this step does (1-2 sentences). Each step should cover a meaningful chunk of work — avoid splitting a single logical operation into multiple steps.",
        max_length=500,
    )

    @field_validator("suggested_skills")
    @classmethod
    def _validate_skill_names(cls, values: List[str]) -> List[str]:
        allowed_names = {s.name for s in ALL_SKILLS}
        invalid = [v for v in values if v not in allowed_names]
        if invalid:
            allowed = ", ".join(sorted(allowed_names)) or "<none loaded>"
            raise ValueError(
                f"Invalid skill(s) {invalid}. Allowed skills are: {allowed}"
            )
        return values


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




# ---------------------------------------------------------------------------
# validate_plan tool
# ---------------------------------------------------------------------------

def validate_plan(plan: dict, tool_context: ToolContext) -> dict:
    """Validate and commit a plan to session state.

    Call this after drafting a plan to validate it against the schema
    and persist it. On success the plan is stored under the 'plan' session
    state key and returned. On failure the validation errors are returned so
    you can fix and retry.

    Args:
        plan: Dict with 'steps' (list of {step_number, suggested_skills, action}) and
              'additional_notes' (str).
    """
    try:
        validated = ExecutionPlan(**plan)
        tool_context.state["plan"] = validated.model_dump()
        tool_context.state["plan_exec_id"] = None  # force new ID on next execution
        return {
            "status": "ok",
            "plan": validated.model_dump(),
            "message": f"Plan validated and saved with {len(validated.steps)} steps.",
        }
    except ValidationError as exc:
        return {
            "status": "error",
            "errors": exc.errors(),
            "message": "Plan validation failed. Fix the errors and re-call validate_plan.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Unexpected error: {exc}",
        }
