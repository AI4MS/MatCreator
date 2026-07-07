"""Markdown-backed workflow skills and search utilities for MatCreator.

Skills are now loaded in standard ADK format via the top-level skill.py
(google.adk.skills.load_skill_from_dir).  This module bridges ADK Skill
objects to the interface expected by the planning/execution agents and keeps
the guide system unchanged.
"""


import logging
import os
from dataclasses import dataclass
from mimetypes import guess_type

from google.adk.skills import load_skill_from_dir
from google.adk.tools import skill_toolset
from google.adk.tools.function_tool import FunctionTool
from .workspace import workspace_skills_dir
from .tools.workspace_tools import run_skill_script
from .config import get_disabled_skills, get_planning_skills
from pathlib import Path

logger = logging.getLogger(__name__)


def _discover_skill_dirs(skills_root: Path) -> list[Path]:
    """Return every directory under *skills_root* that contains ``SKILL.md``.

    This supports both the original one-level layout::

        skills/<name>/SKILL.md

    and nested skill bundles such as::

        skills/mattergen/mattergen_generation/SKILL.md
    """
    if not skills_root.exists():
        return []

    skill_dirs = {
        skill_md.parent
        for skill_md in skills_root.rglob("SKILL.md")
        if skill_md.is_file()
    }
    return sorted(skill_dirs, key=lambda path: path.relative_to(skills_root).as_posix())


@dataclass(frozen=True)
class SkillSource:
    """Skill source metadata used by the loader and UI."""

    name: str
    root: Path
    editable: bool
    managed: bool
    trusted: bool


_MATCREATOR_HOME = Path(
    os.environ.get("MATCREATOR_HOME", str(Path.home() / ".matcreator"))
).expanduser()
_MODULE_SKILLS_ROOT = Path(__file__).parent / "skills"
_USER_SKILLS_ROOT = _MATCREATOR_HOME / "skills"
_OFFICIAL_SKILLS_ROOT = _USER_SKILLS_ROOT / "official"
_RESERVED_USER_SKILL_DIRS = frozenset({"builtin", "official"})


def user_skills_dir() -> Path:
    """Return the user-global ADK skill root."""
    return _USER_SKILLS_ROOT


def official_skills_dir() -> Path:
    """Return the corporation-maintained official skill root."""
    return _OFFICIAL_SKILLS_ROOT


def skill_sources() -> list[SkillSource]:
    """Return skill sources in precedence order."""
    return [
        SkillSource("builtin", _MODULE_SKILLS_ROOT, editable=True, managed=True, trusted=True),
        SkillSource("official", official_skills_dir(), editable=True, managed=True, trusted=True),
        SkillSource("custom", user_skills_dir(), editable=True, managed=False, trusted=False),
        SkillSource("workspace", workspace_skills_dir(), editable=True, managed=False, trusted=False),
    ]


def _discover_custom_skill_dirs(skills_root: Path) -> list[Path]:
    """Discover user custom skills, excluding managed subdirectories."""
    skill_dirs = []
    for path in _discover_skill_dirs(skills_root):
        try:
            rel_parts = path.relative_to(skills_root).parts
        except ValueError:
            rel_parts = ()
        if rel_parts and rel_parts[0] in _RESERVED_USER_SKILL_DIRS:
            continue
        skill_dirs.append(path)
    return skill_dirs


def _discover_skill_dirs_for_source(source: SkillSource) -> list[Path]:
    if source.name == "custom":
        return _discover_custom_skill_dirs(source.root)
    return _discover_skill_dirs(source.root)


def _skill_dir_map() -> dict[str, Path]:
    """Map loaded skill names to their backing directories."""
    return {name: path for name, (path, _) in _skill_dir_source_map().items()}


