from __future__ import annotations

from pathlib import Path

from google.adk.skills import load_skill_from_dir
from know_do_graph import KnowDoGraph

from matcreator import guide, skill
from matcreator.knowledge import query


def _write_skill(skill_dir: Path) -> None:
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo-skill
description: Short summary only.
tools: []
dependent_skills: []
---
Use the detailed SKILL instructions here.

- Step 1: prepare inputs
- Step 2: validate outputs
""",
        encoding="utf-8",
    )
    (skill_dir / "references" / "tips.md").write_text(
        "Reference guidance for tricky edge cases.",
        encoding="utf-8",
    )
    (skill_dir / "assets" / "example.md").write_text(
        "Worked example content for the skill.",
        encoding="utf-8",
    )
    (skill_dir / "scripts" / "tool.py").write_text(
        "print('skill script')\n",
        encoding="utf-8",
    )
    (skill_dir / "README.md").write_text(
        "Extra README context for operators.",
        encoding="utf-8",
    )


def _write_simple_skill(
    skill_dir: Path,
    *,
    name: str,
    entry_type: str = "capability",
    skill_level: str = "L1",
    dependent_skills: list[str] | None = None,
) -> None:
    skill_dir.mkdir(parents=True)
    deps = dependent_skills or []
    deps_yaml = "\n".join(f"    - {dep}" for dep in deps) or "    []"
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {name} description.
metadata:
  entry_type: {entry_type}
  skill_level: {skill_level}
  dependent_skills:
{deps_yaml}
---
Content for {name}.
""",
        encoding="utf-8",
    )


def test_seed_skills_to_graph_stores_full_skill_body_and_native_attachments(
    tmp_path, monkeypatch
) -> None:
    skills_root = tmp_path / "skills"
    skill_dir = skills_root / "demo-skill"
    _write_skill(skill_dir)

    loaded_skill = load_skill_from_dir(skill_dir)
    graph = KnowDoGraph(tmp_path / "know-do.db")

    monkeypatch.setattr(skill, "ALL_SKILLS", [loaded_skill])
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", tmp_path / "empty-defaults")
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: skills_root)
    monkeypatch.setattr(guide, "ALL_GUIDES", [])
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = skill.seed_skills_to_graph()

    assert result["seeded"] == 1
    assert result["attachments_seeded"] == 6

    matches = graph.search("demo-skill", tags=["matcreator-skill"], limit=5, mode="keyword")
    skill_entry = next(entry for entry in matches if entry.title == "demo-skill")
    assert "Use the detailed SKILL instructions here." in skill_entry.content
    assert "Short summary only." not in skill_entry.content

    assert skill_entry.internal_refs == ["references/tips.md", "README.md"]
    assert {(asset.folder, asset.filename) for asset in skill_entry.assets} == {
        ("references", "tips.md"),
        ("assets", "example.md"),
        ("scripts", "tool.py"),
        ("docs", "README.md"),
    }
    assert [script.filename for script in skill_entry.scripts] == ["tool.py"]

    context = query.search_skill_context(skill_entry.id, query="README", top_k=10)
    assert "docs/README.md" in context
    assert "Extra README context for operators." in context
    assert "scripts/tool.py" not in context


def test_seed_skills_to_graph_attaches_l3_l4_nodes_for_progressive_retrieval(
    tmp_path, monkeypatch
) -> None:
    skills_root = tmp_path / "skills"
    _write_simple_skill(skills_root / "base-skill", name="base-skill")
    _write_simple_skill(
        skills_root / "useful-heuristic",
        name="useful-heuristic",
        entry_type="heuristic",
        skill_level="L3",
        dependent_skills=["base-skill"],
    )
    _write_simple_skill(
        skills_root / "known-limitation",
        name="known-limitation",
        entry_type="constraint",
        skill_level="L4",
        dependent_skills=["base-skill"],
    )

    loaded_skills = [load_skill_from_dir(path) for path in sorted(skills_root.iterdir())]
    graph = KnowDoGraph(tmp_path / "know-do.db")

    monkeypatch.setattr(skill, "ALL_SKILLS", loaded_skills)
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", tmp_path / "empty-defaults")
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: skills_root)
    monkeypatch.setattr(guide, "ALL_GUIDES", [])
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    skill.seed_skills_to_graph()
    base = next(entry for entry in graph.search("base-skill", tags=["matcreator-skill"], limit=5, mode="keyword") if entry.title == "base-skill")

    attached = graph.count_attached(base.id)
    assert attached["heuristics"] == 1
    assert attached["constraints"] == 1

    context = query.search_skill_context(base.id)
    assert "useful-heuristic" in context
    assert "known-limitation" in context


