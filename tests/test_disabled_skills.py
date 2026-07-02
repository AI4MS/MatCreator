import asyncio
from types import SimpleNamespace


def test_executor_skill_toolset_hides_disabled_skills(monkeypatch):
    from matcreator.skill import MatCreatorSkillToolset

    enabled_skill = SimpleNamespace(name="enabled-skill")
    disabled_skill = SimpleNamespace(name="disabled-skill")
    monkeypatch.setattr(
        "matcreator.skill.get_disabled_skills",
        lambda: ["disabled-skill"],
    )

    toolset = MatCreatorSkillToolset([enabled_skill, disabled_skill])

    assert toolset._get_skill("enabled-skill") is enabled_skill
    assert toolset._get_skill("disabled-skill") is None
    assert [skill.name for skill in toolset._list_skills()] == ["enabled-skill"]


def test_run_skill_script_refuses_disabled_skill(monkeypatch):
    from matcreator.tools.workspace_tools import run_skill_script

    monkeypatch.setattr(
        "matcreator.config.get_disabled_skills",
        lambda: ["disabled-skill"],
    )

    result = asyncio.run(
        run_skill_script(
            "disabled-skill",
            "script.py",
            "",
            SimpleNamespace(state={}),
        )
    )

    assert result == "Skill 'disabled-skill' is disabled."