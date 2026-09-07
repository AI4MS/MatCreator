from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.adk.skills import load_skill_from_dir

from matcreator import skill
from matcreator.tools import skill_tools


def _write_bundled_skill(skill_dir, name: str = "demo-skill"):
    (skill_dir / "references").mkdir(parents=True)
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: Demo skill for tests.
---
Follow these test instructions.
""",
        encoding="utf-8",
    )
    (skill_dir / "references" / "tips.md").write_text("Reference tips.", encoding="utf-8")
    (skill_dir / "assets" / "example.md").write_text("Example asset.", encoding="utf-8")
    (skill_dir / "scripts" / "tool.py").write_text("print('tool')\n", encoding="utf-8")
    return load_skill_from_dir(skill_dir)


def _isolate_skill_roots(monkeypatch, tmp_path, user_root):
    monkeypatch.setattr(skill, "_USER_SKILLS_ROOT", user_root)
    monkeypatch.setattr(skill, "_OFFICIAL_SKILLS_ROOT", user_root / "official")
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", tmp_path / "empty-defaults")
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: tmp_path / "empty-workspace")
    monkeypatch.setattr(skill, "get_disabled_skill_names", lambda: set())


def test_create_user_skill_writes_adk_skill_under_user_root(tmp_path, monkeypatch):
    user_root = tmp_path / "user-skills"
    monkeypatch.setattr(skill, "_USER_SKILLS_ROOT", user_root)
    monkeypatch.setattr(skill, "refresh_skills", lambda: {"status": "ok", "count": 1})
    monkeypatch.setattr(skill, "get_default_skill_names", lambda: set())

    result = skill_tools.create_user_skill(
        name="demo-skill",
        description="Demo skill for tests.",
        instructions="Follow these test instructions.",
        allowed_tools=["run_python"],
        dependent_skills=["base-skill"],
    )

    skill_file = user_root / "demo-skill" / "SKILL.md"
    assert result["status"] == "ok"
    assert result["path"] == str(skill_file.resolve())
    assert skill_file.exists()

    loaded = load_skill_from_dir(skill_file.parent)
    assert loaded.name == "demo-skill"
    assert loaded.description == "Demo skill for tests."
    assert loaded.instructions == "Follow these test instructions."
    assert loaded.frontmatter.allowed_tools == "run_python"
    assert loaded.frontmatter.metadata["dependent_skills"] == ["base-skill"]


def test_update_user_skill_replaces_existing_skill(tmp_path, monkeypatch):
    user_root = tmp_path / "user-skills"
    monkeypatch.setattr(skill, "_USER_SKILLS_ROOT", user_root)
    monkeypatch.setattr(skill, "refresh_skills", lambda: {"status": "ok", "count": 1})
    monkeypatch.setattr(skill, "get_default_skill_names", lambda: set())

    skill_tools.create_user_skill(
        name="demo-skill",
        description="Original description.",
        instructions="Original instructions.",
    )

    result = skill_tools.update_user_skill(
        name="demo-skill",
        description="Updated description.",
        instructions="Updated instructions.",
        metadata={"owner": "tests"},
    )

    loaded = load_skill_from_dir(user_root / "demo-skill")
    assert result["status"] == "ok"
    assert loaded.description == "Updated description."
    assert loaded.instructions == "Updated instructions."
    assert loaded.frontmatter.metadata["owner"] == "tests"


def test_create_user_skill_does_not_overwrite_existing_skill(tmp_path, monkeypatch):
    user_root = tmp_path / "user-skills"
    monkeypatch.setattr(skill, "_USER_SKILLS_ROOT", user_root)
    monkeypatch.setattr(skill, "refresh_skills", lambda: {"status": "ok", "count": 1})
    monkeypatch.setattr(skill, "get_default_skill_names", lambda: set())

    skill_tools.create_user_skill(
        name="demo-skill",
        description="Original description.",
        instructions="Original instructions.",
    )

    result = skill_tools.create_user_skill(
        name="demo-skill",
        description="New description.",
        instructions="New instructions.",
    )

    loaded = load_skill_from_dir(user_root / "demo-skill")
    assert result["status"] == "error"
    assert loaded.description == "Original description."


def test_skill_bundle_info_lists_sidecar_files_and_folder(tmp_path, monkeypatch):
    user_root = tmp_path / "user-skills"
    skill_dir = user_root / "demo-skill"
    loaded = _write_bundled_skill(skill_dir)
    monkeypatch.setattr(skill, "ALL_SKILLS", [loaded])
    _isolate_skill_roots(monkeypatch, tmp_path, user_root)

    bundle = skill.skill_bundle_info("demo-skill")

    assert bundle == {
        "skill_dir": str(skill_dir),
        "references": ["references/tips.md"],
        "assets": ["assets/example.md"],
        "scripts": ["scripts/tool.py"],
    }

    hint = skill.format_skill_bundle_hint("demo-skill", bundle)
    assert "references/tips.md" in hint
    assert "assets/example.md" in hint
    assert "scripts/tool.py" in hint
    assert str(skill_dir) in hint
    assert "load_skill_resource(skill_name='demo-skill'" in hint
    assert "run_skill_script" in hint


def test_skill_bundle_info_for_skill_without_sidecars(tmp_path, monkeypatch):
    user_root = tmp_path / "user-skills"
    skill_dir = user_root / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: demo-skill
description: Demo skill for tests.
---
Follow these test instructions.
""",
        encoding="utf-8",
    )
    loaded = load_skill_from_dir(skill_dir)
    monkeypatch.setattr(skill, "ALL_SKILLS", [loaded])
    _isolate_skill_roots(monkeypatch, tmp_path, user_root)

    bundle = skill.skill_bundle_info("demo-skill")

    assert bundle == {
        "skill_dir": str(skill_dir),
        "references": [],
        "assets": [],
        "scripts": [],
    }
    assert skill.format_skill_bundle_hint("demo-skill", bundle) is None


