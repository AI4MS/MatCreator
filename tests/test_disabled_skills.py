import asyncio
from types import SimpleNamespace


def test_set_disabled_skill_names_updates_graph_nodes(monkeypatch, tmp_path):
    from know_do_graph import EntryMetadata, EntryType, KnowDoGraph
    from matcreator import skill
    from matcreator.knowledge import query
    from matcreator.knowledge.kdg_memory import is_entry_disabled

    graph = KnowDoGraph(tmp_path / "know-do.db")
    enabled = graph.add(
        "enabled-skill",
        entry_type=EntryType.capability,
        tags=["matcreator-skill"],
        metadata=EntryMetadata(custom={}),
    )
    disabled = graph.add(
        "disabled-skill",
        entry_type=EntryType.capability,
        tags=["matcreator-skill"],
        metadata=EntryMetadata(custom={}),
    )
    monkeypatch.setattr(query, "_get_kg", lambda: graph)
    monkeypatch.setattr(
        skill,
        "ALL_SKILLS",
        [SimpleNamespace(name="enabled-skill"), SimpleNamespace(name="disabled-skill")],
    )

    result = skill.set_disabled_skill_names({"disabled-skill", "unknown-skill"})

    assert result["disabled"] == ["disabled-skill"]
    assert result["changed"] == 1
    assert not is_entry_disabled(graph.get(enabled.id))
    assert is_entry_disabled(graph.get(disabled.id))
    assert skill.get_disabled_skill_names() == {"disabled-skill"}


def test_legacy_config_disabled_skills_migrate_to_graph_once(monkeypatch):
    from matcreator import config, skill

    legacy_config = {"skills": {"disabled": ["atomic-structure"], "module_root": "/skills"}}
    saved: list[dict] = []
    monkeypatch.setattr(config, "load_config", lambda: legacy_config)
    monkeypatch.setattr(config, "save_config", lambda value: saved.append(value.copy()))
    monkeypatch.setattr(
        skill,
        "set_disabled_skill_names",
        lambda names: {"disabled": sorted(names), "changed": 1},
    )

    result = skill.migrate_legacy_disabled_skill_config()

    assert result == {"migrated": True, "disabled": ["atomic-structure"], "changed": 1}
    assert legacy_config == {"skills": {"module_root": "/skills"}}
    assert saved


def test_empty_legacy_disabled_list_does_not_reenable_graph_nodes(monkeypatch):
    from matcreator import config, skill

    legacy_config = {"skills": {"disabled": []}}
    monkeypatch.setattr(config, "load_config", lambda: legacy_config)
    monkeypatch.setattr(config, "save_config", lambda _value: None)
    monkeypatch.setattr(
        skill,
        "set_disabled_skill_names",
        lambda _names: (_ for _ in ()).throw(AssertionError("must not synchronize an empty legacy list")),
    )

    result = skill.migrate_legacy_disabled_skill_config()

    assert result["migrated"] is True
    assert result["disabled"] == []
    assert legacy_config == {}


def test_executor_skill_toolset_hides_disabled_skills(monkeypatch):
    from matcreator.skill import MatCreatorSkillToolset

    enabled_skill = SimpleNamespace(name="enabled-skill")
    disabled_skill = SimpleNamespace(name="disabled-skill")
    monkeypatch.setattr(
        "matcreator.skill.get_disabled_skill_names",
        lambda: {"disabled-skill"},
    )

    toolset = MatCreatorSkillToolset([enabled_skill, disabled_skill])

    assert toolset._get_skill("enabled-skill") is enabled_skill
    assert toolset._get_skill("disabled-skill") is None
    assert [skill.name for skill in toolset._list_skills()] == ["enabled-skill"]


def test_run_skill_script_refuses_disabled_skill(monkeypatch):
    from matcreator.tools.workspace_tools import run_skill_script

    monkeypatch.setattr(
        "matcreator.skill.is_skill_disabled",
        lambda name: name == "disabled-skill",
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


def test_bulk_toggle_unofficial_skill_nodes_uses_kdg_disabled_state(monkeypatch):
    from matcreator.skill import set_unofficial_skill_nodes_disabled

    def entry(node_id, source, disabled=False, entry_type="capability"):
        return SimpleNamespace(
            id=node_id,
            tags=["matcreator-skill", f"skill-source:{source}"],
            metadata=SimpleNamespace(custom={"skill_source": source}, disabled=disabled),
            entry_type=entry_type,
        )

    custom = entry("custom", "custom")
    workspace = entry("workspace", "workspace", disabled=True)
    builtin = entry("builtin", "builtin", disabled=True)
    official = entry("official", "official")
    limitation = entry("limitation", "builtin", entry_type="constraint")
    memory = SimpleNamespace(
        id="memory",
        tags=["matcreator-memory"],
        metadata=SimpleNamespace(custom={"memory": {"session_id": "test"}}, disabled=False),
        entry_type="memory",
    )

    class FakeGraph:
        def __init__(self):
            self.entries = [custom, workspace, builtin, official, memory, limitation]
            self.calls = []
            self.refreshed = False

        def list(self, *, disabled, **_kwargs):
            return [item for item in self.entries if item.metadata.disabled is disabled]

        def set_disabled(self, node_id, disabled):
            self.calls.append((node_id, disabled))
            next(item for item in self.entries if item.id == node_id).metadata.disabled = disabled

        def refresh(self):
            self.refreshed = True

    graph = FakeGraph()
    monkeypatch.setattr("matcreator.knowledge.query._get_kg", lambda: graph)

    result = set_unofficial_skill_nodes_disabled(True)

    assert result == {
        "disabled": True,
        "affected": 4,
        "changed": 3,
        "node_ids": ["custom", "memory", "limitation", "workspace"],
        "restored_official": 1,
    }
    assert graph.calls == [
        ("custom", True),
        ("memory", True),
        ("limitation", True),
        ("builtin", False),
    ]
    assert graph.refreshed is True
    assert builtin.metadata.disabled is False
    assert official.metadata.disabled is False
