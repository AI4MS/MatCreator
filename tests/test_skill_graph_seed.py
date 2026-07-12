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
