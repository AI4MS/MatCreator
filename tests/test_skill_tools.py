from __future__ import annotations

from google.adk.skills import load_skill_from_dir

from matcreator import skill
from matcreator.tools import skill_tools


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
