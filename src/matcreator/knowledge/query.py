"""Retrieval and write tools for unified Know-Do Graph storage."""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from know_do_graph import (
    Entry,
    EntryType,
    KnowDoGraph,
)

from .kdg_memory import (
    add_memory,
    increment_usage,
    is_entry_disabled,
)
from .review import (
    normalize_review_model,
    review_policy,
    review_threshold,
)

logger = logging.getLogger(__name__)
_graph: Optional[KnowDoGraph] = None
_migration_result: dict[str, int] | None = None


def _is_virtual(entry: Entry) -> bool:
    """Return whether an entry is a non-executable topology placeholder."""
    return bool(entry.metadata.custom.get("virtual"))


def _configure_auto_review(graph: KnowDoGraph) -> None:
    """Attach KDG's policy-controlled durable-node review scheduler."""
    enabled = os.environ.get("MATCREATOR_AUTO_REVIEW", "1").strip().lower()
    if enabled in {"0", "false", "no", "off"}:
        return

    from ..constants import GRAPH_AGENT_MODEL, LLM_API_KEY, LLM_BASE_URL

    threshold = review_threshold()
    model = normalize_review_model(
        os.environ.get("REVIEW_AGENT_MODEL")
        or os.environ.get("GRAPH_AGENT_MODEL", GRAPH_AGENT_MODEL)
    )
    api_key = (
        os.environ.get("LLM_API_KEY")
        or LLM_API_KEY
        or ""
    )
    base_url = (
        os.environ.get("LLM_BASE_URL")
        or LLM_BASE_URL
        or None
    )
    batch_size = int(os.environ.get("MATCREATOR_REVIEW_BATCH_SIZE", "5"))
    graph.auto_review(
        threshold=threshold,
        policy=review_policy(),
        strategy=os.environ.get("MATCREATOR_REVIEW_STRATEGY", "auto"),
        include_existing=True,
        model=model,
        api_key=api_key,
        base_url=base_url,
        batch_size=batch_size,
    )


def _get_kg() -> KnowDoGraph:
    global _graph, _migration_result
    if _graph is None:
        from ..constants import KNOW_DO_GRAPH_DB, KNOW_DO_MEMORY_DIR

        _graph = KnowDoGraph(path=KNOW_DO_GRAPH_DB, memory_dir=KNOW_DO_MEMORY_DIR)
        _migration_result = {"know_do_nodes": 0, "memory_entries": 0, "edges": 0}
        _configure_auto_review(_graph)
    return _graph

def get_migration_result() -> dict[str, int]:
    _get_kg()
    return dict(_migration_result or {})


# Compatibility aliases for callers from the previous split implementation.
_get_skill_kg = _get_kg
_get_memory_kg = _get_kg


# Discovery is deliberately cheap: agents load selected SKILL.md bodies through
# ``load_skill`` and use ``read_knowledge_node`` only for L3/L4 sidecars.
_DISCOVERY_CONTENT_CHARS = 240
_DISCOVERY_TOP_K = 8
_MEMORY_DISCOVERY_TOP_K = 3
_RELATED_SKILL_DESCRIPTION_CHARS = 360


def _clip_content(content: str, limit: int) -> str:
    """Return a single-line preview bounded to ``limit`` characters."""
    preview = " ".join(content.split())
    if len(preview) <= limit:
        return preview
    if limit <= 3:
        return "." * max(limit, 0)
    return preview[: max(limit - 3, 0)].rstrip() + "..."


def _short_description(entry: Entry) -> str:
    """Return a compact, human-readable description for a related skill.

    Managed skill nodes preserve their frontmatter description in ``custom``.
    Older or externally-created nodes fall back to a clipped content preview so
    relationship discovery remains useful without loading the full body.
    """
    description = " ".join(
        str(entry.metadata.custom.get("description") or entry.content).split()
    )
    # Keep discovery to at most two sentences even if a skill author supplied
    # a longer frontmatter description. A character ceiling also protects
    # against unusually long unpunctuated text.
    sentences = re.split(r"(?<=[.!?])(?=\s)", description, maxsplit=2)
    return _clip_content(" ".join(sentences[:2]), _RELATED_SKILL_DESCRIPTION_CHARS)


