from __future__ import annotations

from click.testing import CliRunner

from matcreator.scripts import start_agent


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