def test_seed_resolves_path_style_dependency_to_loaded_skill_name(
    tmp_path, monkeypatch
) -> None:
    skills_root = tmp_path / "skills"
    _write_simple_skill(skills_root / "base-skill", name="base-skill")
    _write_simple_skill(
        skills_root / "dependent-skill",
        name="dependent-skill",
        dependent_skills=["concepts/base-skill"],
    )
    loaded_skills = [load_skill_from_dir(path) for path in sorted(skills_root.iterdir())]
    graph = KnowDoGraph(tmp_path / "know-do.db")

    monkeypatch.setattr(skill, "ALL_SKILLS", loaded_skills)
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", tmp_path / "empty-defaults")
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: skills_root)
    monkeypatch.setattr(guide, "ALL_GUIDES", [])
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = skill.seed_skills_to_graph()

    dependent = next(entry for entry in graph.list(limit=20) if entry.title == "dependent-skill")
    related = graph.related(dependent.id, relation="dependency")
    assert [entry.title for entry in related] == ["base-skill"]
    assert not any(entry.title == "concepts/base-skill" for entry in graph.list(limit=20))
    assert result["virtualized"] == 0


def test_seed_removes_stale_managed_skill_and_uses_virtual_dependency(
    tmp_path, monkeypatch
) -> None:
    skills_root = tmp_path / "skills"
    graph = KnowDoGraph(tmp_path / "know-do.db")
    stale = graph.add(
        "removed-skill",
        content="Stale instructions must not remain available.",
        entry_type="capability",
        tags=["matcreator-skill", "managed"],
        internal_refs=["references/stale.md"],
    )
    dependent_dir = skills_root / "dependent-skill"
    _write_simple_skill(
        dependent_dir,
        name="dependent-skill",
        dependent_skills=["removed-skill"],
    )
    dependent = load_skill_from_dir(dependent_dir)

    monkeypatch.setattr(skill, "ALL_SKILLS", [dependent])
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", tmp_path / "empty-defaults")
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: skills_root)
    monkeypatch.setattr(guide, "ALL_GUIDES", [])
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = skill.seed_skills_to_graph()

    assert graph.get(stale.id) is None
    virtual = next(
        entry
        for entry in graph.list(limit=20)
        if entry.title == "removed-skill"
    )
    virtual_id = virtual.id
    assert result["removed"] == 1
    assert result["virtualized"] == 1
    assert virtual.content == ""
    assert virtual.internal_refs == []
    assert virtual.metadata.custom["virtual"] is True
    assert "virtual" in virtual.tags
    assert query.search_skills("removed-skill").startswith("No skills found")
    assert "virtual node" in query.search_skill_context(stale.id)

    skill_dir = skills_root / "removed-skill"
    _write_simple_skill(skill_dir, name="removed-skill")
    monkeypatch.setattr(
        skill,
        "ALL_SKILLS",
        [dependent, load_skill_from_dir(skill_dir)],
    )

    skill.seed_skills_to_graph()

    restored = graph.get(virtual_id)
    assert restored is not None
    assert restored.content == "Content for removed-skill."
    assert restored.metadata.custom["virtual"] is False
    assert "virtual" not in restored.tags
    assert any(tag.startswith("skill-source:") for tag in restored.tags)
