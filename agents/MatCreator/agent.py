"""Root entry point for MatCreator.

Wires the single MatCreator LlmAgent into an ADK App with event compaction
and resumability. The complex phase-routing state machine is gone — the agent
handles planning and execution in a single conversational loop.
"""

import os
import logging

from google.adk.agents.callback_context import CallbackContext
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.apps import ResumabilityConfig
from google.adk.models.lite_llm import LiteLlm

from .thinking_agent import thinking_agent
from .constants import LLM_MODEL, LLM_API_KEY, LLM_BASE_URL

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

app = App(
    name="MatCreator",
    root_agent=thinking_agent,
    resumability_config=ResumabilityConfig(
        is_resumable=True,
    ),
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=3,
        overlap_size=1,
        summarizer=compaction_summarizer,
    ),
)