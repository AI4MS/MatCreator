"""Markdown-backed workflow skills and search utilities for MatCreator.

Loading order (later entries override earlier ones with the same skill name):
1. Built-in package skills  : knowledge/skills/*.md  and  knowledge/skills/*/*.md
2. Workspace overlay skills : $MATCLAW_WORKSPACE/skills/*.md  and  …/skills/*/*.md
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
from ..constants import _SKILLS_DIR, _GUIDES_DIR, _workspace_skills_dir, _workspace_guides_dir


@dataclass(frozen=True)
class Skill:
    """Container for agent skills from markdown skills."""
    instruction: str
    description: str
    needed_tools: List[str]
    dependent_skills: List[str]
    name: str
    source_path: str


@dataclass(frozen=True)
class Guide:
    """Higher-level guidance on how to organise and deploy skills for specific tasks."""
    name: str
    description: str
    body: str
    tags: List[str]
    skills: List[str]
    source_path: str

def _parse_list_value(raw_value: str) -> List[str]:
    value = (raw_value or "").strip()
    if not value:
        return []
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [item.strip().strip("\"'") for item in inner.split(",") if item.strip()]
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_skill_markdown(path: Path) -> Skill:
    raw_text = path.read_text(encoding="utf-8")
    stripped = raw_text.strip()

    metadata: Dict[str, str] = {}
    body = raw_text

    if stripped.startswith("---"):
        first_sep = raw_text.find("---")
        second_sep = raw_text.find("\n---", first_sep + 3)
        if second_sep != -1:
            frontmatter = raw_text[first_sep + 3:second_sep].strip()
            body = raw_text[second_sep + 4:].strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()

    name = metadata.get("name") or path.stem
    description = metadata.get("description", "")
    needed_tools = _parse_list_value(metadata.get("tools", ""))
    dependent_skills = _parse_list_value(metadata.get("dependent_skills", ""))

    return Skill(
        instruction=body.strip(),
        description=description,
        needed_tools=needed_tools,
        dependent_skills=dependent_skills,
        name=name,
        source_path=str(path),
    )


def _collect_skill_paths(root: Path) -> List[Path]:
    """Yield skill markdown files from *root*, supporting both layouts:

    - Flat   : root/skill_name.md
    - Subdir : root/skill_name/skill_name.md  (canonical MatClaw layout)

    Subdirectory layout takes precedence when both exist for the same stem.
    """
    flat: Dict[str, Path] = {}
    for p in sorted(root.glob("*.md")):
        flat[p.stem] = p

    subdir: Dict[str, Path] = {}
    for p in sorted(root.glob("*/*.md")):
        # Only match canonical pattern: skills/FOO/FOO.md
        if p.stem == p.parent.name:
            subdir[p.stem] = p

    merged = {**flat, **subdir}  # subdir wins
    return list(merged.values())


def _load_skill_registry() -> Dict[str, Skill]:
    """Build the merged skill registry (built-ins + workspace overlay).

    NOT cached with lru_cache so that runtime workspace changes (e.g. a new
    skill written by the agent) are picked up on the next call.
    """
    registry: Dict[str, Skill] = {}

    # 1. Built-in package skills
    if _SKILLS_DIR.exists():
        for md_path in _collect_skill_paths(_SKILLS_DIR):
            skill = _parse_skill_markdown(md_path)
            registry[skill.name] = skill

    # 2. Workspace overlay (may override built-ins)
    try:
        ws_skills = _workspace_skills_dir()
        if ws_skills.exists():
            for md_path in _collect_skill_paths(ws_skills):
                skill = _parse_skill_markdown(md_path)
                registry[skill.name] = skill
    except Exception:
        pass  # workspace not configured — silently ignore

    return registry


def _parse_guide_markdown(path: Path) -> Guide:
    raw_text = path.read_text(encoding="utf-8")
    stripped = raw_text.strip()

    metadata: Dict[str, str] = {}
    body = raw_text

    if stripped.startswith("---"):
        first_sep = raw_text.find("---")
        second_sep = raw_text.find("\n---", first_sep + 3)
        if second_sep != -1:
            frontmatter = raw_text[first_sep + 3:second_sep].strip()
            body = raw_text[second_sep + 4:].strip()
            for line in frontmatter.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()

    return Guide(
        name=metadata.get("name") or path.stem,
        description=metadata.get("description", ""),
        body=body.strip(),
        tags=_parse_list_value(metadata.get("tags", "")),
        skills=_parse_list_value(metadata.get("skills", "")),
        source_path=str(path),
    )


def _load_guide_registry() -> List[Guide]:
    """Build merged guide list (built-ins + workspace overlay, workspace wins on name clash)."""
    seen: Dict[str, Guide] = {}

    if _GUIDES_DIR.exists():
        for p in sorted(_GUIDES_DIR.glob("*.md")):
            g = _parse_guide_markdown(p)
            seen[g.name] = g

    try:
        ws_guides = _workspace_guides_dir()
        if ws_guides.exists():
            for p in sorted(ws_guides.glob("*.md")):
                g = _parse_guide_markdown(p)
                seen[g.name] = g
    except Exception:
        pass

    return list(seen.values())


def list_guide_metadata() -> List[Dict[str, str]]:
    """Return planner-facing guide summaries (name, description, tags) — no body."""
    return [
        {
            "name": g.name,
            "description": g.description,
            "tags": ", ".join(g.tags),
            "skills": ", ".join(g.skills),
        }
        for g in _load_guide_registry()
    ]


def load_guide_content(guide_name: str) -> dict:
    """Fetch the full body of a guide by name.
    Call this before building or updating an execution plan when a guide is
    relevant to the user's goal. 
    Can be called multiple times before plan building as the agent discovers relevant guides.

    Args:
        guide_name: Exact guide name as listed by list_guide_metadata.

    Returns:
        Dict with ``name``, ``description``, ``tags``, ``skills``, and ``body``
        (the full markdown content), or an ``error`` key if not found.
    """
    registry = {g.name: g for g in _load_guide_registry()}
    guide = registry.get(guide_name)
    if guide is None:
        available = ", ".join(sorted(registry.keys())) or "<none>"
        return {"error": f"Guide '{guide_name}' not found. Available guides: {available}"}
    return {
        "name": guide.name,
        "description": guide.description,
        "tags": guide.tags,
        "skills": guide.skills,
        "body": guide.body,
    }


# Snapshot at import time — callers that need fresh data should call
# _load_skill_registry() directly (e.g. after a workspace skill is created).
SKILL_LIBRARY = _load_skill_registry()


def list_skill_name_descriptions() -> List[Dict[str, str]]:
    """Return planner-facing skill summaries from the loaded skill library."""
    return [
        {
            "name": skill.name,
            "description": skill.description,
        }
        for skill in SKILL_LIBRARY.values()
    ]


def load_skill_content(skill_name: str) -> dict:
    """Fetch the full instruction body of a skill by name.
    Call this when a skill's description is not sufficient to inform planning
    and the full instruction text is needed.

    Args:
        skill_name: Exact skill name as listed by list_skill_name_descriptions.

    Returns:
        Dict with ``name``, ``description``, ``needed_tools``, ``dependent_skills``,
        and ``instruction`` (the full markdown content), or an ``error`` key if not found.
    """
    registry = _load_skill_registry()
    skill = registry.get(skill_name)
    if skill is None:
        available = ", ".join(sorted(registry.keys())) or "<none>"
        return {"error": f"Skill '{skill_name}' not found. Available skills: {available}"}
    return {
        "name": skill.name,
        "description": skill.description,
        "needed_tools": skill.needed_tools,
        "dependent_skills": skill.dependent_skills,
        "instruction": skill.instruction,
    }


__all__ = [
    "Skill",
    "Guide",
    "list_skill_name_descriptions",
    "load_skill_content",
    "list_guide_metadata",
    "load_guide_content",
    "SKILL_LIBRARY",
]
