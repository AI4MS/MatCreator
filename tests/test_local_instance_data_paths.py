"""Instance data must follow its selected home, not the installed source tree."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_explicit_home_excludes_shared_repository_migration_sources(monkeypatch, tmp_path):
    instance = tmp_path / "instance-a"
    monkeypatch.setenv("MATCREATOR_HOME", str(instance))
    monkeypatch.setenv("MATCREATOR_CONFIG_PATH", str(instance / "config.yaml"))
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    for name in ("src.matcreator.constants", "src.matcreator.config"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    constants = importlib.import_module("src.matcreator.constants")
    assert constants.LEGACY_UNIFIED_GRAPH_DB == instance / ".adk" / "know_do_graph.db"
    assert constants.LEGACY_UNIFIED_MEMORY_DIR == instance / ".adk" / "memory"
    assert not constants.LEGACY_UNIFIED_GRAPH_DB.is_relative_to(constants._PROJECT_ROOT)


def test_structure_and_artifact_default_to_resolved_instance_workspace(monkeypatch, tmp_path):
    from matcreator import workspace
    from matcreator.tools.util_tools import show_artifact, show_structure

    monkeypatch.delenv("MATCLAW_WORKSPACE", raising=False)
    for name in ("instance-a", "instance-b"):
        home = tmp_path / name
        monkeypatch.setenv("MATCREATOR_HOME", str(home))
        monkeypatch.setattr(workspace, "load_config", lambda: {})
        directory = home / "workspace"
        directory.mkdir(parents=True)
        (directory / "structure.cif").write_text(name)
        (directory / "result.json").write_text(name)
        assert show_structure("structure.cif") == {"structure_path": str((directory / "structure.cif").resolve())}
        assert show_artifact("result.json") == {"artifact_path": str((directory / "result.json").resolve())}


def test_explicit_workspace_still_takes_precedence(monkeypatch, tmp_path):
    from matcreator.tools.util_tools import show_artifact

    workspace = tmp_path / "selected-workspace"
    workspace.mkdir()
    artifact = workspace / "selected.txt"
    artifact.write_text("selected")
    monkeypatch.setenv("MATCREATOR_HOME", str(tmp_path / "instance"))
    monkeypatch.setenv("MATCLAW_WORKSPACE", str(workspace))
    assert show_artifact("selected.txt") == {"artifact_path": str(artifact.resolve())}
