from __future__ import annotations

from pathlib import Path

from google.adk.skills import load_skill_from_dir

from matcreator import skill
from matcreator.tools.workspace_tools import get_user_skills_root


def test_skill_creation_guide_loads_as_planning_skill() -> None:
    guide_dir = Path("src/matcreator/skills/guides/skill-creation")

    loaded = load_skill_from_dir(guide_dir)

    assert loaded.name == "skill-creation"
    assert "get_user_skills_root" in loaded.instructions
    assert "Required Checks" in loaded.instructions
    # Ensure skills are loaded before checking PLANNING_SKILL_NAMES (lazy init).
    skill._ensure_skills_loaded()
    assert loaded.name in skill.PLANNING_SKILL_NAMES


def test_get_user_skills_root_returns_configured_root(tmp_path, monkeypatch) -> None:
    user_root = tmp_path / "user-skills"
    monkeypatch.setattr(skill, "_USER_SKILLS_ROOT", user_root)

    assert get_user_skills_root() == str(user_root.resolve())