def _skill_dir_source_map() -> dict[str, tuple[Path, SkillSource]]:
    """Map loaded skill names to backing directories and source metadata."""
    mapping: dict[str, tuple[Path, SkillSource]] = {}
    for source in skill_sources():
        for path in _discover_skill_dirs_for_source(source):
            mapping.setdefault(path.name, (path, source))
    return mapping


def get_skill_source(skill_name: str) -> SkillSource | None:
    """Return source metadata for a loaded skill name."""
    item = _skill_dir_source_map().get(skill_name)
    return item[1] if item else None


def _language_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".md": "markdown",
        ".py": "python",
        ".sh": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".txt": "text",
        ".csv": "csv",
    }.get(suffix)


def get_default_skill_names() -> set[str]:
    """Return the set of skill names bundled with the module (not workspace overrides)."""
    return {p.name for p in _discover_skill_dirs(_MODULE_SKILLS_ROOT)}


def get_official_skill_names() -> set[str]:
    """Return the set of installed official skill names."""
    return {p.name for p in _discover_skill_dirs(official_skills_dir())}


def get_managed_skill_names() -> set[str]:
    """Return skill names from managed builtin and official sources."""
    return get_default_skill_names() | get_official_skill_names()


def load_skills() -> list:
    """Load builtin, official, user-global custom, then workspace custom skills.

    Skills from later sources whose name collides with an earlier source are rejected
    with a warning.
    """
    seen: dict[str, str] = {}
    skills = []
    for source in skill_sources():
        for path in _discover_skill_dirs_for_source(source):
            if path.name in seen:
                logger.warning(
                    "Skill '%s' in %s conflicts with %s and will be ignored.",
                    path.name,
                    source.name,
                    seen[path.name],
                )
                continue
            seen[path.name] = source.name
            try:
                skills.append(load_skill_from_dir(path))
            except Exception as exc:
                logger.error("Failed to load %s skill '%s', skipping: %s", source.name, path.name, exc)
    return skills


ALL_SKILLS = load_skills()

_PLANNING_CATEGORIES = frozenset({"concepts", "guides"})


def _build_planning_skill_names() -> frozenset[str]:
    names: set[str] = set()
    disabled = set(get_disabled_skills())
    for source in skill_sources():
        for path in _discover_skill_dirs_for_source(source):
            if path.name not in disabled and path.parent.name in _PLANNING_CATEGORIES:
                names.add(path.name)
    for name in get_planning_skills():
        if name not in disabled:
            names.add(name)
    return frozenset(names)


PLANNING_SKILL_NAMES: set[str] = set(_build_planning_skill_names())


class MatCreatorSkillToolset(skill_toolset.SkillToolset):
    """SkillToolset with workspace-aware list and run tools."""

    def __init__(self, skills: list):
        super().__init__(skills=skills)
        kept = [t for t in self._tools
                if t.__class__.__name__ in ('LoadSkillTool', 'LoadSkillResourceTool')]
        self._tools = [
            #FunctionTool(list_workspace_skills),
            *kept,
            FunctionTool(run_skill_script),
        ]

    def _get_skill(self, skill_name: str):
        skill = super()._get_skill(skill_name)
        if skill is not None and skill.name in get_disabled_skills():
            return None
        return skill

    def _list_skills(self) -> list:
        disabled = set(get_disabled_skills())
        return [skill for skill in super()._list_skills() if skill.name not in disabled]

    async def process_llm_request(self, *, tool_context, llm_request) -> None:
        # Suppress the default XML skill-list injection; agents use search_skills instead.
        pass


ALL_SKILLS_TOOLSET = MatCreatorSkillToolset(ALL_SKILLS)


