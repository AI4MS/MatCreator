"""Centralised registry of all environment variables used by MatCreator.

This module documents every environment variable that affects MatCreator's
runtime behaviour, including its default value, type, and a short
description.  Other modules can import from here instead of hardcoding
``os.environ.get(...)`` calls with magic-string defaults scattered across
files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EnvVar:
    """Metadata for a single environment variable."""

    name: str
    default: Any
    var_type: type
    description: str

    def get(self) -> Any:
        """Read and coerce the env var to its declared type."""
        raw = os.environ.get(self.name)
        if raw is None or raw == "":
            return self.default
        if self.var_type is bool:
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        try:
            return self.var_type(raw)
        except (ValueError, TypeError):
            return self.default


# ---------------------------------------------------------------------------
# LLM / model configuration
# ---------------------------------------------------------------------------

LLM_MODEL = EnvVar("LLM_MODEL", "", str, "Primary LLM model identifier.")
LLM_API_KEY = EnvVar("LLM_API_KEY", "", str, "API key for the LLM provider.")
LLM_BASE_URL = EnvVar("LLM_BASE_URL", "", str, "Base URL for the LLM API.")
EMBEDDING_MODEL = EnvVar("EMBEDDING_MODEL", "", str, "Embedding model name.")
GRAPH_AGENT_MODEL = EnvVar("GRAPH_AGENT_MODEL", "", str, "Model for graph agent (defaults to LLM_MODEL).")
REVIEW_AGENT_MODEL = EnvVar("REVIEW_AGENT_MODEL", "", str, "Model for review agent (defaults to GRAPH_AGENT_MODEL).")

# ---------------------------------------------------------------------------
# Execution runtime
# ---------------------------------------------------------------------------

SUB_STEP_TIMEOUT = EnvVar("SUB_STEP_TIMEOUT", 3600, int, "Wall-clock timeout for a single step execution (seconds).")
STEP_RECOVERY_HEARTBEAT_INTERVAL = EnvVar("STEP_RECOVERY_HEARTBEAT_INTERVAL", 10, int, "Heartbeat interval for recovery attempts (seconds).")
STEP_RECOVERY_STALE_AFTER = EnvVar("STEP_RECOVERY_STALE_AFTER", 60, int, "Stale threshold for running attempt recovery (seconds).")
EXECUTION_ENABLE_WITHIN_INVOCATION_COMPACTION = EnvVar("EXECUTION_ENABLE_WITHIN_INVOCATION_COMPACTION", 1, int, "Enable execution within invocation compaction.")
EXECUTION_COMPACT_KEEP_TAIL = EnvVar("EXECUTION_COMPACT_KEEP_TAIL", 10, int, "Number of trailing events to keep during compaction.")
EXECUTION_COMPACT_EVERY_EVENTS = EnvVar("EXECUTION_COMPACT_EVERY_EVENTS", 5, int, "Compact every N events.")

# ---------------------------------------------------------------------------
# Image / multimodal input
# ---------------------------------------------------------------------------

MAX_INPUT_IMAGE_ATTACHMENTS = EnvVar("MATCREATOR_MAX_INPUT_IMAGE_ATTACHMENTS", 4, int, "Maximum number of input image attachments per step.")
MAX_INPUT_IMAGE_BYTES = EnvVar("MATCREATOR_MAX_INPUT_IMAGE_BYTES", 5 * 1024 * 1024, int, "Maximum size per input image in bytes.")

# ---------------------------------------------------------------------------
# Knowledge graph review
# ---------------------------------------------------------------------------

AUTO_REVIEW = EnvVar("MATCREATOR_AUTO_REVIEW", "1", str, "Enable KDG auto-review ('1' = on).")
REVIEW_TRIGGER_THRESHOLD = EnvVar("MATCREATOR_REVIEW_TRIGGER_THRESHOLD", 20, int, "Minimum unreviewed nodes to trigger graph review.")
REVIEW_BATCH_SIZE = EnvVar("MATCREATOR_REVIEW_BATCH_SIZE", 5, int, "Batch size for review operations.")
REVIEW_STRATEGY = EnvVar("MATCREATOR_REVIEW_STRATEGY", "auto", str, "Review strategy ('auto', 'random', etc.).")

# ---------------------------------------------------------------------------
# Synthesizer
# ---------------------------------------------------------------------------

SYNTHESIZER_INTERVAL = 10  # Run synthesizer every N completed executions.
SYNTHESIZER_STALE_DAYS = 30
SYNTHESIZER_MIN_INSIGHTS = 3

# ---------------------------------------------------------------------------
# Step executor recursion
# ---------------------------------------------------------------------------

MAX_RECURSION_DEPTH = 3

# ---------------------------------------------------------------------------
# Cancellation polling
# ---------------------------------------------------------------------------

CANCEL_POLL_INTERVAL = 0.5  # seconds

# ---------------------------------------------------------------------------
# App-level tuning
# ---------------------------------------------------------------------------

COMPACTION_INTERVAL = 3
COMPACTION_OVERLAP_SIZE = 1

# ---------------------------------------------------------------------------
# Skill similarity thresholds
# ---------------------------------------------------------------------------

SKILL_SIMILARITY_THRESHOLD = 0.9
CLUSTER_SIMILARITY_THRESHOLD = 0.72

# ---------------------------------------------------------------------------
# Knowledge graph query limits
# ---------------------------------------------------------------------------

DISCOVERY_CONTENT_CHARS = 240
