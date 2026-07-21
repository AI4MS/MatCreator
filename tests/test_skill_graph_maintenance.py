from pathlib import Path

from know_do_graph import EntryType, KnowDoGraph

from matcreator import guide, skill
from matcreator.knowledge import query


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {name} description.
metadata:
  dependent_skills: []
---
Instructions for {name}.
""",
        encoding="utf-8",
    )


def test_clear_memory_preserves_durable_nodes(tmp_path, monkeypatch) -> None:
    graph = KnowDoGraph(tmp_path / "graph.db")
    durable = graph.add("durable", content="Keep me", entry_type=EntryType.capability)
    graph.memory("one").add("first memory")
    graph.memory("two").add("second memory")
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = skill.clear_skill_graph_memory()

    assert result["deleted"] == 2
    assert result["failed"] == 0
    backup = KnowDoGraph(Path(result["backup_path"]))
    assert sum(entry.entry_type == EntryType.memory for entry in backup.list(limit=20)) == 2
    backup.close()
    assert graph.get(durable.id) is not None
    assert all(entry.entry_type != EntryType.memory for entry in graph.list(limit=20))


def test_reset_removes_learned_nodes_then_reseeds_installed_skills(
    tmp_path, monkeypatch
) -> None:
    graph = KnowDoGraph(tmp_path / "graph.db")
    graph.add("learned", content="Remove me", entry_type=EntryType.heuristic)
    graph.memory("one").add("Remove this memory")
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    def fake_refresh():
        graph.add(
            "custom-skill",
            content="Restored from SKILL.md",
            entry_type=EntryType.capability,
            tags=["matcreator-skill", "skill-source:workspace"],
        )
        return {"count": 1}

    monkeypatch.setattr(skill, "refresh_skills", fake_refresh)

    result = skill.reset_skill_graph()

    entries = graph.list(limit=20)
    assert result["deleted"] == 2
    backup = KnowDoGraph(Path(result["backup_path"]))
    assert {entry.title for entry in backup.list(limit=20)} == {
        "learned",
        "Remove this memory",
    }
    backup.close()
    assert [entry.title for entry in entries] == ["custom-skill"]


def test_clear_memory_can_explicitly_skip_backup(tmp_path, monkeypatch) -> None:
    graph = KnowDoGraph(tmp_path / "graph.db")
    graph.memory("one").add("memory")
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    result = skill.clear_skill_graph_memory(create_backup=False)

    assert result["backup_path"] is None
    assert not (tmp_path / "backups").exists()


def test_refresh_adds_and_removes_frontend_custom_skills(tmp_path, monkeypatch) -> None:
    builtin_root = tmp_path / "builtin"
    custom_root = tmp_path / "custom"
    _write_skill(builtin_root, "builtin-skill")
    _write_skill(custom_root, "custom-skill")
    graph = KnowDoGraph(tmp_path / "graph.db")

    sources = [
        skill.SkillSource("builtin", builtin_root, True, True, True),
        skill.SkillSource("workspace", custom_root, True, False, False),
    ]
    monkeypatch.setattr(skill, "skill_sources", lambda: sources)
    monkeypatch.setattr(skill, "_MODULE_SKILLS_ROOT", builtin_root)
    monkeypatch.setattr(skill, "workspace_skills_dir", lambda: custom_root)
    monkeypatch.setattr(skill, "ALL_SKILLS", [])
    monkeypatch.setattr(guide, "ALL_GUIDES", [])
    monkeypatch.setattr(query, "_get_kg", lambda: graph)

    skill.refresh_skills()
    custom = next(entry for entry in graph.list(limit=20) if entry.title == "custom-skill")
    assert "skill-source:workspace" in custom.tags
    assert custom.metadata.custom["skill_source"] == "workspace"

    (custom_root / "custom-skill" / "SKILL.md").unlink()
    (custom_root / "custom-skill").rmdir()
    skill.refresh_skills()

    assert {entry.title for entry in graph.list(limit=20)} == {"builtin-skill"}
