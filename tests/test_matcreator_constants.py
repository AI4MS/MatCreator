from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_default_kdg_db_path_points_to_repo_local_adk(monkeypatch) -> None:
    monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)

    constants = importlib.import_module("src.matcreator.constants")

    expected = Path.home() / ".matcreator" / ".adk" / "know_do_graph.db"

    assert constants.DEFAULT_KDG_DB_PATH == expected
    assert constants.KNOW_DO_GRAPH_DB == expected


def test_repo_local_kdg_db_is_treated_as_legacy_source(monkeypatch) -> None:
    monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)

    constants = importlib.import_module("src.matcreator.constants")

    assert constants.LEGACY_UNIFIED_GRAPH_DB == (
        Path(__file__).resolve().parents[1] / "agents" / "MatCreator" / ".adk" / "know_do_graph.db"
    )


def test_legacy_minimax_env_is_normalized(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("MINIMAX_API_KEY", "legacy-key")
    monkeypatch.setenv("MINIMAX_API_BASE", "https://legacy.example/v1")
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)

    constants = importlib.import_module("src.matcreator.constants")

    assert constants.LLM_API_KEY == "legacy-key"
    assert constants.LLM_BASE_URL == "https://legacy.example/v1"


def test_embedding_model_is_forwarded_to_kdg_embedder(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MATCREATOR_HOME", raising=False)
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    monkeypatch.delenv("KDG_EMBED_PROVIDER", raising=False)
    monkeypatch.delenv("KDG_EMBED_MODEL", raising=False)
    monkeypatch.delenv("KDG_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("KDG_EMBED_BASE_URL", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL", "minimax/qwen3-embedding-8b")
    monkeypatch.setenv("LLM_API_KEY", "embed-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://embed.example/v1")
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)

    constants = importlib.import_module("src.matcreator.constants")

    assert constants.os.environ["KDG_EMBED_PROVIDER"] == "openai"
    assert constants.os.environ["KDG_EMBED_MODEL"] == "minimax/qwen3-embedding-8b"
    assert constants.os.environ["KDG_EMBED_API_KEY"] == "embed-key"
    assert constants.os.environ["KDG_EMBED_BASE_URL"] == "https://embed.example/v1"


def test_server_mode_config_overrides_container_defaults(monkeypatch, tmp_path: Path) -> None:
    matcreator_home = tmp_path / "worker-home"
    matcreator_home.mkdir()
    (matcreator_home / "config.yaml").write_text(
        "llm:\n"
        "  model: openai/user-model\n"
        "env:\n"
        "  MP_API_KEY: user-mp-key\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATCREATOR_MODE", "server")
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    monkeypatch.setenv("LLM_MODEL", "openai/container-default")
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    monkeypatch.delenv("MP_API_KEY", raising=False)
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)

    constants = importlib.import_module("src.matcreator.constants")

    assert constants.LLM_MODEL == "openai/user-model"
    assert constants.os.environ["LLM_MODEL"] == "openai/user-model"
    assert constants.os.environ["MP_API_KEY"] == "user-mp-key"


def test_api_server_startup_applies_config_env_to_harness(monkeypatch, tmp_path: Path) -> None:
    matcreator_home = tmp_path / "worker-home"
    matcreator_home.mkdir()
    (matcreator_home / "config.yaml").write_text(
        "llm:\n"
        "  model: openai/user-harness-model\n"
        "env:\n"
        "  FRONTEND_SET_FLAG: visible-to-harness\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MATCREATOR_MODE", "server")
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    monkeypatch.setenv("LLM_MODEL", "openai/container-default")
    monkeypatch.delenv("FRONTEND_SET_FLAG", raising=False)
    for module_name in (
        "matcreator.config",
        "matcreator.ports",
        "src.matcreator.scripts.start_agent",
    ):
        sys.modules.pop(module_name, None)

    start_agent = importlib.import_module("src.matcreator.scripts.start_agent")
    start_agent._apply_harness_config_env()

    assert start_agent.os.environ["LLM_MODEL"] == "openai/user-harness-model"
    assert start_agent.os.environ["FRONTEND_SET_FLAG"] == "visible-to-harness"


def test_matcreator_home_overrides_user_storage_paths(monkeypatch, tmp_path: Path) -> None:
    matcreator_home = tmp_path / "server-home"
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    monkeypatch.delenv("KDG_DB_PATH", raising=False)
    sys.modules.pop("src.matcreator.constants", None)
    sys.modules.pop("src.matcreator.config", None)
    sys.modules.pop("src.matcreator.workspace", None)

    constants = importlib.import_module("src.matcreator.constants")
    config = importlib.import_module("src.matcreator.config")
    workspace = importlib.import_module("src.matcreator.workspace")

    assert constants.DEFAULT_KDG_DB_PATH == matcreator_home / ".adk" / "know_do_graph.db"
    assert config._CONFIG_PATH == matcreator_home / "config.yaml"
    assert workspace.get_workspace_root() == (matcreator_home / "workspace").resolve()


def test_matcreator_config_path_overrides_config_file_location(monkeypatch, tmp_path: Path) -> None:
    matcreator_home = tmp_path / "server-home"
    config_path = tmp_path / "service-config.yaml"
    monkeypatch.setenv("MATCREATOR_HOME", str(matcreator_home))
    monkeypatch.setenv("MATCREATOR_CONFIG_PATH", str(config_path))
    sys.modules.pop("src.matcreator.config", None)

    config = importlib.import_module("src.matcreator.config")

    assert config._CONFIG_PATH == config_path