def _format_durable(entries: list[Entry], *, max_content_chars: int | None = None) -> str:
    lines = []
    for entry in entries:
        level = entry.metadata.skill_level
        if level is None:
            level = {
                EntryType.capability: "L1",
                EntryType.workflow: "L1",
                EntryType.procedure: "L2",
                EntryType.heuristic: "L3",
                EntryType.constraint: "L4",
            }.get(entry.entry_type)
        elif hasattr(level, "value"):
            level = level.value
        label = f"{level} {entry.entry_type.value}" if level else entry.entry_type.value
        line = f"- **{entry.title}** [{label}]"
        if entry.content:
            content = (
                _clip_content(entry.content, max_content_chars)
                if max_content_chars is not None
                else entry.content
            )
            line += f": {content}"
        lines.append(line)
    return "\n".join(lines)


def _format_memory(entries: list[Entry]) -> str:
    lines = []
    for entry in entries:
        memory = entry.metadata.custom.get("memory", {})
        success = memory.get("success")
        status = "success" if success is True else "failed" if success is False else "unchecked"
        lines.append(f"- [{memory.get('session_id', 'default')}; {status}] {entry.content}")
    return "\n".join(lines)


def _format_discovery(entries: list[Entry], *, include_ids: bool = True) -> str:
    """Format L1/L2 candidates without eagerly placing their bodies in context."""
    lines = []
    graph = _get_kg()
    for entry in entries:
        level = entry.metadata.skill_level
        if hasattr(level, "value"):
            level = level.value
        if level is None:
            level = {
                EntryType.capability: "L1",
                EntryType.workflow: "L1",
                EntryType.procedure: "L2",
            }.get(entry.entry_type)
        label = f"{level} {entry.entry_type.value}" if level else entry.entry_type.value
        preview = _clip_content(entry.content, _DISCOVERY_CONTENT_CHARS)
        id_suffix = f"; id=`{entry.id}`" if include_ids else ""
        line = f"- **{entry.title}** [{label}{id_suffix}]"
        if preview:
            line += f": {preview}"

        attached = graph.count_attached(entry.id)
        hints = []
        if attached.get("heuristics", 0):
            hints.append(f"{attached['heuristics']} L3 heuristic(s)")
        if attached.get("constraints", 0):
            hints.append(f"{attached['constraints']} L4 constraint(s)")
        if hints:
            line += " — attached context: " + ", ".join(hints)
        lines.append(line)
    return "\n".join(lines)


def _format_memory_discovery(entries: list[Entry], *, include_ids: bool = True) -> str:
    """Format memory hits as compact, selectable search results."""
    lines = []
    for entry in entries:
        memory = entry.metadata.custom.get("memory", {})
        success = memory.get("success")
        status = "success" if success is True else "failed" if success is False else "unchecked"
        preview = _clip_content(entry.content, _DISCOVERY_CONTENT_CHARS)
        id_suffix = f"; id=`{entry.id}`" if include_ids else ""
        lines.append(f"- [{memory.get('session_id', 'default')}; {status}{id_suffix}] {preview}")
    return "\n".join(lines)


def _format_related_skills(entries: list[Entry]) -> str:
    """Format related skills as selectable summaries, never full bodies."""
    lines = []
    for entry in entries:
        level = entry.metadata.skill_level
        if hasattr(level, "value"):
            level = level.value
        if level is None:
            level = {
                EntryType.capability: "L1",
                EntryType.workflow: "L1",
                EntryType.procedure: "L2",
            }.get(entry.entry_type)
        label = f"{level} {entry.entry_type.value}" if level else entry.entry_type.value
        line = f"- **{entry.title}** [{label}; id=`{entry.id}`]"
        description = _short_description(entry)
        if description:
            line += f": {description}"
        lines.append(line)
    return "\n".join(lines)


def get_node_context_summary(node_id: str) -> dict[str, object] | None:
    """Return lightweight L3/L4 attachment counts for one skill node.

    This is intentionally metadata-only so callers can decide whether reading
    sidecars is worthwhile without loading any L1/L2 or L3/L4 content.
    """
    graph = _get_kg()
    try:
        node = graph.get(node_id)
        if node is None:
            matches = graph.search(node_id, tags=["matcreator-skill"], limit=5)
            node = next((entry for entry in matches if entry.title == node_id), None)
        if node is None or is_entry_disabled(node) or _is_virtual(node):
            return None
        attached = graph.count_attached(node.id)
        return {
            "node_id": node.id,
            "heuristics": attached.get("heuristics", 0),
            "limitations": attached.get("constraints", 0),
        }
    except Exception as exc:
        logger.warning("get_node_context_summary failed: %s", exc)
        return None


