from __future__ import annotations

import asyncio
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


class _FakeImage:
    def __init__(self, image_id: str):
        self.id = image_id


class _FakeContainer:
    def __init__(self, image_id: str):
        self.image = _FakeImage(image_id)


class _FakeImages:
    def __init__(self, image_id: str):
        self._image = _FakeImage(image_id)

    def get(self, _name: str):
        return self._image


class _FakeDockerClient:
    def __init__(self, image_id: str):
        self.images = _FakeImages(image_id)


def _load_web_main(monkeypatch, matcreator_home: Path | None = None):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MATCREATOR_MODE", "local")
    if matcreator_home is not None:
        monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    else:
        monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    for path in (root / "web", root / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    for module_name in ("matcreator.config", "matcreator.constants", "matcreator.ports", "matcreator.workspace"):
        sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(
        "web_main_session_access_test",
        root / "web" / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_web_main_server(monkeypatch, control_home: Path, data_root: Path, host_data_root: Path | None = None):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("MATCREATOR_MODE", "server")
    monkeypatch.setenv("MATCREATOR_CONTROL_PLANE_HOME", str(control_home))
    monkeypatch.setenv("MATCREATOR_DATA_ROOT", str(data_root))
    if host_data_root is not None:
        monkeypatch.setenv("MATCREATOR_HOST_DATA_ROOT", str(host_data_root))
    else:
        monkeypatch.delenv("MATCREATOR_HOST_DATA_ROOT", raising=False)
    monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    for env_key in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "MP_API_KEY", "SERVER_ONLY_FLAG"):
        monkeypatch.delenv(env_key, raising=False)
    for path in (root / "web", root / "src"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    for module_name in ("matcreator.config", "matcreator.constants", "matcreator.ports", "matcreator.workspace"):
        sys.modules.pop(module_name, None)

    spec = importlib.util.spec_from_file_location(
        "web_main_server_env_test",
        root / "web" / "main.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_session_db(path: Path, app_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE sessions (
                app_name TEXT,
                user_id TEXT,
                id TEXT,
                state TEXT,
                create_time TEXT,
                update_time TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE events (
                app_name TEXT,
                user_id TEXT,
                session_id TEXT,
                event_data TEXT,
                timestamp REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
            (app_name, "legacy-display-name", "session-1", '{"answer": 42}', "1", "2"),
        )
        conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (app_name, "another-legacy-name", "session-1", '{"event": "persisted"}', 1.0),
        )
        conn.commit()


def test_server_worker_env_uses_persistent_control_plane_config(monkeypatch, tmp_path):
    control_home = tmp_path / "control-plane" / ".matcreator"
    control_home.mkdir(parents=True)
    (control_home / "config.yaml").write_text(
        "llm:\n"
        "  model: openai/server-default\n"
        "  api_key: server-secret\n"
        "  base_url: https://server.example/v1\n"
        "env:\n"
        "  MP_API_KEY: default-mp-key\n"
        "  SERVER_ONLY_FLAG: enabled\n",
        encoding="utf-8",
    )
    web_main = _load_web_main_server(monkeypatch, control_home, tmp_path / "server-data")

    env_vars = web_main._worker_env_vars()

    assert env_vars["LLM_MODEL"] == "openai/server-default"
    assert env_vars["LLM_API_KEY"] == "server-secret"
    assert env_vars["LLM_BASE_URL"] == "https://server.example/v1"
    assert env_vars["MP_API_KEY"] == "default-mp-key"
    assert env_vars["SERVER_ONLY_FLAG"] == "enabled"


def test_worker_image_check_detects_stale_container(monkeypatch, tmp_path):
    control_home = tmp_path / "control-plane" / ".matcreator"
    control_home.mkdir(parents=True)
    web_main = _load_web_main_server(monkeypatch, control_home, tmp_path / "server-data")

    assert web_main._worker_container_uses_current_image(
        _FakeDockerClient("sha256:new-image"),
        _FakeContainer("sha256:old-image"),
    ) is False
    assert web_main._worker_container_uses_current_image(
        _FakeDockerClient("sha256:new-image"),
        _FakeContainer("sha256:new-image"),
    ) is True


def test_server_worker_shared_mounts_parse_extra_binds(monkeypatch, tmp_path):
    control_home = tmp_path / "control-plane" / ".matcreator"
    control_home.mkdir(parents=True)
    shared_dir = tmp_path / "share"
    writable_dir = tmp_path / "scratch"
    monkeypatch.setenv(
        "MATCREATOR_WORKER_SHARED_MOUNTS",
        f"{shared_dir}:/share,{writable_dir}:/scratch:rw",
    )
    web_main = _load_web_main_server(monkeypatch, control_home, tmp_path / "server-data")

    mounts = web_main._worker_shared_mounts()

    assert mounts[str(shared_dir)] == {"bind": "/share", "mode": "ro"}
    assert mounts[str(writable_dir)] == {"bind": "/scratch", "mode": "rw"}


def test_server_env_config_writes_worker_mounted_user_config(monkeypatch, tmp_path):
    control_home = tmp_path / "control-plane" / ".matcreator"
    control_home.mkdir(parents=True)
    data_root = tmp_path / "container-data"
    host_data_root = tmp_path / "host-data"
    web_main = _load_web_main_server(monkeypatch, control_home, data_root, host_data_root)

    body = SimpleNamespace(values={
        web_main._CUSTOM_ENV_CONFIG_KEY: {"FRONTEND_SET_FLAG": "visible-to-worker"},
    })
    asyncio.run(web_main.update_env_config(body, user_id="alice"))

    host_config = host_data_root / "users" / "alice" / ".matcreator" / "config.yaml"
    container_config = data_root / "users" / "alice" / ".matcreator" / "config.yaml"
    assert "FRONTEND_SET_FLAG: visible-to-worker" in host_config.read_text(encoding="utf-8")
    assert "FRONTEND_SET_FLAG: visible-to-worker" in container_config.read_text(encoding="utf-8")


def test_server_session_summaries_are_scoped_to_user_home(monkeypatch, tmp_path):
    control_home = tmp_path / "control-plane" / ".matcreator"
    control_home.mkdir(parents=True)
    data_root = tmp_path / "server-data"
    web_main = _load_web_main_server(monkeypatch, control_home, data_root)
    db_path = data_root / "users" / "alice" / ".matcreator" / ".adk" / "session.db"
    _create_session_db(db_path, web_main.APP_NAME)

    body = SimpleNamespace(summary="Manual summary")
    asyncio.run(web_main.update_session_summary("session-1", body, user_id="alice"))

    summary_path = data_root / "users" / "alice" / ".matcreator" / ".adk" / "session_summaries.json"
    assert summary_path.exists()
    assert web_main._get_session_summary("session-1", "alice") == "Manual summary"
    sessions = web_main._query_session_summaries("alice")
    assert sessions[0]["summary"] == "Manual summary"


def test_local_adk_restart_env_uses_frontend_config(monkeypatch, tmp_path):
    matcreator_home = tmp_path / ".matcreator"
    matcreator_home.mkdir()
    monkeypatch.setenv("FRONTEND_SET_FLAG", "stale-inherited-value")
    web_main = _load_web_main(monkeypatch, matcreator_home)

    body = SimpleNamespace(values={
        "LLM_MODEL": "openai/local-config-model",
        "GRAPH_AGENT_MODEL": "openai/local-graph-model",
        "REVIEW_AGENT_MODEL": "openai/local-review-model",
        web_main._CUSTOM_ENV_CONFIG_KEY: {"FRONTEND_SET_FLAG": "visible-locally"},
    })
    asyncio.run(web_main.update_env_config(body))
    settings_body = SimpleNamespace(
        planning=None,
        user=None,
        skills=None,
        workspace=None,
        llm={
            "executor_cards": {
                "default": "balanced",
                "cards": {
                    "balanced": {
                        "model": "openai/local-executor-model",
                        "description": "Local executor model",
                    },
                },
            },
        },
    )
    asyncio.run(web_main.update_settings(settings_body))

    env = web_main._local_adk_env()

    assert env["LLM_MODEL"] == "openai/local-config-model"
    assert env["GRAPH_AGENT_MODEL"] == "openai/local-graph-model"
    assert env["REVIEW_AGENT_MODEL"] == "openai/local-review-model"
    assert env["FRONTEND_SET_FLAG"] == "visible-locally"
    config_text = (matcreator_home / "config.yaml").read_text(encoding="utf-8")
    assert "model: openai/local-config-model" in config_text
    assert "graph_agent_model: openai/local-graph-model" in config_text
    assert "review_agent_model: openai/local-review-model" in config_text
    assert "executor_cards:" in config_text
    assert "model: openai/local-executor-model" in config_text
    assert "FRONTEND_SET_FLAG: visible-locally" in config_text


def test_local_mode_lists_sessions_regardless_of_requested_user(monkeypatch, tmp_path):
    web_main = _load_web_main(monkeypatch)
    db_path = tmp_path / "session.db"
    _create_session_db(db_path, web_main.APP_NAME)
    monkeypatch.setattr(web_main, "SESSION_DB_PATH", db_path)
    monkeypatch.setattr(web_main, "_MATCREATOR_MODE", "local")

    sessions = web_main._query_session_summaries("current-user")

    assert [session["id"] for session in sessions] == ["session-1"]
    assert sessions[0]["userId"] == "legacy-display-name"


def test_local_mode_reads_session_detail_regardless_of_requested_user(monkeypatch, tmp_path):
    web_main = _load_web_main(monkeypatch)
    db_path = tmp_path / "session.db"
    _create_session_db(db_path, web_main.APP_NAME)
    monkeypatch.setattr(web_main, "SESSION_DB_PATH", db_path)
    monkeypatch.setattr(web_main, "_MATCREATOR_MODE", "local")

    response = asyncio.run(web_main.get_user_session("current-user", "session-1"))
    payload = json.loads(response.body)

    assert payload["userId"] == "legacy-display-name"
    assert payload["state"] == {"answer": 42}
    assert payload["events"] == [{"event": "persisted"}]