from __future__ import annotations

from know_do_graph import (
    EdgeRelation,
    EntryMetadata,
    EntryType,
    KnowDoGraph,
    SkillLevel,
    VerificationStatus,
)

from matcreator.knowledge import query, review
from matcreator.knowledge.kdg_memory import set_entry_disabled


def _add(
    graph: KnowDoGraph,
    title: str,
    entry_type: EntryType,
    level: SkillLevel,
    *,
    content: str | None = None,
    tags: list[str] | None = None,
):
    return graph.add(
        title,
        content=content if content is not None else f"{title} content",
        entry_type=entry_type,
        tags=tags or [],
        metadata=EntryMetadata(skill_level=level),
    )


def test_unified_skill_only_discovery_returns_clipped_skill_previews(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    full_body = "\n".join(
        [
            "Short skill overview.",
            "Detailed instruction A " * 20,
            "Detailed instruction B should stay out of the discovery result.",
        ]
    )
    _add(
        graph,
        "Verbose skill",
        EntryType.capability,
        SkillLevel.L1,
        content=full_body,
        tags=["matcreator-skill"],
    )
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setattr(query, "increment_usage", lambda _graph, _entry: None)

    result = query.query_knowledge_graph(
        "verbose skill",
        top_k=1,
        include_memory=False,
        skills_only=True,
    )

    assert "Verbose skill" in result
    assert "Short skill overview." in result
    assert "Detailed instruction B should stay out" not in result
    assert len(result) < len(full_body)
    assert "..." in result


def test_unified_discovery_can_exclude_memory_and_non_skill_nodes(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    skill_entry = _add(
        graph,
        "Installed skill",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    _add(graph, "General knowledge", EntryType.capability, SkillLevel.L1)
    graph.add(
        "Installed memory",
        content="Installed working-memory result.",
        entry_type=EntryType.memory,
    )
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setattr(query, "increment_usage", lambda _graph, _entry: None)

    result = query.query_knowledge_graph(
        "installed",
        top_k=5,
        include_memory=False,
        skills_only=True,
    )

    assert "Installed skill" in result
    assert skill_entry.id in result
    assert "General knowledge" not in result
    assert "Installed memory" not in result

    display_result = query.query_knowledge_graph(
        "installed",
        top_k=5,
        include_memory=False,
        skills_only=True,
        include_ids=False,
    )
    assert "Installed skill" in display_result
    assert skill_entry.id not in display_result



def test_get_related_skills_returns_selectable_descriptions_not_full_bodies(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    start = _add(
        graph,
        "Structure generation",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    full_body = "Detailed MatterSim instruction. " * 200
    related = graph.add(
        "MatterSim",
        content=full_body,
        entry_type=EntryType.capability,
        tags=["matcreator-skill"],
        metadata=EntryMetadata(
            skill_level=SkillLevel.L1,
            custom={
                "description": (
                    "Relax structures. Run molecular dynamics. "
                    "This sentence must not appear."
                )
            },
        ),
    )
    graph.connect(start.id, related.id, relation=EdgeRelation.dependency)
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setattr(query, "increment_usage", lambda _graph, _entry: None)

    result = query.get_related_skills(start.id, top_k=1)

    assert "MatterSim" in result
    assert related.id in result
    assert "Relax structures. Run molecular dynamics." in result
    assert "This sentence must not appear." not in result
    assert "Detailed MatterSim instruction" not in result
    assert "read_knowledge_node" in result
    assert len(result) < len(full_body)


def test_graph_disabled_skills_are_hidden_from_discovery_and_reads(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    disabled = _add(
        graph,
        "atomic-structure",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    set_entry_disabled(graph, disabled.id, True)

    discovery = query.query_knowledge_graph("atomic structure", top_k=1)

    assert "atomic-structure" not in discovery
    assert discovery.startswith("No knowledge graph entries found")
    assert query.read_knowledge_node(disabled.id) == "Skill 'atomic-structure' is disabled."


def test_query_knowledge_graph_is_compact_discovery_with_native_sidecar_hints(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    full_body = "Overview. " + "Detailed planning instruction. " * 40
    selected = _add(
        graph,
        "Verbose planning capability",
        EntryType.capability,
        SkillLevel.L1,
        content=full_body,
    )
    heuristic = _add(
        graph,
        "Selected heuristic",
        EntryType.heuristic,
        SkillLevel.L3,
    )
    graph.connect(heuristic.id, selected.id, relation=EdgeRelation.heuristic_for)
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setattr(query, "increment_usage", lambda _graph, _entry: None)

    result = query.query_knowledge_graph("verbose planning", top_k=1)

    assert selected.id in result
    assert "1 L3 heuristic(s)" in result
    assert "Load a selected installed skill with `load_skill`" in result
    assert len(result) < len(full_body)
    assert "..." in result

    expanded = query.read_knowledge_node(selected.id)
    assert full_body not in expanded
    assert "Selected heuristic" in expanded


def test_read_knowledge_node_without_sidecars_does_not_repeat_the_node_body(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    selected = _add(
        graph,
        "Standalone capability",
        EntryType.capability,
        SkillLevel.L1,
        content="Full SKILL.md instructions that must be loaded separately.",
    )
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = query.read_knowledge_node(selected.id)

    assert result == "No attached L3/L4 context found for 'Standalone capability'."
    assert "Full SKILL.md instructions" not in result


def test_node_context_summary_reports_l3_l4_counts_without_loading_content(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    selected = _add(
        graph,
        "Selected capability",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    heuristic = _add(graph, "Attached heuristic", EntryType.heuristic, SkillLevel.L3)
    limitation = _add(graph, "Attached limitation", EntryType.constraint, SkillLevel.L4)
    graph.connect(heuristic.id, selected.id, relation=EdgeRelation.heuristic_for)
    graph.connect(limitation.id, selected.id, relation=EdgeRelation.constraint_on)
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    summary = query.get_node_context_summary(selected.id)

    assert summary == {
        "node_id": selected.id,
        "heuristics": 1,
        "limitations": 1,
    }
    assert query.format_node_context_hint(summary) == (
        "This skill has 1 attached L3 heuristic(s) and 1 L4 limitation(s). "
        f"Call read_knowledge_node(node_id='{selected.id}') to read them."
    )


def test_search_skill_context_compatibility_alias_only_returns_attached_sidecars(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "know-do.db")
    selected = _add(
        graph,
        "Selected capability",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    other = _add(
        graph,
        "Other capability",
        EntryType.capability,
        SkillLevel.L1,
        tags=["matcreator-skill"],
    )
    attached_heuristic = _add(
        graph,
        "Attached heuristic",
        EntryType.heuristic,
        SkillLevel.L3,
    )
    attached_constraint = _add(
        graph,
        "Attached constraint",
        EntryType.constraint,
        SkillLevel.L4,
    )
    unrelated = _add(
        graph,
        "Unrelated heuristic",
        EntryType.heuristic,
        SkillLevel.L3,
    )
    graph.connect(
        attached_heuristic.id,
        selected.id,
        relation=EdgeRelation.heuristic_for,
    )
    graph.connect(
        attached_constraint.id,
        selected.id,
        relation=EdgeRelation.constraint_on,
    )
    graph.connect(unrelated.id, other.id, relation=EdgeRelation.heuristic_for)
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = query.search_skill_context(selected.id)

    assert "Attached heuristic" in result
    assert "Attached constraint" in result
    assert "Unrelated heuristic" not in result


class _ReviewerSession:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def review_nodes(self, instructions: str = "") -> dict:
        self.calls.append(("review_graph", instructions))
        return {"status": "completed", "summary": "graph reviewed"}

    def review_memory(
        self,
        *,
        session_id: str | None = None,
        instructions: str = "",
    ) -> dict:
        self.calls.append(("review_memory", session_id, instructions))
        return {"status": "completed", "summary": "memory reviewed"}


class _RecordingGraph:
    def __init__(self) -> None:
        self.options: dict | None = None
        self.session = _ReviewerSession()
        self.refreshed = False

    def chat(self, **options):
        self.options = options
        return self.session

    def refresh(self):
        self.refreshed = True
        return {}


def test_graph_agent_tool_routes_through_protected_reviewer_policy(
    monkeypatch,
) -> None:
    graph = _RecordingGraph()
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    result = review.talk_to_knowledge_graph_agent(
        "review_graph",
        instructions="Focus on duplicate unverified nodes.",
        batch_size=3,
    )

    assert result["status"] == "completed"
    assert graph.options is not None
    assert graph.options["agent"] == "reviewer"
    assert graph.options["batch_size"] == 3
    assert graph.options["policy"].protected_statuses == frozenset(
        {
            VerificationStatus.peer_reviewed,
            VerificationStatus.community_tested,
        }
    )
    assert graph.session.calls == [
        ("review_graph", "Focus on duplicate unverified nodes.")
    ]
    assert graph.refreshed is True


def test_graph_agent_tool_scopes_memory_review_to_current_session(
    monkeypatch,
) -> None:
    graph = _RecordingGraph()
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    result = review.talk_to_knowledge_graph_agent(
        "review_memory",
        instructions="Distill only reusable lessons.",
        session_id="session-123",
    )

    assert result["status"] == "completed"
    assert graph.session.calls == [
        ("review_memory", "session-123", "Distill only reusable lessons.")
    ]