def format_node_context_hint(summary: dict[str, object]) -> str | None:
    """Describe non-empty L3/L4 sidecars and the exact follow-up call."""
    heuristic_count = summary["heuristics"]
    limitation_count = summary["limitations"]
    if not heuristic_count and not limitation_count:
        return None
    return (
        f"This skill has {heuristic_count} attached L3 heuristic(s) and "
        f"{limitation_count} L4 limitation(s). Call "
        f"read_knowledge_node(node_id='{summary['node_id']}') to read them."
    )


def query_knowledge_graph(
    query: str,
    depth: int = 2,
    top_k: int = _DISCOVERY_TOP_K,
    include_memory: bool = True,
    skills_only: bool = False,
    include_ids: bool = True,
) -> str:
    """Discover compact, selectable knowledge candidates in one call.

    ``depth`` is retained for compatibility with older callers but is unused.
    Set ``skills_only`` to limit durable candidates to installed skills, and
    ``include_memory`` to include or omit working-memory matches. Set
    ``include_ids`` to omit stable node IDs from a display-only result. L3/L4 entries
    are intentionally not expanded here. Select a returned ID and use
    ``load_skill`` to read its SKILL.md body, then use
    ``read_knowledge_node`` only when its scoped L3/L4 sidecars are needed.
    """
    del depth
    graph = _get_kg()
    try:
        durable = [
            entry
            for entry in graph.plan(
                query,
                limit=max(top_k * (4 if skills_only else 3), 20),
                mode="hybrid",
                include_procedures=True,
            )
            if entry.entry_type != EntryType.memory
            and not _is_virtual(entry)
            and not is_entry_disabled(entry)
            and (not skills_only or "matcreator-skill" in entry.tags)
        ][:top_k]
        for entry in durable:
            increment_usage(graph, entry)

        memory_entries = []
        if include_memory:
            memory_entries = [
                entry
                for entry in graph.search(
                    query,
                    entry_type=EntryType.memory,
                    limit=min(top_k, _MEMORY_DISCOVERY_TOP_K),
                    mode="hybrid",
                )
                if not entry.metadata.custom.get("memory", {}).get("promoted", False)
                and not is_entry_disabled(entry)
            ]
        for entry in memory_entries:
            increment_usage(graph, entry)

        sections = []
        if durable:
            heading = "### Skill Candidates" if skills_only else "### L1/L2 Knowledge Candidates"
            sections.append(heading + "\n" + _format_discovery(durable, include_ids=include_ids))
        if memory_entries:
            sections.append(
                "### Working-Memory Candidates\n"
                + _format_memory_discovery(memory_entries, include_ids=include_ids)
            )
        if durable:
            sections.append(
                "Load a selected installed skill with `load_skill`; call "
                "`read_knowledge_node` only for conditionally scoped L3/L4 knowledge."
            )
        if sections:
            return "\n\n".join(sections)
        kind = "skills" if skills_only else "knowledge graph entries"
        return f"No {kind} found for '{query}'."
    except Exception as exc:
        logger.warning("query_knowledge_graph failed: %s", exc)
        return f"Knowledge graph query failed: {exc}"


def save_to_knowledge_graph(
    content: str,
    context: str = "",
    session_id: str = "default",
    success: bool | None = None,
) -> str:
    """Write an observation to MemGraph for later validation and distillation."""
    graph = _get_kg()
    tags = ["matcreator-memory"]
    if context:
        tags.append(f"context:{context[:80]}")
    try:
        memory = add_memory(
            graph,
            session_id,
            content,
            tags=tags,
            success=success,
        )
        return f"Saved working memory (id={memory.id}, session={session_id})."
    except Exception as exc:
        logger.warning("save_to_knowledge_graph failed: %s", exc)
        return f"Failed to save working memory: {exc}"


def search_skills(query: str, top_k: int = 5) -> str:
    """Compatibility wrapper for skill-only graph discovery.

    New agent integrations should use :func:`query_knowledge_graph` with
    ``skills_only=True`` and ``include_memory=False`` instead. This wrapper is
    retained for Python and CLI compatibility.
    """
    return query_knowledge_graph(
        query,
        top_k=top_k,
        include_memory=False,
        skills_only=True,
        include_ids=False,
    )