def seed_skills_to_graph() -> dict:
    """Upsert all workspace skills and guides into Know-Do Graph.

    The primary skill node stores the full SKILL.md instruction body so graph
    search can retrieve the same text the agent actually reads. Sidecar files
    such as ``references/*``, ``assets/*``, ``scripts/*``, and ``README.md``
    are attached directly to that node using the Know-Do Graph's native
    attachment fields. Skill and guide nodes are marked immutable — they are
    dev-maintained and will not be silently updated by the extractor or
    synthesizer.

    After all nodes are seeded, ``depends_on`` edges are created between skill
    nodes based on the ``dependent_skills`` field in each SKILL.md's metadata.
    """
    from know_do_graph import (
        EdgeRelation,
        EntryMetadata,
        EntryType,
        NodeAsset,
        RefinementStatus,
        ScriptAttachment,
        SkillLevel,
        VerificationStatus,
    )
    from .knowledge.kdg_memory import connect_once, upsert_entry
    from .knowledge.query import _get_kg
    from .guide import ALL_GUIDES

    kg = _get_kg()
    seeded = 0
    attachments_seeded = 0
    skill_node_ids: dict[str, str] = {}
    skill_dirs = _skill_dir_map()

    for skill in ALL_SKILLS:
        content = (skill.instructions or skill.description or "").strip()
        assets: list[NodeAsset] = []
        internal_refs: list[str] = []
        for relative_path, resource_content in skill.resources.references.items():
            assets.append(
                NodeAsset(
                    folder="references",
                    filename=relative_path,
                    kind="reference",
                    content=resource_content.strip(),
                    language=_language_for_path(relative_path),
                    mime_type=guess_type(relative_path)[0],
                    metadata={"skill_name": skill.name, "relative_path": f"references/{relative_path}"},
                )
            )
            internal_refs.append(f"references/{relative_path}")
        for relative_path, resource_content in skill.resources.assets.items():
            assets.append(
                NodeAsset(
                    folder="assets",
                    filename=relative_path,
                    kind="asset",
                    content=resource_content.strip(),
                    language=_language_for_path(relative_path),
                    mime_type=guess_type(relative_path)[0],
                    metadata={"skill_name": skill.name, "relative_path": f"assets/{relative_path}"},
                )
            )
        scripts = [
            ScriptAttachment(
                filename=relative_path,
                language=_language_for_path(relative_path) or "text",
                content=resource_content.src.strip(),
                description=f"Bundled script for skill '{skill.name}'.",
            )
            for relative_path, resource_content in skill.resources.scripts.items()
            if resource_content.src.strip()
        ]
        # Mirror scripts into assets because the current Know-Do Graph build
        # persists assets/internal_refs but drops scripts on write.
        for relative_path, resource_content in skill.resources.scripts.items():
            script_content = resource_content.src.strip()
            if not script_content:
                continue
            assets.append(
                NodeAsset(
                    folder="scripts",
                    filename=relative_path,
                    kind="script",
                    content=script_content,
                    language=_language_for_path(relative_path),
                    mime_type=guess_type(relative_path)[0],
                    metadata={"skill_name": skill.name, "relative_path": f"scripts/{relative_path}"},
                )
            )
        skill_dir = skill_dirs.get(skill.name)
        if skill_dir is not None:
            readme_path = skill_dir / "README.md"
            if readme_path.exists():
                try:
                    readme_content = readme_path.read_text(encoding="utf-8").strip()
                    if readme_content:
                        assets.append(
                            NodeAsset(
                                folder="docs",
                                filename="README.md",
                                kind="readme",
                                content=readme_content,
                                language="markdown",
                                mime_type="text/markdown",
                                metadata={"skill_name": skill.name, "relative_path": "README.md"},
                            )
                        )
                        internal_refs.append("README.md")
                except OSError as exc:
                    logger.warning("Failed to read %s: %s", readme_path, exc)
        frontmatter_metadata = skill.frontmatter.metadata or {}
        entry_type_value = frontmatter_metadata.get("entry_type") or frontmatter_metadata.get("type")
        entry_type = EntryType.capability
        if entry_type_value:
            try:
                entry_type = EntryType(str(entry_type_value))
            except ValueError:
                logger.warning("Ignoring invalid entry_type for skill '%s': %s", skill.name, entry_type_value)
        skill_level_value = frontmatter_metadata.get("skill_level")
        skill_level = SkillLevel.L1
        if skill_level_value:
            try:
                skill_level = SkillLevel(str(skill_level_value))
            except ValueError:
                logger.warning("Ignoring invalid skill_level for skill '%s': %s", skill.name, skill_level_value)
        extra_tags = [
            str(tag).strip()
            for tag in frontmatter_metadata.get("tags", [])
            if str(tag).strip()
        ]
        tags = list(dict.fromkeys(["matcreator-skill", "managed", *extra_tags]))
        node, created = upsert_entry(
            kg,
            title=skill.name,
            content=content,
            entry_type=entry_type,
            tags=tags,
            internal_refs=internal_refs,
            scripts=scripts,
            assets=assets,
            metadata=EntryMetadata(
                source_provenance="SKILL.md",
                refinement_status=RefinementStatus.validated,
                verification_status=VerificationStatus.peer_reviewed,
                skill_level=skill_level,
                custom={"managed_by": "matcreator", "kind": "skill"},
            ),
        )
        skill_node_ids[skill.name] = node.id
        seeded += int(created)
        attachments_seeded += len(internal_refs) + len(assets)

    for guide in ALL_GUIDES:
        _, created = upsert_entry(
            kg,
            title=guide.name,
            content=guide.instructions or guide.description or "",
            entry_type=EntryType.procedure,
            tags=["matcreator-skill", "matcreator-guide", "managed"],
            metadata=EntryMetadata(
                source_provenance="guide",
                refinement_status=RefinementStatus.validated,
                verification_status=VerificationStatus.peer_reviewed,
                skill_level=SkillLevel.L2,
                custom={"managed_by": "matcreator", "kind": "guide"},
            ),
        )
        seeded += int(created)

    # Create edges from dependent_skills metadata. For L3/L4 nodes this field
    # means "attached parent skills" so progressive retrieval can scope them.
    edges_created = 0
    for skill in ALL_SKILLS:
        metadata = skill.frontmatter.metadata or {}
        deps = metadata.get("dependent_skills", [])
        entry_type_value = metadata.get("entry_type") or metadata.get("type")
        skill_level_value = metadata.get("skill_level")
        relation = EdgeRelation.dependency
        if entry_type_value == "heuristic" or skill_level_value == "L3":
            relation = EdgeRelation.heuristic_for
        elif entry_type_value == "constraint" or skill_level_value == "L4":
            relation = EdgeRelation.constraint_on
        src_id = skill_node_ids.get(skill.name)
        for dep_name in deps:
            tgt_id = skill_node_ids.get(dep_name)
            if src_id and tgt_id:
                edges_created += int(
                    connect_once(
                        kg,
                        src_id,
                        tgt_id,
                        relation=relation,
                    )
                )
            else:
                logger.warning(
                    "dependent_skills: '%s' references unknown skill '%s'",
                    skill.name, dep_name,
                )

    kg.refresh()
    return {
        "status": "ok",
        "seeded": seeded,
        "attachments_seeded": attachments_seeded,
        "edges_created": edges_created,
    }


def refresh_skills() -> dict:
    """Reload all skills from the workspace and re-seed the knowledge graph.

    Call this after creating or modifying a skill to make it available
    in the current session without restarting.
    """
    new_skills = load_skills()
    ALL_SKILLS.clear()
    ALL_SKILLS.extend(new_skills)
    PLANNING_SKILL_NAMES.clear()
    PLANNING_SKILL_NAMES.update(_build_planning_skill_names())
    seed_result = seed_skills_to_graph()
    return {
        "status": "ok",
        "skills": [s.name for s in new_skills],
        "count": len(new_skills),
        "message": f"Refreshed {len(new_skills)} skills; seeded {seed_result['seeded']} nodes into knowledge graph.",
    }
