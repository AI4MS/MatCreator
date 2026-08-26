"""Memory utilities for the thinking agent.

Exposes knowledge-graph-based tools (preferred) and legacy MEMORY.md helpers
(kept for backward compatibility and manual use).
"""

from __future__ import annotations

import os
from google.adk.tools import ToolContext
from ...workspace import workspace_memory_path


# ---------------------------------------------------------------------------
# Knowledge graph tools (preferred)
# ---------------------------------------------------------------------------

from ...knowledge.query import (
    query_knowledge_graph as _query_knowledge_graph,
    read_knowledge_node,
    save_to_knowledge_graph as _save_to_knowledge_graph,
    get_related_skills,
)
from ...knowledge.review import chat_with_knowledge_graph as _chat_with_knowledge_graph
from ...knowledge.synthesizer import run_knowledge_synthesizer as _run_synthesizer


def query_knowledge_graph(
    query: str,
    depth: int = 2,
    top_k: int = 5,
    include_memory: bool = True,
    skills_only: bool = False,
    include_ids: bool = True,
) -> str:
    """Discover compact knowledge and installed-skill candidates relevant to *query*.

    Returns clipped L1/L2 and working-memory candidates with stable IDs. Select
    one ID, then call ``load_skill`` for its full SKILL.md body and
    ``read_knowledge_node`` only for attached L3/L4 context. Set
    ``skills_only=True`` and ``include_memory=False`` for skill-only discovery.

    Args:
        query: Free-text search string.
        depth: Retained for compatibility; currently unused.
        top_k: Maximum L1/L2 candidates to return (default 5).
        include_memory: Include working-memory candidates (default true).
        skills_only: Limit L1/L2 candidates to installed skills (default false).
        include_ids: Include stable node IDs for follow-up reads (default true).
    """
    return _query_knowledge_graph(
        query,
        depth=depth,
        top_k=top_k,
        include_memory=include_memory,
        skills_only=skills_only,
        include_ids=include_ids,
    )


def save_to_knowledge_graph(
    content: str,
    tool_context: ToolContext,
    context: str = "",
) -> str:
    """Save a finding to the current session's writable MemGraph.

    Args:
        content: The observation, lesson, warning, or result to remember.
        context: Short task or skill context for later retrieval.
    """
    session_id = tool_context.state.get("session_id", "default")
    return _save_to_knowledge_graph(
        content,
        context=context,
        session_id=session_id,
    )


def chat_with_knowledge_graph(
    message: str,
    tool_context: ToolContext,
    read_only: bool = False,
) -> dict:
    """Send a message to Know-Do Graph's general chat agent.

    Args:
        message: Natural-language instruction or question for the graph agent.
        read_only: When true, restrict the KDG session to query-only tools.
    """
    del tool_context
    return _chat_with_knowledge_graph(message, read_only=read_only)


def run_synthesizer(
    stale_days: int = 30,
    stale_min_refs: int = 0,
    min_insights_for_workflow: int = 3,
) -> dict:
    """Distill repeated successful memory into durable Know-Do knowledge.

    Similar observations from successful executions are promoted after enough
    evidence, linked to their source capabilities, and marked as promoted in
    MemGraph. Stale failed or unchecked observations are pruned.

    Args:
        stale_days: Delete nodes older than this many days with few references.
        stale_min_refs: Nodes with <= this many references are stale candidates.
        min_insights_for_workflow: Minimum Insight nodes sharing a skill/workflow
            before a Workflow abstraction node is synthesized above them.
    """
    return _run_synthesizer(
        stale_days=stale_days,
        stale_min_refs=stale_min_refs,
        min_insights_for_workflow=min_insights_for_workflow,
    )


# ---------------------------------------------------------------------------
# Legacy MEMORY.md helpers (backward-compatible)
# ---------------------------------------------------------------------------

def load_memory() -> str:
    """Return the full contents of MEMORY.md, or an empty string if missing."""
    memory_path = workspace_memory_path()
    try:
        with open(memory_path, "r") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def write_memory(content: str) -> str:
    """Append *content* to MEMORY.md and return a confirmation message."""
    memory_path = workspace_memory_path()
    os.makedirs(os.path.dirname(memory_path), exist_ok=True)
    with open(memory_path, "a") as f:
        f.write(content)
    return f"Memory appended successfully at {memory_path}"


def update_memory(new_entries: str) -> str:
    """Append new_entries to MEMORY.md.

    Prefer save_to_knowledge_graph for new knowledge. This function is kept
    for manual/legacy use.
    """
    return write_memory("\n" + new_entries)


def read_memory() -> str:
    """Read the full contents of MEMORY.md.

    Prefer query_knowledge_graph for targeted retrieval. This function loads
    the entire file and should be used only when a broad context dump is needed.
    """
    content = load_memory()
    if not content.strip():
        return "Memory is empty. No past context available."
    return content
