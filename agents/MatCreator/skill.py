"""Markdown-backed workflow skills and search utilities for MatCreator.

Skills are now loaded in standard ADK format via the top-level skill.py
(google.adk.skills.load_skill_from_dir).  This module bridges ADK Skill
objects to the interface expected by the planning/execution agents and keeps
the guide system unchanged.
"""


from google.adk import Agent
from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from .constants import _SKILLS_DIR
import pathlib


def load_skills() -> list:
    """Load all skills from _SKILLS_DIR that contain a SKILL.md file."""
    skills_root = pathlib.Path(_SKILLS_DIR)
    skills = []
    for skill_dir in sorted(skills_root.iterdir()):
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            skills.append(load_skill_from_dir(skill_dir))
    return skills


ALL_SKILLS = load_skills()

ALL_SKILLS_TOOLSET = skill_toolset.SkillToolset(
    skills=ALL_SKILLS
    )