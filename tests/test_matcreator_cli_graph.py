from __future__ import annotations

from click.testing import CliRunner

from matcreator.scripts import start_agent


def test_graph_sets_persistent_knowledge_frequencies(monkeypatch, tmp_path) -> None:
    from matcreator import config

    monkeypatch.setattr(config, "_CONFIG_PATH", tmp_path / "config.yaml")

    result = CliRunner().invoke(
        start_agent.main,
        ["graph", "--memorize_frequency", "0", "--review-frequency", "25"],
    )

    assert result.exit_code == 0
    assert "MATCREATOR_MEMORIZATION_FREQUENCY=0" in result.output
    assert "MATCREATOR_REVIEW_FREQUENCY=25" in result.output
    assert config.get_config_value("knowledge.memorization_frequency") == "0"
    assert config.get_config_value("knowledge.review_frequency") == "25"

    monkeypatch.delenv("MATCREATOR_MEMORIZATION_FREQUENCY", raising=False)
    monkeypatch.delenv("MATCREATOR_REVIEW_FREQUENCY", raising=False)
    config.apply_config_env_overrides(pre_env=frozenset())
    assert start_agent.os.environ["MATCREATOR_MEMORIZATION_FREQUENCY"] == "0"
    assert start_agent.os.environ["MATCREATOR_REVIEW_FREQUENCY"] == "25"


def test_graph_clear_memory_calls_maintenance(monkeypatch) -> None:
    from matcreator import skill

    monkeypatch.setattr(
        skill,
        "clear_skill_graph_memory",
        lambda **_kwargs: {"message": "Cleared 3 memory node(s).", "backup_path": None},
    )
    result = CliRunner().invoke(start_agent.main, ["graph", "clear-memory", "--yes"])

    assert result.exit_code == 0
    assert result.output.strip() == "Cleared 3 memory node(s)."


def test_graph_reset_calls_maintenance(monkeypatch) -> None:
    from matcreator import skill

    monkeypatch.setattr(
        skill,
        "reset_skill_graph",
        lambda **_kwargs: {"message": "Reset the skill graph.", "backup_path": None},
    )
    result = CliRunner().invoke(start_agent.main, ["graph", "reset", "--yes"])

    assert result.exit_code == 0
    assert result.output.strip() == "Reset the skill graph."


def test_graph_stats_uses_matcreator_kdg_db(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(start_agent, "_resolve_kdg_cli", lambda: "/tmp/know-do-graph")
    monkeypatch.setattr(
        start_agent,
        "_matcreator_kdg_env",
        lambda: {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
    )

    def fake_run(cmd, check, env=None):
        calls.append((cmd, env or {}))
        return None

    monkeypatch.setattr(start_agent.subprocess, "run", fake_run)

    result = CliRunner().invoke(start_agent.main, ["graph", "stats"])

    assert result.exit_code == 0
    assert calls == [
        (
            ["/tmp/know-do-graph", "graph", "stats"],
            {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
        )
    ]


def test_graph_serve_uses_matcreator_kdg_db(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(start_agent, "_resolve_kdg_cli", lambda: "/tmp/know-do-graph")
    monkeypatch.setattr(
        start_agent,
        "_matcreator_kdg_env",
        lambda: {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
    )

    def fake_run(cmd, check, env=None):
        calls.append((cmd, env or {}))
        return None

    monkeypatch.setattr(start_agent.subprocess, "run", fake_run)

    result = CliRunner().invoke(
        start_agent.main,
        ["graph", "serve", "--host", "127.0.0.1", "--port", "8011"],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            ["/tmp/know-do-graph", "serve", "--host", "127.0.0.1", "--port", "8011"],
            {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
        )
    ]


def test_graph_neighbors_forwards_extra_args(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    monkeypatch.setattr(start_agent, "_resolve_kdg_cli", lambda: "/tmp/know-do-graph")
    monkeypatch.setattr(
        start_agent,
        "_matcreator_kdg_env",
        lambda: {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
    )

    def fake_run(cmd, check, env=None):
        calls.append((cmd, env or {}))
        return None

    monkeypatch.setattr(start_agent.subprocess, "run", fake_run)

    result = CliRunner().invoke(
        start_agent.main,
        ["graph", "neighbors", "entry-123", "--depth", "2"],
    )

    assert result.exit_code == 0
    assert calls == [
        (
            ["/tmp/know-do-graph", "graph", "neighbors", "entry-123", "--depth", "2"],
            {"KDG_DB_PATH": "/tmp/matcreator/know_do_graph.db"},
        )
    ]