def read_knowledge_node(
    node_id: str,
    query: str = "",
    include_heuristics: bool = True,
    include_constraints: bool = True,
    top_k: int = 5,
) -> str:
    """Read only a selected node's conditionally scoped L3/L4 sidecars.

    ``node_id`` accepts the stable ID returned by ``query_knowledge_graph`` as
    well as a slug or alias. This deliberately does not return the L1/L2 body:
    use ``load_skill`` for full SKILL.md instructions. The sidecar candidate
    pool is scoped by ``heuristic_for``, ``constraint_on``, and
    ``warning_about`` edges, so unrelated L3/L4 nodes cannot leak into the
    result.
    """
    graph = _get_kg()
    try:
        start = graph.get(node_id)
        if start is None:
            matches = graph.plan(
                node_id,
                limit=1,
                mode="hybrid",
                include_procedures=True,
            )
            start = matches[0] if matches else None
        if start is None:
            return f"No L1/L2 node found matching '{node_id}'."
        if is_entry_disabled(start):
            return f"Skill '{start.title}' is disabled."
        if _is_virtual(start):
            return f"Skill '{start.title}' is a virtual node; its backing skill is not installed."

        sections: list[str] = []
        used_entries: list[Entry] = []
        attached = graph.count_attached(start.id)

        if include_heuristics and attached.get("heuristics", 0) > 0:
            heuristics, total = graph.search_attached(
                start.id,
                kind="heuristics",
                query=query,
                limit=top_k,
                mode="hybrid",
            )
            heuristics = [entry for entry in heuristics if not is_entry_disabled(entry)]
            if heuristics:
                sections.append(
                    f"### Attached L3 Heuristics ({len(heuristics)}/{total})\n"
                    + _format_durable(heuristics)
                )
                used_entries.extend(heuristics)
        if include_constraints and attached.get("constraints", 0) > 0:
            constraints, total = graph.search_attached(
                start.id,
                kind="constraints",
                query=query,
                limit=top_k,
                mode="hybrid",
            )
            constraints = [entry for entry in constraints if not is_entry_disabled(entry)]
            if constraints:
                sections.append(
                    f"### Attached L4 Constraints ({len(constraints)}/{total})\n"
                    + _format_durable(constraints)
                )
                used_entries.extend(constraints)

        for entry in used_entries:
            increment_usage(graph, entry)

        if not sections:
            return f"No attached L3/L4 context found for '{start.title}'."
        return f"### Attached context for {start.title}\n\n" + "\n\n".join(sections)
    except Exception as exc:
        logger.warning("read_knowledge_node failed: %s", exc)
        return f"Knowledge-node read failed: {exc}"


def search_skill_context(
    skill: str,
    query: str = "",
    include_heuristics: bool = True,
    include_constraints: bool = True,
    top_k: int = 5,
) -> str:
    """Compatibility alias for :func:`read_knowledge_node`.

    This alias is retained for Python callers but is no longer exposed as an
    agent tool. Prefer ``read_knowledge_node`` for new integrations.
    """
    return read_knowledge_node(
        skill,
        query=query,
        include_heuristics=include_heuristics,
        include_constraints=include_constraints,
        top_k=top_k,
    )


def get_related_skills(start_node: str, top_k: int = 5, depth: int = 2) -> str:
    """Discover compact, selectable summaries of skills related to a node.

    Use ``load_skill`` with a returned title to retrieve a selected SKILL.md
    body, and :func:`read_knowledge_node` only for attached L3/L4 context.
    """
    graph = _get_kg()
    try:
        start = graph.get(start_node)
        if start is None:
            matches = graph.search(start_node, tags=["matcreator-skill"], limit=1)
            start = matches[0] if matches else None
        if start is None:
            return f"No skill node found matching '{start_node}'."
        if is_entry_disabled(start):
            return f"Skill '{start.title}' is disabled."

        related = [
            entry
            for entry in graph.related(start.id, depth=depth)
            if "matcreator-skill" in entry.tags
            and not is_entry_disabled(entry)
        ][:top_k]
        for entry in related:
            increment_usage(graph, entry)
        if not related:
            return f"No related skills found near '{start_node}'."
        return (
            _format_related_skills(related)
            + "\n\nLoad a selected installed skill with `load_skill`; call "
            + "`read_knowledge_node` only for its L3/L4 context."
        )
    except Exception as exc:
        logger.warning("get_related_skills failed: %s", exc)
        return f"Related skills lookup failed: {exc}"
