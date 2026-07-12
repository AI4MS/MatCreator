"""Root entry point for MatCreator.

Wires the PlanningExecutionOrchestrator into an ADK App with event compaction
and resumability.

Routing (per user invocation):
  1. Planning   — thinking_agent understands the goal, builds a plan, asks confirmation.
  2. Execution  — execution_agent runs each plan step (one step per sub-invocation).
"""

import os
import logging

from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps import ResumabilityConfig
from google.adk.models.lite_llm import LiteLlm

from .agents.thinking_agent import thinking_agent
from .agents.execution_agent import execution_agent
from .agents.orchestrator.agent import PlanningExecutionOrchestrator
from .constants import LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
from .env_schema import COMPACTION_INTERVAL, COMPACTION_OVERLAP_SIZE

model_name = os.environ.get("LLM_MODEL", LLM_MODEL)
model_api_key = os.environ.get("LLM_API_KEY", LLM_API_KEY)
model_base_url = os.environ.get("LLM_BASE_URL", LLM_BASE_URL)

logger = logging.getLogger(__name__)


compaction_summarizer = LlmEventSummarizer(
    llm=LiteLlm(
        model=model_name,
        base_url=model_base_url,
        api_key=model_api_key,
    ),
)

orchestrator = PlanningExecutionOrchestrator(
    name="MatCreator",
    planning_agent=thinking_agent,
    execution_agent=execution_agent,
)

app = App(
    name="MatCreator",
    root_agent=orchestrator,
    resumability_config=ResumabilityConfig(
        is_resumable=True,
    ),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=COMPACTION_INTERVAL,
        overlap_size=COMPACTION_OVERLAP_SIZE,
        summarizer=compaction_summarizer,
    ),
)