def test_skill_bundle_info_unknown_skill_returns_none():
    assert skill.skill_bundle_info("missing-skill") is None
    assert skill.format_skill_bundle_hint("missing-skill", None) is None


def test_load_skill_tool_result_carries_bundled_files_and_graph_context(
    tmp_path, monkeypatch
):
    from know_do_graph import EdgeRelation, EntryType, KnowDoGraph

    from matcreator.knowledge import query

    user_root = tmp_path / "user-skills"
    skill_dir = user_root / "demo-skill"
    loaded = _write_bundled_skill(skill_dir)
    monkeypatch.setattr(skill, "ALL_SKILLS", [loaded])
    _isolate_skill_roots(monkeypatch, tmp_path, user_root)

    graph = KnowDoGraph(tmp_path / "know-do.db")
    node = graph.add(
        "demo-skill",
        content="Follow these test instructions.",
        entry_type=EntryType.capability,
        tags=["matcreator-skill"],
    )
    neighbor = graph.add(
        "neighbor-skill",
        content="Neighbor content.",
        entry_type=EntryType.capability,
        tags=["matcreator-skill"],
    )
    graph.connect(neighbor.id, node.id, relation=EdgeRelation.dependency)
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    toolset = skill.MatCreatorSkillToolset([loaded])
    load_tool = next(
        t for t in toolset._tools if isinstance(t, skill.MatCreatorLoadSkillTool)
    )
    tool_context = SimpleNamespace(
        invocation_id="test-invocation",
        agent_name="test-agent",
        state={},
    )

    result = asyncio.run(
        load_tool.run_async(args={"skill_name": "demo-skill"}, tool_context=tool_context)
    )

    assert result["skill_name"] == "demo-skill"
    assert result["bundled_files"] == {
        "skill_dir": str(skill_dir),
        "references": ["references/tips.md"],
        "assets": ["assets/example.md"],
        "scripts": ["scripts/tool.py"],
    }
    assert "scripts/tool.py" in result["bundled_files_hint"]
    assert result["attached_context"] == {
        "node_id": node.id,
        "heuristics": 0,
        "limitations": 0,
        "related_skills": ["neighbor-skill"],
    }
    assert "neighbor-skill" in result["attached_context_hint"]
    assert "get_related_skills" in result["attached_context_hint"]
