"""Lightweight FastAPI server that exposes agent graph data to the frontend.

Runs alongside the ADK backend.  Ports are resolved via
:func:`~matcreator.ports.get_web_port` and
:func:`~matcreator.ports.get_adk_port` at call time
(env vars > config.yaml > defaults).

Endpoints
---------
GET /api/agent-graph/{session_id}
    Returns the JSON graph file for the session, or an empty graph if not found.
GET /api/workspace/files?path=<path>
    Serves any file from the workspace root (absolute or relative path).
    Returns 403 if the path escapes the workspace root.
GET /api/sessions/{session_id}/files
    Lists all files under the session's working directory.

The vite dev server proxies /api/* here and /run_sse + /apps/* to the ADK server.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import re
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import termios
import threading
import time
from importlib.resources import files
from pathlib import Path
from typing import Any, List
from urllib.parse import unquote

import httpx
import yaml
from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
# Allow importing users_db from the web/ directory
ROOT = Path(__file__).parent.parent
_WEB_DIR = Path(__file__).parent
_MATCREATOR_MODE = os.environ.get("MATCREATOR_MODE", "local")
_SERVER_DATA_ROOT = Path(
    os.environ.get("MATCREATOR_DATA_ROOT", str(ROOT / "server-data"))
).expanduser()
_CONTROL_PLANE_HOME_ENV = Path(
    os.environ.get(
        "MATCREATOR_CONTROL_PLANE_HOME",
        str(_SERVER_DATA_ROOT / "control-plane" / ".matcreator"),
    )
).expanduser()
if _MATCREATOR_MODE == "server":
    os.environ.setdefault("MATCREATOR_HOME", str(_CONTROL_PLANE_HOME_ENV))
if str(_WEB_DIR) not in sys.path:
    sys.path.insert(0, str(_WEB_DIR))

import users_db  # noqa: E402

from matcreator.workspace import get_session_workdir, get_workspace_root, workspace_skills_dir  # noqa: E402
from matcreator.agents.cancellation import (  # noqa: E402
    request_cancellation,
    is_cancellation_requested,
    get_cancellation_reason,
    clear_cancellation,
    request_step_cancellation,
)
from matcreator.agents.graph_logger import AgentGraphLogger  # noqa: E402
from matcreator.agents.session_log import build_session_log_export  # noqa: E402
from matcreator.skill import (  # noqa: E402
    ALL_SKILLS,
    PLANNING_SKILL_NAMES,
    _MODULE_SKILLS_ROOT,
    _discover_skill_dirs,
    _skill_dir_map,
    get_skill_source,
    official_skills_dir,
    refresh_skills,
    get_default_skill_names,
)
from matcreator.config import load_config, save_config, get_disabled_skills  # noqa: E402
from matcreator.config import ENV_TO_YAML, YAML_TO_ENV, SENSITIVE_YAML_KEYS  # noqa: E402
from matcreator.constants import GRAPH_AGENT_MODEL, KNOW_DO_GRAPH_DB  # noqa: E402
from matcreator.control_plane.remote_job_monitor import RemoteJobMonitor  # noqa: E402
from matcreator.control_plane.remote_job_service import RemoteJobService  # noqa: E402
from matcreator.control_plane.remote_jobs import RemoteJobStore  # noqa: E402
from matcreator.control_plane.benchmark_client import BenchmarkApiError, BenchmarkClient  # noqa: E402
from matcreator.control_plane.evaluation_manager import EvaluationManager  # noqa: E402
from matcreator.control_plane.evaluation_runtime import RuntimeOutcome, RuntimeSpec  # noqa: E402
from matcreator.control_plane.evaluation_service import EvaluationService  # noqa: E402
from matcreator.control_plane.evaluations import EvaluationStore  # noqa: E402
from matcreator.control_plane.runs import ManagedRun, ManagedRunRegistry  # noqa: E402
from matcreator.control_plane.worker_supervisor import WorkerSupervisor  # noqa: E402
from matcreator.control_plane.session_question_generator import (  # noqa: E402
    BuiltinLlmQuestionGeneratorPlugin,
    CallableSessionQuestionGenerator,
    StagedSessionQuestionService,
)
from matcreator.knowledge.query import _get_kg  # noqa: E402
from matcreator.knowledge.review import run_review_pipeline  # noqa: E402
from matcreator.ports import get_adk_port, get_local_adk_command, get_web_port, get_worker_base_port  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="MatCreator Graph API", version="1.0.0")
APP_NAME = "MatCreator"


class EvaluationCampaignBody(BaseModel):
    model_name: str
    question_ids: list[str]
    max_parallelism: int = 1
    max_turns: int = 50
    timeout_seconds: int = 600
    flash: bool = False


class EvaluationQuestionSetBody(BaseModel):
    name: str
    question_ids: list[str]
    visibility: str = "private"


class EvaluationQuestionDraftBody(BaseModel):
    title: str
    prompt: str
    expected_deliverables: list[str]
    rubrics: list[dict[str, Any]]
    tags: list[str] = []


class EvaluationQuestionDraftUpdateBody(BaseModel):
    question_yaml: str


class EvaluationQuestionDraftRefineBody(BaseModel):
    instruction: str = ""
_SERVER_HOST_DATA_ROOT = Path(
    os.environ.get("MATCREATOR_HOST_DATA_ROOT", str(_SERVER_DATA_ROOT))
).expanduser()
_CONTROL_PLANE_HOME = _CONTROL_PLANE_HOME_ENV
_LOCAL_MATCREATOR_HOME = Path("~/.matcreator").expanduser()
_MATCREATOR_HOME = _CONTROL_PLANE_HOME if _MATCREATOR_MODE == "server" else _LOCAL_MATCREATOR_HOME
SESSION_DB_PATH = _MATCREATOR_HOME / ".adk" / "session.db"
_ADK_DIR = _MATCREATOR_HOME / ".adk"
_USERS_DATA_ROOT = _SERVER_DATA_ROOT / "users"
_USERS_HOST_ROOT = _SERVER_HOST_DATA_ROOT / "users"
_WORKER_MATCREATOR_HOME = Path("/root/.matcreator")
_WORKER_WORKSPACE_ROOT = _WORKER_MATCREATOR_HOME / "workspace"
DEFAULT_ADMIN_USERS = {"admin"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

SUMMARIES_PATH = ROOT / "agents" / "MatCreator" / ".adk" / "session_summaries.json"
_SENSITIVE_FIELDS = frozenset({"LLM_API_KEY", "BOHRIUM_PASSWORD", "BOHRIUM_ACCESS_KEY"})
_ENV_FIELDS = [
    "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "EMBEDDING_MODEL",
    "GRAPH_AGENT_MODEL", "REVIEW_AGENT_MODEL",
    "BOHRIUM_EMAIL", "BOHRIUM_PASSWORD", "BOHRIUM_ACCESS_KEY", "BOHRIUM_API_URL", "BOHRIUM_PROJECT_ID",
    "BOHRIUM_VASP_IMAGE", "BOHRIUM_VASP_MACHINE",
    "BOHRIUM_DEEPMD_IMAGE", "BOHRIUM_DEEPMD_MACHINE", "DEEPMD_MODEL_PATH",
]
_CUSTOM_ENV_CONFIG_KEY = "CUSTOM_ENV"
_ENV_VALUE_MASK = "***"
_USER_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PROTECTED_USER_ENV_KEYS = frozenset({
    "HOME",
    "PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "MATCREATOR_HOME",
    "MATCREATOR_MODULE_SKILLS_ROOT",
    "MATCREATOR_MODE",
    "MATCREATOR_USER_ID",
})

_adk_process: subprocess.Popen | None = None
_knowledge_review_lock = threading.Lock()
_knowledge_review_task: asyncio.Task | None = None
_knowledge_review_state = {
    "status": "idle",
    "trigger_session_id": None,
    "progress": {"completed": 0, "total": 0, "percent": 0},
    "results": [],
    "errors": [],
    "summary": "",
}
_run_registry = ManagedRunRegistry()
_remote_job_store = RemoteJobStore(_ADK_DIR / "remote-jobs.db")
_remote_job_service = RemoteJobService(_remote_job_store)
_evaluation_store = EvaluationStore(_ADK_DIR / "evaluations.db")
_evaluation_manager = EvaluationManager(
    max_concurrent_attempts=int(os.environ.get("MATCREATOR_EVALUATION_MAX_CONCURRENCY", "4"))
)
_remote_job_monitor = RemoteJobMonitor(_remote_job_store, _remote_job_service)
_remote_job_monitor_task: asyncio.Task[None] | None = None
_remote_job_monitor_stop = asyncio.Event()
_LEGACY_ENV_ALIASES = {
    "LLM_API_KEY": "MINIMAX_API_KEY",
    "LLM_BASE_URL": "MINIMAX_API_BASE",
}


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Log validation details without logging potentially sensitive request bodies."""
    logger.debug(
        "Request validation failed: method=%s path=%s query=%s errors=%s",
        request.method,
        request.url.path,
        dict(request.query_params),
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


def _config_value_for_env_key(env_key: str) -> str:
    yaml_key = ENV_TO_YAML.get(env_key)
    if not yaml_key:
        return ""
    parts = yaml_key.split(".", 1)
    config = load_config()
    if len(parts) == 1:
        value = config.get(parts[0], "")
    else:
        value = config.get(parts[0], {}).get(parts[1], "")
    return "" if value is None else str(value)


def _runtime_env_value(env_key: str) -> str:
    """Resolve a setting from the active runtime plus persisted UI settings."""
    mode = os.environ.get("MATCREATOR_MODE", "local")
    if mode == "local":
        value = (
            _config_value_for_env_key(env_key)
            or os.environ.get(env_key, "")
        )
    else:
        value = (
            os.environ.get(env_key, "")
            or _config_value_for_env_key(env_key)
        )
    if value:
        return value
    legacy_key = _LEGACY_ENV_ALIASES.get(env_key)
    if not legacy_key:
        return ""
    return os.environ.get(legacy_key, "")

# ---------------------------------------------------------------------------
# Server-mode worker management
# ---------------------------------------------------------------------------
# In server mode each user gets a dedicated Docker container running the ADK
# API server.  The control plane (this process) proxies /run_sse and /apps/*
# to the correct worker and manages the container lifecycle.

# _ADK_LOCAL_PORT is resolved at call time via get_adk_port() (env > config.yaml > default).
_WORKER_IMAGE = os.environ.get("MATCREATOR_WORKER_IMAGE", "matcreator-worker:latest")
_WORKER_NETWORK = os.environ.get("MATCREATOR_WORKER_NETWORK", "matcreator-net")
_WORKER_CONNECT_MODE = os.environ.get("MATCREATOR_WORKER_CONNECT_MODE", "network").lower()
_WORKER_BASE_PORT = get_worker_base_port()
_WORKER_IDLE_TIMEOUT_SECONDS = int(os.environ.get("MATCREATOR_WORKER_IDLE_TIMEOUT_SECONDS", "0"))
_WORKER_MEM_LIMIT = os.environ.get("MATCREATOR_WORKER_MEM_LIMIT", "")
_WORKER_CPUS = os.environ.get("MATCREATOR_WORKER_CPUS", "")
_WORKER_PIDS_LIMIT = os.environ.get("MATCREATOR_WORKER_PIDS_LIMIT", "")
_WORKER_SHARED_MOUNTS = os.environ.get("MATCREATOR_WORKER_SHARED_MOUNTS", "")
_WORKSPACE_CLI_TIMEOUT_SECONDS = int(os.environ.get("MATCREATOR_WORKSPACE_CLI_TIMEOUT_SECONDS", "30"))
_WORKSPACE_CLI_OUTPUT_LIMIT = int(os.environ.get("MATCREATOR_WORKSPACE_CLI_OUTPUT_LIMIT", "20000"))



def _safe_user_dir_name(user_id: str) -> str:
    """Return a path-safe user directory name, rejecting path traversal."""
    candidate = (user_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", candidate):
        raise ValueError(f"Invalid user_id for filesystem path: {user_id!r}")
    return candidate


def _user_matcreator_home(user_id: str, *, host: bool = False) -> Path:
    root = _USERS_HOST_ROOT if host else _USERS_DATA_ROOT
    return root / _safe_user_dir_name(user_id) / ".matcreator"


def _remote_job_store_for_owner(owner_id: str) -> RemoteJobStore:
    if _MATCREATOR_MODE == "server":
        if not owner_id:
            raise HTTPException(status_code=400, detail="user_id is required in server mode")
        return RemoteJobStore(_user_matcreator_home(owner_id) / ".adk" / "remote-jobs.db")
    return _remote_job_store


def _remote_job_service_for_owner(owner_id: str) -> RemoteJobService:
    store = _remote_job_store_for_owner(owner_id)
    if store is _remote_job_store:
        return _remote_job_service
    return RemoteJobService(store)


def _evaluation_store_for_owner(owner_id: str) -> EvaluationStore:
    if _MATCREATOR_MODE == "server":
        if not owner_id:
            raise HTTPException(status_code=400, detail="user_id is required in server mode")
        return EvaluationStore(_user_matcreator_home(owner_id) / ".adk" / "evaluations.db")
    return _evaluation_store


def _evaluation_workspace_for_owner(owner_id: str) -> Path:
    if _MATCREATOR_MODE == "server":
        if not owner_id:
            raise HTTPException(status_code=400, detail="user_id is required in server mode")
        return _user_workspace_root(owner_id) / "evaluations"
    return get_workspace_root() / "evaluations"


def _benchmark_client() -> BenchmarkClient:
    benchmark_config = load_config().get("benchmark") or {}
    if not isinstance(benchmark_config, dict):
        benchmark_config = {}
    server_url = (
        os.environ.get("MAT_BENCH_SERVER_URL", "").strip()
        or str(benchmark_config.get("server_url") or "").strip()
    )
    token = (
        os.environ.get("MAT_BENCH_TOKEN", "").strip()
        or str(benchmark_config.get("token") or "").strip()
    )
    if not server_url or not token:
        raise HTTPException(
            status_code=503,
            detail="Benchmark service is not configured. Set MAT_BENCH_SERVER_URL and MAT_BENCH_TOKEN or benchmark.server_url and benchmark.token in config.yaml.",
        )
    return BenchmarkClient(server_url, token)


_benchmark_token_registration_lock = asyncio.Lock()


async def _benchmark_client_for_owner(owner_id: str = "") -> BenchmarkClient:
    """Resolve a benchmark client, registering a development token when needed."""
    config = _load_config_for_user(owner_id)
    benchmark_config = config.get("benchmark") or {}
    if not isinstance(benchmark_config, dict):
        benchmark_config = {}
    server_url = (
        os.environ.get("MAT_BENCH_SERVER_URL", "").strip()
        or str(benchmark_config.get("server_url") or "").strip()
    )
    token = (
        os.environ.get("MAT_BENCH_TOKEN", "").strip()
        or str(benchmark_config.get("token") or "").strip()
    )
    if not server_url:
        raise HTTPException(
            status_code=503,
            detail="Benchmark service is not configured. Set MAT_BENCH_SERVER_URL or benchmark.server_url in config.yaml.",
        )
    if token:
        return BenchmarkClient(server_url, token)
    async with _benchmark_token_registration_lock:
        # Another first-use request may have persisted the token while this request waited.
        config = _load_config_for_user(owner_id)
        benchmark_config = config.get("benchmark") or {}
        if not isinstance(benchmark_config, dict):
            benchmark_config = {}
        token = (
            os.environ.get("MAT_BENCH_TOKEN", "").strip()
            or str(benchmark_config.get("token") or "").strip()
        )
        if token:
            return BenchmarkClient(server_url, token)
        try:
            token = await BenchmarkClient.register_token(server_url)
        except (BenchmarkApiError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Benchmark API token is missing and automatic development-token registration failed: "
                    f"{exc}. Start mat-agent-bench with --allow-token-registration or configure benchmark.token."
                ),
            ) from exc
        updated_benchmark = dict(benchmark_config)
        updated_benchmark["server_url"] = server_url
        updated_benchmark["token"] = token
        updated_config = dict(config)
        updated_config["benchmark"] = updated_benchmark
        _save_config_for_user(updated_config, owner_id)
        return BenchmarkClient(server_url, token)


async def _run_remote_job_monitor() -> None:
    """Reconcile local jobs or each user-owned store in server mode."""
    if _MATCREATOR_MODE != "server":
        await _remote_job_monitor.run()
        return

    monitors: dict[str, RemoteJobMonitor] = {}
    while not _remote_job_monitor_stop.is_set():
        if _USERS_DATA_ROOT.exists():
            for user_root in _USERS_DATA_ROOT.iterdir():
                if not user_root.is_dir():
                    continue
                owner_id = user_root.name
                monitor = monitors.setdefault(
                    owner_id,
                    RemoteJobMonitor(
                        _remote_job_store_for_owner(owner_id),
                        _remote_job_service_for_owner(owner_id),
                    ),
                )
                await monitor.reconcile_once()
        try:
            await asyncio.wait_for(_remote_job_monitor_stop.wait(), timeout=15)
        except TimeoutError:
            pass


def _config_path_for_user(user_id: str = "") -> Path | None:
    if _MATCREATOR_MODE != "server":
        return None
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required in server mode")
    return _user_matcreator_home(user_id) / "config.yaml"


def _config_paths_for_user(user_id: str = "") -> list[Path]:
    path = _config_path_for_user(user_id)
    if path is None:
        return []
    paths = [path]
    host_path = _user_matcreator_home(user_id, host=True) / "config.yaml"
    if host_path != path:
        paths.insert(0, host_path)
    return paths


def _load_config_for_user(user_id: str = "") -> dict[str, Any]:
    paths = _config_paths_for_user(user_id)
    if not paths:
        return load_config()
    path = next((candidate for candidate in paths if candidate.exists()), paths[0])
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}


def _save_config_for_user(config: dict[str, Any], user_id: str = "") -> None:
    paths = _config_paths_for_user(user_id)
    if not paths:
        save_config(config)
        return
    rendered = yaml.dump(config, default_flow_style=False, allow_unicode=True)
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")


def _get_nested_config_value(config: dict[str, Any], dotted_key: str) -> str:
    current: Any = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return ""
        current = current.get(part, {})
    if not isinstance(current, dict):
        return ""
    value = current.get(parts[-1], "")
    return "" if value is None else str(value)


def _set_nested_config_value(config: dict[str, Any], dotted_key: str, value: str) -> None:
    current = config
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        next_value = current.setdefault(part, {})
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _is_sensitive_env_key(env_key: str) -> bool:
    upper = env_key.upper()
    return env_key in _SENSITIVE_FIELDS or any(
        token in upper for token in ("API_KEY", "PASSWORD", "SECRET", "TOKEN", "CREDENTIAL")
    )


def _validate_user_env_key(env_key: str) -> None:
    if not _USER_ENV_KEY_RE.fullmatch(env_key):
        raise HTTPException(status_code=400, detail=f"Invalid environment variable name: {env_key}")
    if env_key in _PROTECTED_USER_ENV_KEYS:
        raise HTTPException(status_code=400, detail=f"Protected environment variable cannot be overridden: {env_key}")


def _masked_env_value(env_key: str, value: str) -> str:
    return _ENV_VALUE_MASK if (_is_sensitive_env_key(env_key) and value) else value


def _custom_env_from_config(config: dict[str, Any]) -> dict[str, str]:
    env_cfg = config.get("env", {})
    if not isinstance(env_cfg, dict):
        return {}
    return {
        str(key): "" if value is None else str(value)
        for key, value in env_cfg.items()
    }


def _config_env_values(config: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for yaml_key, env_key in YAML_TO_ENV.items():
        value = _get_nested_config_value(config, yaml_key)
        if value:
            values[env_key] = value
    for env_key, value in _custom_env_from_config(config).items():
        if not value or not _USER_ENV_KEY_RE.fullmatch(env_key) or env_key in _PROTECTED_USER_ENV_KEYS:
            continue
        values[env_key] = value
    return values


def _local_adk_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_config_env_values(load_config()))
    return env


def _user_adk_dir(user_id: str) -> Path:
    return _user_matcreator_home(user_id) / ".adk"


def _user_workspace_root(user_id: str) -> Path:
    return _user_matcreator_home(user_id) / "workspace"


def _worker_target_url(user_id: str, port: int | None = None) -> str:
    if _WORKER_CONNECT_MODE == "host-port":
        if port is None:
            raise RuntimeError("host-port worker routing requires a host port")
        return f"http://127.0.0.1:{port}"
    return f"http://{_worker_container_name(user_id)}:{get_adk_port()}"


def _worker_shared_mounts() -> dict[str, dict[str, str]]:
    """Parse optional extra worker bind mounts.

    Format: ``host_path:container_path[:ro|rw]`` entries separated by commas.
    Example: ``/srv/matcreator/share:/share:ro``.
    """
    mounts: dict[str, dict[str, str]] = {}
    for item in _WORKER_SHARED_MOUNTS.split(","):
        raw = item.strip()
        if not raw:
            continue
        parts = raw.rsplit(":", 2)
        if len(parts) == 2:
            host_path, container_path = parts
            mode = "ro"
        elif len(parts) == 3 and parts[2] in {"ro", "rw"}:
            host_path, container_path, mode = parts
        else:
            raise RuntimeError(
                "Invalid MATCREATOR_WORKER_SHARED_MOUNTS entry. "
                "Use host_path:container_path[:ro|rw]."
            )
        if not host_path or not container_path.startswith("/"):
            raise RuntimeError(
                "Invalid MATCREATOR_WORKER_SHARED_MOUNTS entry. "
                "Host path is required and container path must be absolute."
            )
        mounts[str(Path(host_path).expanduser())] = {"bind": container_path, "mode": mode}
    return mounts


def _iter_session_db_paths(user_id: str | None = None):
    if _MATCREATOR_MODE != "server":
        if SESSION_DB_PATH.exists():
            yield user_id or "", SESSION_DB_PATH
        return

    if user_id:
        db_path = _user_adk_dir(user_id) / "session.db"
        if db_path.exists():
            yield user_id, db_path
        return

    if not _USERS_DATA_ROOT.exists():
        return
    for db_path in sorted(_USERS_DATA_ROOT.glob("*/.matcreator/.adk/session.db")):
        yield db_path.parents[2].name, db_path


def _load_session_state(session_id: str, user_id: str | None = None) -> tuple[str | None, dict]:
    for owner_id, db_path in _iter_session_db_paths(user_id):
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT state FROM sessions WHERE app_name = ? AND id = ?",
                    (APP_NAME, session_id),
                ).fetchone()
        except sqlite3.Error:
            continue
        if row:
            return owner_id, _load_json_field(row[0], {})
    return None, {}


def _worker_container_name(user_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", user_id)[:64]
    return f"matcreator-worker-{safe}"


def _worker_image_id(dc) -> str:
    try:
        return dc.images.get(_WORKER_IMAGE).id or ""
    except Exception as exc:
        logger.warning("Could not resolve worker image %s: %s", _WORKER_IMAGE, exc)
        return ""


def _container_image_id(container) -> str:
    image = getattr(container, "image", None)
    image_id = getattr(image, "id", "") or ""
    if image_id:
        return image_id
    attrs = getattr(container, "attrs", {}) or {}
    return str(attrs.get("Image") or "")


def _worker_container_uses_current_image(dc, container) -> bool:
    current_image_id = _worker_image_id(dc)
    container_image_id = _container_image_id(container)
    return bool(current_image_id and container_image_id and current_image_id == container_image_id)


def _worker_env_vars() -> dict[str, str]:
    """Default environment to forward into each worker container.

    In server mode this comes from the control-plane runtime first, then the
    persistent control-plane config.yaml. Each worker may still override these
    defaults from its mounted user config.yaml during agent startup.
    """
    keys = [
        "LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "EMBEDDING_MODEL",
        "GRAPH_AGENT_MODEL", "REVIEW_AGENT_MODEL",
        "BOHRIUM_USERNAME", "BOHRIUM_PASSWORD", "BOHRIUM_ACCESS_KEY", "BOHRIUM_API_URL", "BOHRIUM_PROJECT_ID",
        "BOHRIUM_VASP_IMAGE", "BOHRIUM_VASP_MACHINE",
        "BOHRIUM_DEEPMD_IMAGE", "BOHRIUM_DEEPMD_MACHINE", "DEEPMD_MODEL_PATH",
        "KDG_EMBED_MODEL", "HF_HUB_OFFLINE", "MATCREATOR_MODULE_SKILLS_ROOT",
    ]
    env_vars = {k: v for k in keys if (v := _runtime_env_value(k))}
    for key, value in _custom_env_from_config(load_config()).items():
        if not value or not _USER_ENV_KEY_RE.fullmatch(key) or key in _PROTECTED_USER_ENV_KEYS:
            continue
        env_vars[key] = value
    return env_vars


# The supervisor is server-only in practice: local routes never invoke these
# operations, preserving the direct local ADK workflow.
_worker_supervisor = WorkerSupervisor(
    image=_WORKER_IMAGE,
    network=_WORKER_NETWORK,
    connect_mode=_WORKER_CONNECT_MODE,
    base_port=_WORKER_BASE_PORT,
    adk_port=get_adk_port,
    user_home=lambda user_id, host: _user_matcreator_home(user_id, host=host),
    worker_environment=_worker_env_vars,
    shared_mounts=_worker_shared_mounts,
    memory_limit=_WORKER_MEM_LIMIT,
    cpus=_WORKER_CPUS,
    pids_limit=_WORKER_PIDS_LIMIT,
)


def ensure_worker_running(user_id: str) -> str:
    """Ensure the server-mode worker for *user_id* is running."""
    _safe_user_dir_name(user_id)
    return _worker_supervisor.ensure_running(user_id)


def stop_worker(user_id: str) -> None:
    """Stop (but retain) the server-mode worker for *user_id*."""
    _worker_supervisor.stop(user_id)


def remove_worker(user_id: str) -> None:
    """Stop and remove the server-mode worker for *user_id*."""
    _worker_supervisor.remove(user_id)


def _list_workers() -> list[dict]:
    return _worker_supervisor.list_workers()


async def _idle_worker_reaper() -> None:
    """Stop workers that have been idle longer than the configured timeout."""
    while True:
        await asyncio.sleep(min(max(_WORKER_IDLE_TIMEOUT_SECONDS // 2, 60), 600))
        for user_id in _worker_supervisor.idle_users(_WORKER_IDLE_TIMEOUT_SECONDS):
            await asyncio.to_thread(stop_worker, user_id)


def _extract_user_id_from_adk_path(path: str) -> str:
    match = re.search(r"(?:^|/)users/([^/]+)(?:/|$)", path)
    return unquote(match.group(1)) if match else ""


async def _adk_target_url(request: Request, adk_path: str = "") -> str:
    """Return the ADK base URL to proxy to, starting a worker if needed."""
    if _MATCREATOR_MODE != "server":
        return f"http://127.0.0.1:{get_adk_port()}"

    # Determine user_id from query string, ADK URL path, or request body.
    user_id = request.query_params.get("user_id", "") or _extract_user_id_from_adk_path(adk_path)
    if not user_id:
        try:
            body = await request.body()
            if body:
                payload = json.loads(body)
                user_id = payload.get("user_id", "")
        except Exception:
            pass

    if not user_id:
        raise HTTPException(status_code=400,
                            detail="user_id required to route to worker in server mode")

    return await asyncio.to_thread(ensure_worker_running, user_id)



def _is_port_open(host: str = "127.0.0.1", port: int | None = None) -> bool:
    if port is None:
        port = get_adk_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _entry_value(value):
    return value.value if hasattr(value, "value") else value


def _entry_preview(content: str | None, *, limit: int = 280) -> str:
    preview = " ".join((content or "").split())
    if len(preview) <= limit:
        return preview
    return preview[: limit - 3].rstrip() + "..."


def _json_ready(value):
    if hasattr(value, "model_dump"):
        return _json_ready(value.model_dump())
    if hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _load_skill_graph_payload(*, limit: int = 400) -> dict:
    graph = _get_kg()
    disabled_skills = set(get_disabled_skills())
    default_skill_names = get_default_skill_names()
    skill_dirs = _skill_dir_map()
    workspace_skill_root = workspace_skills_dir().resolve()
    nodes = []
    included_ids: set[str] = set()
    offset = 0
    page_size = 200

    while len(nodes) < limit:
        page = graph.list(limit=page_size, offset=offset)
        if not page:
            break
        for entry in page:
            entry_type = _entry_value(entry.entry_type)
            if entry_type == "memory":
                continue
            metadata = entry.metadata
            metadata_payload = _json_ready(metadata)
            skill_name = entry.title if "matcreator-skill" in entry.tags else None
            skill_dir = skill_dirs.get(skill_name) if skill_name else None
            virtual = bool(metadata.custom.get("virtual")) or bool(
                skill_name
                and "matcreator-guide" not in entry.tags
                and skill_dir is None
            )
            enabled = (
                skill_name not in disabled_skills and not virtual
                if skill_name
                else not virtual
            )
            skill_path = str(skill_dir.resolve()) if skill_dir else None
            source = get_skill_source(skill_name) if skill_name else None
            removable = bool(
                skill_name
                and skill_dir is not None
                and source
                and source.editable
                and skill_dir.resolve().is_relative_to(workspace_skill_root)
            )
            nodes.append(
                {
                    "id": entry.id,
                    "label": entry.title,
                    "title": entry.title,
                    "slug": entry.slug,
                    "skill_name": skill_name,
                    "skill_path": skill_path,
                    "source": source.name if source else None,
                    "editable": bool(source and source.editable),
                    "managed": bool(source and source.managed),
                    "trusted": bool(source and source.trusted),
                    "is_custom": bool(source and source.name in {"custom", "workspace"}),
                    "removable": removable,
                    "remove_requires_confirmation": bool(skill_name and source and source.managed),
                    "enabled": enabled,
                    "virtual": virtual,
                    "entry_type": entry_type,
                    "content": "" if virtual else entry.content,
                    "content_preview": "" if virtual else _entry_preview(entry.content),
                    "tags": entry.tags,
                    "aliases": entry.aliases,
                    "internal_refs": [] if virtual else entry.internal_refs,
                    "scripts": [] if virtual else _json_ready(entry.scripts),
                    "assets": [] if virtual else _json_ready(entry.assets),
                    "metadata": metadata_payload,
                    "verification_status": _entry_value(metadata.verification_status),
                    "refinement_status": _entry_value(metadata.refinement_status),
                    "usage_count": metadata.usage_count,
                    "source_provenance": metadata.source_provenance,
                    "trust_score": metadata.trust_score,
                }
            )
            included_ids.add(entry.id)
            if len(nodes) >= limit:
                break
        if len(page) < page_size:
            break
        offset += len(page)

    edges = []
    if included_ids and KNOW_DO_GRAPH_DB.exists():
        placeholders = ",".join("?" for _ in included_ids)
        params = [*included_ids, *included_ids]
        try:
            with sqlite3.connect(KNOW_DO_GRAPH_DB) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    SELECT id, source_id, target_id, relation, weight
                    FROM edges
                    WHERE source_id IN ({placeholders})
                      AND target_id IN ({placeholders})
                    """,
                    params,
                ).fetchall()
        except sqlite3.Error:
            rows = []
        edges = [
            {
                "id": row["id"],
                "from": row["source_id"],
                "to": row["target_id"],
                "relation": row["relation"],
                "weight": row["weight"],
            }
            for row in rows
        ]

    stats = graph.stats()
    return {
        "nodes": nodes,
        "edges": edges,
        "limit": limit,
        "truncated": stats.get("nodes", len(nodes)) > len(nodes),
        "total_nodes": stats.get("nodes", len(nodes)),
        "total_edges": stats.get("edges", len(edges)),
    }


# ---------------------------------------------------------------------------
# ADK proxy routes — forward /run_sse, /apps/*, /list-apps to the right worker
# ---------------------------------------------------------------------------

async def _proxy_request(request: Request, target_url: str, path: str) -> Response:
    url = f"{target_url.rstrip('/')}/{path.lstrip('/')}"
    params = dict(request.query_params)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "origin")}
    body = await request.body()
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            params=params,
            headers=headers,
            content=body,
        )
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type"),
    )


async def _proxy_sse(request: Request, target_url: str, path: str):
    url = f"{target_url.rstrip('/')}/{path.lstrip('/')}"
    params = dict(request.query_params)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "origin")}
    body = await request.body()

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                method=request.method,
                url=url,
                params=params,
                headers=headers,
                content=body,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    return StreamingResponse(stream(), media_type="text/event-stream")


class ManagedRunBody(BaseModel):
    app_name: str = APP_NAME
    user_id: str
    session_id: str
    new_message: dict[str, Any]


def _body_to_dict(body: BaseModel) -> dict[str, Any]:
    if hasattr(body, "model_dump"):
        return body.model_dump()
    return body.dict()


async def _target_url_for_user(user_id: str) -> str:
    if _MATCREATOR_MODE != "server":
        return f"http://127.0.0.1:{get_adk_port()}"
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required to route to worker in server mode")
    return await asyncio.to_thread(ensure_worker_running, user_id)


async def _produce_managed_run(run: ManagedRun, payload: dict[str, Any], target_url: str) -> None:
    url = f"{target_url.rstrip('/')}/run_sse"
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            method="POST",
            url=url,
            headers=headers,
            content=json.dumps(payload),
        ) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                raise RuntimeError(f"ADK run_sse failed with HTTP {resp.status_code}: {detail.decode('utf-8', errors='replace')}")
            async for chunk in resp.aiter_bytes():
                if run.status == "cancelling":
                    raise asyncio.CancelledError()
                if chunk:
                    await _run_registry.publish(run, chunk.decode("utf-8", errors="replace"))


async def _start_managed_run(
    *,
    owner_id: str,
    session_id: str,
    payload: dict[str, Any],
) -> ManagedRun:
    target_url = await _target_url_for_user(owner_id)

    async def producer(run: ManagedRun) -> None:
        await _produce_managed_run(run, payload, target_url)

    return await _run_registry.start(owner_id=owner_id, session_id=session_id, producer=producer)


@app.post("/api/runs")
async def start_managed_run(body: ManagedRunBody) -> JSONResponse:
    payload = _body_to_dict(body)
    try:
        run = await _start_managed_run(owner_id=body.user_id, session_id=body.session_id, payload=payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(run.summary())


async def _prepare_evaluation_adk_session(
    *,
    owner_id: str,
    session_id: str,
    workspace: Path,
    flash: bool,
) -> None:
    target_url = await _target_url_for_user(owner_id)
    path = f"/apps/{APP_NAME}/users/{owner_id}/sessions/{session_id}"
    payload = {
        "agent_mode": "flash" if flash else "bench",
        "benchmark_mode": True,
        "custom_workdir": str(workspace),
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(f"{target_url}{path}", json=payload)
        if response.status_code == 409:
            return
        if response.is_success:
            return
        raise RuntimeError(
            f"ADK session creation failed with HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )


def _extract_runtime_result(run: ManagedRun) -> dict[str, Any]:
    answer = ""
    event_count = 0
    for _, payload in run.events:
        for line in payload.splitlines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            event_count += 1
            for part in event.get("content", {}).get("parts", []):
                if part.get("text") and not part.get("thought"):
                    answer = str(part["text"])
    return {"answer": answer, "num_turns": event_count, "num_events": event_count}


class _ManagedAdkEvaluationRuntime:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id

    async def run(self, spec: RuntimeSpec) -> RuntimeOutcome:
        started_at = time.monotonic()
        await _prepare_evaluation_adk_session(
            owner_id=self.owner_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            flash=spec.flash,
        )
        prompt = spec.prompt_path.read_text(encoding="utf-8")
        run = await _start_managed_run(
            owner_id=self.owner_id,
            session_id=spec.session_id,
            payload={
                "app_name": APP_NAME,
                "user_id": self.owner_id,
                "session_id": spec.session_id,
                "new_message": {"role": "user", "parts": [{"text": prompt}]},
            },
        )
        if spec.on_managed_run_started is not None:
            await spec.on_managed_run_started(run.run_id)
        try:
            await asyncio.wait_for(asyncio.shield(run.task), timeout=spec.timeout_seconds)
        except asyncio.TimeoutError:
            await _run_registry.request_cancel(run)
            return RuntimeOutcome(
                exit_code=None,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started_at,
                result=_extract_runtime_result(run),
                error=f"runtime timed out after {spec.timeout_seconds}s",
            )
        result = _extract_runtime_result(run)
        if run.status == "completed":
            return RuntimeOutcome(
                exit_code=0,
                stdout="",
                stderr="",
                duration_seconds=time.monotonic() - started_at,
                result=result,
            )
        return RuntimeOutcome(
            exit_code=1,
            stdout="",
            stderr="",
            duration_seconds=time.monotonic() - started_at,
            result=result,
            error=run.error or f"managed ADK run {run.status}",
        )


@app.get("/api/runs/active")
async def get_active_runs(
    user_id: str = Query(..., description="Current signed-in user"),
    session_id: str = Query(default="", description="Optional session ID to narrow reconnect lookup"),
) -> JSONResponse:
    if session_id:
        run = _run_registry.active_for(user_id, session_id)
        return JSONResponse({"run": run.summary() if run else None})
    return JSONResponse({"runs": [run.summary() for run in _run_registry.active_runs(user_id)]})


@app.get("/api/runs/{run_id}")
async def get_managed_run(run_id: str) -> JSONResponse:
    run = _run_registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return JSONResponse(run.summary())


@app.get("/api/runs/{run_id}/events")
async def stream_managed_run_events(run_id: str, after: int = Query(default=0, ge=0)) -> StreamingResponse:
    run = _run_registry.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def stream():
        async for event in _run_registry.subscribe(run, after=after):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.api_route("/run_sse", methods=["GET", "POST"])
async def proxy_run_sse(request: Request):
    target_url = await _adk_target_url(request, "/run_sse")
    return await _proxy_sse(request, target_url, "/run_sse")


@app.api_route("/apps/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_apps(path: str, request: Request):
    adk_path = f"/apps/{path}"
    target_url = await _adk_target_url(request, adk_path)
    return await _proxy_request(request, target_url, adk_path)


@app.api_route("/list-apps", methods=["GET"])
async def proxy_list_apps(request: Request):
    target_url = await _adk_target_url(request, "/list-apps")
    return await _proxy_request(request, target_url, "/list-apps")


# ---------------------------------------------------------------------------
# Worker management API
# ---------------------------------------------------------------------------

@app.get("/api/workers")
async def list_workers(user_id: str = Query(...)) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return JSONResponse(_list_workers())


@app.post("/api/workers/{worker_user_id}/start")
async def start_worker_api(worker_user_id: str, user_id: str = Query(...)) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    if _MATCREATOR_MODE != "server":
        raise HTTPException(status_code=400, detail="Worker management only available in server mode")
    target = await asyncio.to_thread(ensure_worker_running, worker_user_id)
    return JSONResponse({"user_id": worker_user_id, "target": target, "status": "running"})


@app.post("/api/workers/{worker_user_id}/stop")
async def stop_worker_api(worker_user_id: str, user_id: str = Query(...)) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    if _MATCREATOR_MODE != "server":
        raise HTTPException(status_code=400, detail="Worker management only available in server mode")
    await asyncio.to_thread(stop_worker, worker_user_id)
    return JSONResponse({"user_id": worker_user_id, "status": "stopped"})


@app.delete("/api/workers/{worker_user_id}")
async def remove_worker_api(worker_user_id: str, user_id: str = Query(...)) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    if _MATCREATOR_MODE != "server":
        raise HTTPException(status_code=400, detail="Worker management only available in server mode")
    await asyncio.to_thread(remove_worker, worker_user_id)
    return JSONResponse({"user_id": worker_user_id, "status": "removed"})


def _kill_port(port: int) -> None:
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        for pid_str in result.stdout.strip().splitlines():
            try:
                os.kill(int(pid_str.strip()), signal.SIGTERM)
            except (ProcessLookupError, ValueError):
                pass
    except Exception:
        pass


def _admin_users() -> set[str]:
    raw_value = os.environ.get("MATCREATOR_ADMIN_USERS")
    if raw_value is None:
        return DEFAULT_ADMIN_USERS.copy()

    return {
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    }


def _is_admin(user_id: str) -> bool:
    admin_names = _admin_users()
    if user_id in admin_names:
        return True  # legacy path: user_id is a display name (pre-UUID)
    user = users_db.get_by_id(user_id)
    return user is not None and user["display_name"] in admin_names


def _session_row_to_summary(row: sqlite3.Row, summaries: dict[str, dict] | None = None) -> dict:
    result = {
        "id": row["id"],
        "appName": row["app_name"],
        "userId": row["user_id"],
        "createTime": row["create_time"],
        "lastUpdateTime": row["update_time"],
    }
    if summaries is not None:
        entry = summaries.get(row["id"], "")
        if isinstance(entry, dict):
            result["summary"] = entry.get("summary", "")
        else:
            result["summary"] = entry
    return result


def _query_session_summaries(user_id: str | None = None) -> list[dict]:
    if _MATCREATOR_MODE == "server":
        return _query_session_summaries_server(user_id)

    if not SESSION_DB_PATH.exists():
        return []

    where_clause = "WHERE app_name = ?"
    params: tuple[str, ...] = (APP_NAME,)

    try:
        with sqlite3.connect(SESSION_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"""
                SELECT app_name, user_id, id, create_time, update_time
                FROM sessions
                {where_clause}
                ORDER BY update_time DESC
                """,
                params,
            ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read sessions: {exc}")

    summaries = _load_summaries()
    return [_session_row_to_summary(row, summaries) for row in rows]


# ---------------------------------------------------------------------------
# Session summaries (experimental)
# ---------------------------------------------------------------------------

import hashlib
import tempfile


def _summary_path_for_user(user_id: str | None = None) -> Path:
    if _MATCREATOR_MODE == "server" and user_id:
        return _user_adk_dir(user_id) / "session_summaries.json"
    return SUMMARIES_PATH


def _load_summaries(user_id: str | None = None) -> dict[str, dict]:
    """Load session summaries from the JSON file.

    Returns dict of {session_id: {"summary": str, "content_hash": str}}.
    For backward compatibility, plain string values are also accepted.
    """
    summaries_path = _summary_path_for_user(user_id)
    if not summaries_path.exists():
        return {}
    try:
        data = json.loads(summaries_path.read_text(encoding="utf-8"))
        # Normalize: support both old format (str) and new format (dict)
        result = {}
        for k, v in data.items():
            if isinstance(v, str):
                result[k] = {"summary": v, "content_hash": ""}
            else:
                result[k] = v
        return result
    except (json.JSONDecodeError, OSError):
        return {}


def _save_summaries(data: dict[str, dict], user_id: str | None = None) -> None:
    """Persist session summaries atomically via temp file + rename."""
    summaries_path = _summary_path_for_user(user_id)
    summaries_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(summaries_path.parent), suffix=".tmp", prefix="summaries_"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, str(summaries_path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _get_session_summary(session_id: str, user_id: str | None = None) -> str:
    """Get summary text for a single session, or empty string."""
    entry = _load_summaries(user_id).get(session_id)
    if isinstance(entry, dict):
        return entry.get("summary", "")
    return ""


def _fetch_first_user_message(session_id: str, user_id: str | None = None) -> str:
    """Read the first user message text from the session DB."""
    session_db_path = next((db for _, db in _iter_session_db_paths(user_id)), None)
    if not session_db_path or not session_db_path.exists():
        return ""
    try:
        with sqlite3.connect(session_db_path) as conn:
            conn.row_factory = sqlite3.Row
            if _MATCREATOR_MODE == "server" and user_id:
                rows = conn.execute(
                    """
                    SELECT event_data FROM events
                    WHERE app_name = ? AND user_id = ? AND session_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (APP_NAME, user_id, session_id),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT event_data FROM events
                    WHERE app_name = ? AND session_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (APP_NAME, session_id),
                ).fetchall()
    except sqlite3.Error:
        return ""

    for row in rows:
        event = _load_json_field(row["event_data"], {})
        if event.get("author") != "user":
            continue
        parts = event.get("content", {}).get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text"))
        if text.strip():
            return text.strip()
    return ""


_SUMMARIZE_PROMPT = (
    "请用一句简洁的中文（不超过30个字）总结以下对话的核心内容。"
    "只输出总结本身，不要任何前缀、解释或标点符号。不要使用句号、逗号、感叹号等任何标点。\n\n"
    "对话内容：\n{text}"
)

_SUMMARIZE_PROMPT_EN = (
    "Summarize the following conversation in a concise English phrase (no more than 6 words). "
    "Output only the summary itself, no prefixes, no explanations, no punctuation whatsoever "
    "(no periods, commas, exclamation marks, colons, semicolons, or any other punctuation).\n\n"
    "Conversation:\n{text}"
)


def _is_primarily_english(text: str) -> bool:
    """Return True if the majority of non-whitespace characters are ASCII letters."""
    alpha = [c for c in text.strip()[:500] if c.isalpha()]
    if not alpha:
        return False
    return sum(1 for c in alpha if c.isascii()) / len(alpha) > 0.5


def _llm_config() -> tuple[str, str | None, str | None]:
    """Resolve LLM config from env vars/config.yaml.

    Returns None for api_key/base_url when not explicitly set, so litellm
    can use its built-in provider detection (e.g. MINIMAX_API_KEY for minimax/).
    """
    model = _runtime_env_value("LLM_MODEL")
    api_key = _runtime_env_value("LLM_API_KEY") or None
    base_url = _runtime_env_value("LLM_BASE_URL") or None
    return model, api_key, base_url


@app.post("/api/sessions/{session_id}/summarize")
async def summarize_session(
    session_id: str,
    user_id: str = Query(default="", description="Current user ID; required to scope server-mode sessions."),
) -> JSONResponse:
    """Generate a one-sentence summary from the session's first user message."""
    # Fetch canonical first message from DB
    scoped_user_id = user_id or None
    first_msg = _fetch_first_user_message(session_id, scoped_user_id)
    if not first_msg:
        return JSONResponse({"summary": ""})

    content_hash = hashlib.md5(first_msg.encode()).hexdigest()[:12]

    # Return cached summary if content hash matches
    summaries = _load_summaries(scoped_user_id)
    cached = summaries.get(session_id)
    if cached and isinstance(cached, dict) and cached.get("content_hash") == content_hash:
        return JSONResponse({"summary": cached["summary"]})

    # Call LLM to generate summary
    summary = ""
    try:
        from litellm import acompletion

        model, api_key, base_url = _llm_config()
        prompt_template = _SUMMARIZE_PROMPT_EN if _is_primarily_english(first_msg) else _SUMMARIZE_PROMPT
        prompt = prompt_template.format(text=first_msg[:2000])

        response = await acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            api_key=api_key or None,
            base_url=base_url or None,
            temperature=0.3,
            max_tokens=100,
        )
        raw = response.choices[0].message.content or ""
        import unicodedata
        summary = ''.join(c for c in raw if unicodedata.category(c)[0] != 'P').strip()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Session summary LLM call failed: %s", exc)
        # Fallback: use truncated first message
        summary = first_msg[:40] + ("…" if len(first_msg) > 40 else "")

    # Persist with content hash (skip caching empty results so it retries next time)
    if summary:
        summaries[session_id] = {"summary": summary, "content_hash": content_hash}
        _save_summaries(summaries, scoped_user_id)

    return JSONResponse({"summary": summary})


class UpdateSummaryBody(BaseModel):
    summary: str


@app.put("/api/sessions/{session_id}/summary")
async def update_session_summary(
    session_id: str,
    body: UpdateSummaryBody,
    user_id: str = Query(default="", description="Current user ID; required to scope server-mode sessions."),
) -> JSONResponse:
    """Manually set, override, or clear the summary for a session."""
    text = body.summary.strip()
    scoped_user_id = user_id or None

    summaries = _load_summaries(scoped_user_id)
    if not text:
        # Clear summary
        summaries.pop(session_id, None)
        _save_summaries(summaries, scoped_user_id)
        return JSONResponse({"summary": ""})

    existing = summaries.get(session_id, {})
    content_hash = existing.get("content_hash", "") if isinstance(existing, dict) else ""
    summaries[session_id] = {"summary": text, "content_hash": content_hash}
    _save_summaries(summaries, scoped_user_id)
    return JSONResponse({"summary": text})


def _query_session_summaries_server(user_id: str | None = None) -> list[dict]:
    """In server mode, aggregate sessions from all per-user session DBs."""
    results: list[dict] = []
    # Each user's DB is at users/<user_id>/.matcreator/.adk/session.db.
    for owner_id, db_path in _iter_session_db_paths(user_id):
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT app_name, user_id, id, create_time, update_time
                    FROM sessions
                    WHERE app_name = ?
                    ORDER BY update_time DESC
                    """,
                    (APP_NAME,),
                ).fetchall()
            summaries = _load_summaries(owner_id)
            results.extend(_session_row_to_summary(r, summaries) for r in rows)
        except sqlite3.Error:
            continue

    results.sort(key=lambda r: r.get("lastUpdateTime") or "", reverse=True)
    return results


def _load_json_field(raw_value: str | None, fallback):
    if not raw_value:
        return fallback
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        return fallback


def _ase_read_structure(path: Path):
    from ase.io import read as ase_read

    name = path.name.lower()
    if name in {"poscar", "contcar"} or path.suffix.lower() == ".vasp":
        return ase_read(str(path), format="vasp")
    return ase_read(str(path))


def _load_agent_graph_data(session_id: str) -> dict:
    graph_paths: list[Path]
    if _MATCREATOR_MODE == "server":
        graph_paths = [
            _user_adk_dir(owner_id) / "agent_graphs" / f"{session_id}.json"
            for owner_id, _ in _iter_session_db_paths()
        ]
    else:
        graph_paths = [_ADK_DIR / "agent_graphs" / f"{session_id}.json"]

    for graph_path in graph_paths:
        if not graph_path.exists():
            continue
        try:
            return json.loads(graph_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _map_worker_path_to_control_plane(user_id: str, path_str: str) -> Path | None:
    raw = (path_str or "").strip()
    if not raw:
        return None
    if raw.startswith("~/"):
        raw = str(_WORKER_MATCREATOR_HOME.parent / raw[2:])
    candidate = Path(raw)
    workspace_root = _user_workspace_root(user_id)
    user_home = _user_matcreator_home(user_id)

    if not candidate.is_absolute():
        return (workspace_root / candidate).resolve()

    try:
        rel = candidate.relative_to(_WORKER_WORKSPACE_ROOT)
        return (workspace_root / rel).resolve()
    except ValueError:
        pass

    try:
        rel = candidate.relative_to(_WORKER_MATCREATOR_HOME)
        return (user_home / rel).resolve()
    except ValueError:
        return candidate.resolve()


def _get_workdir_for_session(session_id: str) -> Path:
    """Resolve workdir for a session, preferring the value stored in session state.

    Priority: state["workdir"] → state["custom_workdir"] → computed default.
    In server mode, worker-container paths are mapped to the user's host-mounted
    .matcreator tree and paths outside that user's workspace are rejected.
    """
    owner_id, state = _load_session_state(session_id)
    path_str = state.get("workdir") or state.get("custom_workdir")
    if path_str:
        if _MATCREATOR_MODE == "server" and owner_id:
            candidate = _map_worker_path_to_control_plane(owner_id, path_str)
            ws_root = _user_workspace_root(owner_id).resolve()
            if candidate and candidate.is_relative_to(ws_root):
                return candidate
        elif _MATCREATOR_MODE != "server":
            return Path(path_str).expanduser().resolve()

    if _MATCREATOR_MODE == "server" and owner_id:
        return _user_workspace_root(owner_id)
    if _MATCREATOR_MODE == "server":
        return _USERS_DATA_ROOT / "_unknown" / ".matcreator" / "workspace"

    # Fall back to config.yaml default_workdir before using WORKSPACE_ROOT
    cfg_workdir = (load_config().get("workspace") or {}).get("default_workdir") or ""
    if cfg_workdir:
        candidate = Path(cfg_workdir).expanduser().resolve()
        if _MATCREATOR_MODE == "server":
            if candidate.is_relative_to(get_workspace_root().resolve()):
                return candidate
        else:
            return candidate
    return get_session_workdir(session_id)


def _safe_upload_filename(filename: str) -> str:
    cleaned = Path(filename or "upload").name.strip()
    if not cleaned or cleaned in {".", ".."}:
        cleaned = "upload"
    return "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in cleaned)


def _available_upload_path(upload_dir: Path, filename: str) -> Path:
    candidate = upload_dir / filename
    if not candidate.exists():
        return candidate

    stem = candidate.stem or "upload"
    suffix = candidate.suffix
    for i in range(1, 10000):
        next_candidate = upload_dir / f"{stem}-{i}{suffix}"
        if not next_candidate.exists():
            return next_candidate

    raise HTTPException(status_code=409, detail="Too many files with the same name")


def _control_plane_path_to_worker(user_id: str, path: Path) -> str:
    if _MATCREATOR_MODE != "server":
        return str(path)
    user_home = _user_matcreator_home(user_id).resolve()
    try:
        rel = path.resolve().relative_to(user_home)
    except ValueError:
        return str(path)
    return str(_WORKER_MATCREATOR_HOME / rel)


def _resolve_readable_file_path(path: str, session_id: str = "") -> Path:
    if _MATCREATOR_MODE == "server":
        owner_id = None
        if session_id:
            owner_id, _ = _load_session_state(session_id)
        if owner_id:
            resolved = _map_worker_path_to_control_plane(owner_id, path)
            if resolved is None:
                raise HTTPException(status_code=404, detail="File not found")
            allowed_roots = [
                _get_workdir_for_session(session_id).resolve(),
                _user_workspace_root(owner_id).resolve(),
            ]
        else:
            resolved = Path(path).expanduser().resolve()
            allowed_roots = [_USERS_DATA_ROOT.resolve()]
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            raise HTTPException(status_code=403, detail="Access denied: path is outside workspace")
        return resolved

    ws_root = get_workspace_root().resolve()
    p = Path(path)
    resolved = p.resolve() if p.is_absolute() else (ws_root / p).resolve()
    allowed = ws_root
    if session_id:
        allowed = _get_workdir_for_session(session_id).resolve()
    if not resolved.is_relative_to(allowed) and not resolved.is_relative_to(ws_root):
        raise HTTPException(status_code=403, detail="Access denied: path is outside workspace")
    return resolved


def _normalize_cli_cwd(cwd: str) -> Path:
    raw = (cwd or ".").strip()
    if raw in {"", "~"}:
        raw = "."
    if raw.startswith("~/"):
        raw = raw[2:]
    path = Path(raw)
    if path.is_absolute():
        raise HTTPException(status_code=403, detail="CLI cwd must be workspace-relative")

    root = Path("/")
    normalized = (root / path).resolve().relative_to(root)
    return normalized


def _cli_script(command: str) -> str:
    return (
        "exec 2>&1\n"
        "trap 's=$?; printf \"\\n__MATCREATOR_CWD__%s\\n__MATCREATOR_STATUS__%s\\n\" \"$PWD\" \"$s\"' EXIT\n"
        f"{command}\n"
    )


def _parse_cli_output(raw_output: str, workspace_root: Path | str) -> dict:
    cwd_marker = "\n__MATCREATOR_CWD__"
    status_marker = "\n__MATCREATOR_STATUS__"
    output = raw_output
    exit_code = 0
    next_cwd = "."

    cwd_idx = raw_output.rfind(cwd_marker)
    status_idx = raw_output.rfind(status_marker)
    if cwd_idx >= 0 and status_idx > cwd_idx:
        output = raw_output[:cwd_idx]
        cwd_abs = raw_output[cwd_idx + len(cwd_marker):status_idx].strip()
        status_raw = raw_output[status_idx + len(status_marker):].strip().splitlines()[0:1]
        if status_raw:
            try:
                exit_code = int(status_raw[0])
            except ValueError:
                exit_code = 1
        try:
            rel = Path(cwd_abs).resolve().relative_to(Path(workspace_root).resolve())
            next_cwd = rel.as_posix() or "."
        except (OSError, ValueError):
            next_cwd = "."

    if len(output) > _WORKSPACE_CLI_OUTPUT_LIMIT:
        output = output[:_WORKSPACE_CLI_OUTPUT_LIMIT] + "\n... [truncated]"
    return {"output": output, "exit_code": exit_code, "cwd": next_cwd}


async def _run_local_workspace_cli(command: str, cwd: str) -> dict:
    rel_cwd = _normalize_cli_cwd(cwd)
    workspace = get_workspace_root().resolve()
    run_cwd = (workspace / rel_cwd).resolve()
    if not run_cwd.is_relative_to(workspace):
        raise HTTPException(status_code=403, detail="CLI cwd is outside workspace")
    run_cwd.mkdir(parents=True, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-lc",
        _cli_script(command),
        cwd=str(run_cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=_WORKSPACE_CLI_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace")
        return {
            "output": output + f"\n[Timeout after {_WORKSPACE_CLI_TIMEOUT_SECONDS}s]",
            "exit_code": 124,
            "cwd": rel_cwd.as_posix() or ".",
        }
    return _parse_cli_output(stdout.decode("utf-8", errors="replace"), workspace)


def _run_worker_workspace_cli(user_id: str, command: str, cwd: str) -> dict:
    _safe_user_dir_name(user_id)
    rel_cwd = _normalize_cli_cwd(cwd)
    ensure_worker_running(user_id)

    dc = _worker_supervisor.docker_client()
    container = dc.containers.get(_worker_container_name(user_id))
    workdir = (_WORKER_WORKSPACE_ROOT / rel_cwd).as_posix()
    container.exec_run(["mkdir", "-p", workdir])

    exec_result = container.exec_run(
        [
            "timeout",
            str(_WORKSPACE_CLI_TIMEOUT_SECONDS),
            "bash",
            "-lc",
            _cli_script(command),
        ],
        workdir=workdir,
        stdout=True,
        stderr=True,
        demux=False,
    )
    raw = exec_result.output.decode("utf-8", errors="replace")
    if exec_result.exit_code == 124:
        return {
            "output": raw + f"\n[Timeout after {_WORKSPACE_CLI_TIMEOUT_SECONDS}s]",
            "exit_code": 124,
            "cwd": rel_cwd.as_posix() or ".",
        }
    return _parse_cli_output(raw, _WORKER_WORKSPACE_ROOT)


def _complete_workspace_paths(workspace_root: Path, cwd: str, token: str) -> dict:
    workspace = workspace_root.resolve()
    rel_cwd = _normalize_cli_cwd(cwd)
    raw_token = (token or "").strip()
    if raw_token.startswith("~/"):
        raw_token = raw_token[2:]
    if Path(raw_token).is_absolute():
        raise HTTPException(status_code=403, detail="Completion path must be workspace-relative")

    if "/" in raw_token:
        parent_raw, prefix = raw_token.rsplit("/", 1)
        parent_rel = _normalize_cli_cwd(parent_raw or ".")
        replacement_prefix = "" if parent_raw in {"", "."} else f"{parent_raw}/"
    else:
        parent_rel = rel_cwd
        prefix = raw_token
        replacement_prefix = ""

    base_dir = (workspace / parent_rel).resolve()
    if not base_dir.is_relative_to(workspace):
        raise HTTPException(status_code=403, detail="Completion path is outside workspace")
    if not base_dir.is_dir():
        return {"matches": []}

    matches = []
    try:
        entries = sorted(base_dir.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to list workspace path: {exc}")

    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        suffix = "/" if entry.is_dir() else ""
        matches.append({
            "name": entry.name + suffix,
            "replacement": replacement_prefix + entry.name + suffix,
            "type": "dir" if entry.is_dir() else "file",
        })

    return {"matches": matches[:200]}


def _set_pty_size(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows or 24), 200))
    cols = max(1, min(int(cols or 80), 400))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _send_terminal_output(websocket: WebSocket, data: bytes) -> None:
    await websocket.send_text(json.dumps({
        "type": "output",
        "data": data.decode("utf-8", errors="replace"),
    }))


async def _local_terminal_session(websocket: WebSocket) -> None:
    workspace = get_workspace_root().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    master_fd, slave_fd = pty.openpty()
    _set_pty_size(master_fd, 24, 80)
    env = {
        **os.environ,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "MATCLAW_WORKSPACE": str(workspace),
        "MATCLAW_SESSION_DIR": str(workspace),
    }
    proc = subprocess.Popen(
        ["bash", "-l"],
        cwd=str(workspace),
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        start_new_session=True,
        env=env,
    )
    os.close(slave_fd)
    loop = asyncio.get_running_loop()

    async def read_loop() -> None:
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            try:
                await _send_terminal_output(websocket, data)
            except Exception:
                break

    reader = asyncio.create_task(read_loop())
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            msg_type = message.get("type")
            if msg_type == "input":
                os.write(master_fd, str(message.get("data", "")).encode())
            elif msg_type == "resize":
                _set_pty_size(master_fd, int(message.get("rows", 24)), int(message.get("cols", 80)))
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        try:
            os.close(master_fd)
        except OSError:
            pass
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def _docker_exec_socket(user_id: str):
    ensure_worker_running(user_id)
    dc = _worker_supervisor.docker_client()
    container = dc.containers.get(_worker_container_name(user_id))
    workspace = _WORKER_WORKSPACE_ROOT.as_posix()
    container.exec_run(["mkdir", "-p", workspace])
    exec_id = dc.api.exec_create(
        container.id,
        ["bash", "-l"],
        stdin=True,
        stdout=True,
        stderr=True,
        tty=True,
        workdir=workspace,
        environment={
            "TERM": "xterm-256color",
            "COLORTERM": "truecolor",
            "MATCLAW_WORKSPACE": workspace,
            "MATCLAW_SESSION_DIR": workspace,
        },
    )["Id"]
    sock = dc.api.exec_start(exec_id, tty=True, socket=True)
    raw_sock = getattr(sock, "_sock", sock)
    raw_sock.settimeout(None)
    return dc, exec_id, sock, raw_sock


async def _worker_terminal_session(websocket: WebSocket, user_id: str) -> None:
    _safe_user_dir_name(user_id)
    dc, exec_id, sock, raw_sock = await asyncio.to_thread(_docker_exec_socket, user_id)
    loop = asyncio.get_running_loop()

    async def read_loop() -> None:
        while True:
            try:
                data = await loop.run_in_executor(None, raw_sock.recv, 4096)
            except OSError:
                break
            if not data:
                break
            try:
                await _send_terminal_output(websocket, data)
            except Exception:
                break

    reader = asyncio.create_task(read_loop())
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            msg_type = message.get("type")
            if msg_type == "input":
                await loop.run_in_executor(None, raw_sock.send, str(message.get("data", "")).encode())
            elif msg_type == "resize":
                rows = max(1, min(int(message.get("rows", 24)), 200))
                cols = max(1, min(int(message.get("cols", 80)), 400))
                await asyncio.to_thread(dc.api.exec_resize, exec_id, height=rows, width=cols)
    except WebSocketDisconnect:
        pass
    finally:
        reader.cancel()
        try:
            sock.close()
        except Exception:
            pass


@app.get("/api/health")
async def health_check():
    mode = os.environ.get("MATCREATOR_MODE", "local")
    return {"status": "ok", "mode": mode}


@app.get("/api/evaluations/catalog")
async def list_evaluation_catalog(
    q: str = Query(default=""),
    capability: str = Query(default=""),
    task_type: str = Query(default=""),
    domain: str = Query(default=""),
    tags: list[str] = Query(default=[]),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
    user_id: str = Query(default=""),
) -> JSONResponse:
    catalog = await (await _benchmark_client_for_owner(user_id)).list_questions(
        q=q,
        capability=capability,
        task_type=task_type,
        domain=domain,
        tags=tags,
        offset=offset,
        limit=limit,
    )
    return JSONResponse(
        {
            "questions": catalog["questions"],
            "total": catalog["total"],
            "offset": catalog["offset"] if catalog["offset"] is not None else offset,
            "limit": catalog["limit"] if catalog["limit"] is not None else limit,
            "facets": catalog["facets"],
        }
    )


@app.get("/api/evaluations/campaigns")
async def list_evaluation_campaigns(user_id: str = Query(...)) -> JSONResponse:
    return JSONResponse({"campaigns": _evaluation_store_for_owner(user_id).list_campaigns(owner_id=user_id)})


@app.get("/api/evaluations/question-sets")
async def list_evaluation_question_sets(user_id: str = Query(...)) -> JSONResponse:
    return JSONResponse({"question_sets": _evaluation_store_for_owner(user_id).list_question_sets(viewer_id=user_id)})


@app.post("/api/evaluations/question-sets")
async def create_evaluation_question_set(
    body: EvaluationQuestionSetBody = Body(...), user_id: str = Query(...)
) -> JSONResponse:
    try:
        question_set = _evaluation_store_for_owner(user_id).create_question_set(
            owner_id=user_id, name=body.name, question_ids=body.question_ids, visibility=body.visibility
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(question_set, status_code=201)


@app.patch("/api/evaluations/question-sets/{set_id}")
async def update_evaluation_question_set(
    set_id: str, body: EvaluationQuestionSetBody = Body(...), user_id: str = Query(...)
) -> JSONResponse:
    try:
        question_set = _evaluation_store_for_owner(user_id).update_question_set(
            set_id=set_id, owner_id=user_id, name=body.name, question_ids=body.question_ids, visibility=body.visibility
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(question_set)


@app.delete("/api/evaluations/question-sets/{set_id}")
async def delete_evaluation_question_set(set_id: str, user_id: str = Query(...)) -> Response:
    try:
        _evaluation_store_for_owner(user_id).delete_question_set(set_id=set_id, owner_id=user_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@app.post("/api/evaluations/campaigns")
async def create_evaluation_campaign(
    body: EvaluationCampaignBody = Body(...),
    user_id: str = Query(...),
) -> JSONResponse:
    service = EvaluationService(
        _evaluation_store_for_owner(user_id),
        _evaluation_workspace_for_owner(user_id),
    )
    try:
        campaign = service.create_campaign(
            owner_id=user_id,
            model_name=body.model_name,
            question_ids=body.question_ids,
            max_parallelism=body.max_parallelism,
            max_turns=body.max_turns,
            timeout_seconds=body.timeout_seconds,
            flash=body.flash,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(campaign, status_code=201)


@app.get("/api/evaluations/campaigns/{campaign_id}")
async def get_evaluation_campaign(campaign_id: str, user_id: str = Query(...)) -> JSONResponse:
    store = _evaluation_store_for_owner(user_id)
    campaign = store.get_campaign(campaign_id, owner_id=user_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Evaluation campaign not found")
    return JSONResponse({**campaign, "attempts": store.list_attempts(campaign_id)})


@app.get("/api/evaluations/campaigns/{campaign_id}/attempts/{attempt_id}/events")
async def get_evaluation_attempt_events(
    campaign_id: str,
    attempt_id: str,
    user_id: str = Query(...),
    after: int = Query(default=0, ge=0),
) -> JSONResponse:
    store = _evaluation_store_for_owner(user_id)
    campaign = store.get_campaign(campaign_id, owner_id=user_id)
    attempt = store.get_attempt(attempt_id)
    if campaign is None or attempt is None or attempt["campaign_id"] != campaign_id:
        raise HTTPException(status_code=404, detail="Evaluation attempt not found")
    workspace = Path(attempt["workspace_path"] or "").resolve()
    runtime_dir = workspace.parent / ".runtime" / attempt["runtime_session_id"]
    event_log = runtime_dir / "events.jsonl"
    events: list[dict[str, Any]] = []
    if event_log.is_file():
        for sequence, line in enumerate(event_log.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if sequence <= after:
                continue
            try:
                events.append({"sequence": sequence, "event": json.loads(line)})
            except json.JSONDecodeError:
                continue
    return JSONResponse({"events": events[-200:], "latest_sequence": after + len(events)})


@app.post("/api/evaluations/campaigns/{campaign_id}/start")
async def start_evaluation_campaign(campaign_id: str, user_id: str = Query(...)) -> JSONResponse:
    store = _evaluation_store_for_owner(user_id)
    if store.get_campaign(campaign_id, owner_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Evaluation campaign not found")
    service = EvaluationService(
        store,
        _evaluation_workspace_for_owner(user_id),
        launcher=_ManagedAdkEvaluationRuntime(user_id),
    )
    client = await _benchmark_client_for_owner(user_id)
    try:
        campaign = await service.start_campaign(campaign_id, client)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await _evaluation_manager.start(campaign_id, service, client)
    return JSONResponse(campaign)


@app.post("/api/evaluations/campaigns/{campaign_id}/cancel")
async def cancel_evaluation_campaign(campaign_id: str, user_id: str = Query(...)) -> JSONResponse:
    store = _evaluation_store_for_owner(user_id)
    campaign = store.get_campaign(campaign_id, owner_id=user_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Evaluation campaign not found")
    service = EvaluationService(
        store,
        _evaluation_workspace_for_owner(user_id),
        launcher=_ManagedAdkEvaluationRuntime(user_id),
    )

    async def cancel_managed_run(run_id: str) -> None:
        run = _run_registry.get(run_id)
        if run is not None:
            await _run_registry.request_cancel(run)

    try:
        campaign = await _evaluation_manager.cancel_campaign(
            campaign_id,
            service,
            cancel_managed_run=cancel_managed_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse({**campaign, "attempts": store.list_attempts(campaign_id)})


@app.get("/api/skill-graph/data")
async def get_skill_graph_data(
    limit: int = Query(default=400, ge=1, le=1200),
) -> JSONResponse:
    try:
        payload = await asyncio.to_thread(_load_skill_graph_payload, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return JSONResponse(payload)


@app.on_event("startup")
async def _on_startup() -> None:
    global _remote_job_monitor_task
    users_db.init_db()
    if _MATCREATOR_MODE != "server":
        users_db.migrate_legacy_adk_sessions(SESSION_DB_PATH, APP_NAME)
    # Reconcile repository-managed skills before the graph UI can read stale
    # nodes or edges.  Agent initialization also seeds the graph, but it may
    # happen later or in a different worker process than the web frontend.
    try:
        await asyncio.to_thread(refresh_skills)
    except Exception:
        logger.exception("Failed to reconcile skill graph during web startup")
    if _MATCREATOR_MODE == "server" and _WORKER_IDLE_TIMEOUT_SECONDS > 0:
        asyncio.create_task(_idle_worker_reaper())
    _remote_job_monitor_task = asyncio.create_task(_run_remote_job_monitor())
    if _MATCREATOR_MODE == "local":
        try:
            client = _benchmark_client()
        except HTTPException:
            logger.warning("Skipping evaluation recovery because benchmark service is not configured")
        else:
            for campaign in _evaluation_store.list_active_campaigns():
                service = EvaluationService(
                    _evaluation_store,
                    _evaluation_workspace_for_owner(campaign["owner_id"]),
                    launcher=_ManagedAdkEvaluationRuntime(campaign["owner_id"]),
                )
                await _evaluation_manager.start(campaign["campaign_id"], service, client)
            for campaign in _evaluation_store.list_campaigns(owner_id="user"):
                service = EvaluationService(
                    _evaluation_store,
                    _evaluation_workspace_for_owner(campaign["owner_id"]),
                    launcher=_ManagedAdkEvaluationRuntime(campaign["owner_id"]),
                )
                if campaign["status"] == "failed":
                    for attempt in _evaluation_store.list_attempts(campaign["campaign_id"]):
                        if attempt["status"] in {"runtime_starting", "running", "submitting"}:
                            _evaluation_store.transition_attempt(
                                attempt["attempt_id"],
                                "interrupted",
                                error="Local evaluation runtime was interrupted before benchmark submission.",
                            )
                await _evaluation_manager.recover_missing_result_campaign(
                    campaign["campaign_id"], service, client
                )


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    _remote_job_monitor.stop()
    _remote_job_monitor_stop.set()
    if _remote_job_monitor_task is not None:
        await _remote_job_monitor_task
    await _run_registry.shutdown()


class LoginBody(BaseModel):
    display_name: str
    password: str | None = None


class RegisterBody(BaseModel):
    display_name: str
    password: str


class SetPasswordBody(BaseModel):
    user_id: str
    old_password: str | None = None
    new_password: str


class LogoutBody(BaseModel):
    user_id: str


class WorkspaceCliBody(BaseModel):
    command: str
    cwd: str = "."
    user_id: str | None = None


class WorkspaceCompleteBody(BaseModel):
    token: str = ""
    cwd: str = "."
    user_id: str | None = None


class KnowledgeReviewBody(BaseModel):
    session_id: str


def _knowledge_review_snapshot() -> dict:
    with _knowledge_review_lock:
        return dict(_knowledge_review_state)


def _set_knowledge_review_state(**changes) -> None:
    with _knowledge_review_lock:
        _knowledge_review_state.update(changes)


def _review_model_config() -> tuple[str, str, str | None]:
    model = (
        _runtime_env_value("REVIEW_AGENT_MODEL")
        or _runtime_env_value("GRAPH_AGENT_MODEL")
        or _runtime_env_value("LLM_MODEL")
        or GRAPH_AGENT_MODEL
    )
    api_key = _runtime_env_value("LLM_API_KEY")
    base_url = _runtime_env_value("LLM_BASE_URL") or None
    if "/" in model:
        model = model.split("/", 1)[1]
    return model, api_key, base_url


async def _run_knowledge_review(session_id: str) -> None:
    try:
        model, api_key, base_url = _review_model_config()
        if not api_key:
            raise RuntimeError(
                "No review API key configured. Set LLM_API_KEY in Settings "
                "(stored in ~/.matcreator/config.yaml in local mode)."
            )
        if not model:
            raise RuntimeError(
                "No REVIEW_AGENT_MODEL, GRAPH_AGENT_MODEL, or LLM_MODEL configured."
            )

        def run_review() -> dict:
            graph = _get_kg()
            result = run_review_pipeline(
                graph,
                model=model,
                api_key=api_key,
                base_url=base_url,
                batch_size=20,
                strategy=os.environ.get("MATCREATOR_REVIEW_STRATEGY", "auto"),
                on_status=lambda phase, status: _set_knowledge_review_state(
                    **status,
                    phase=phase,
                    trigger_session_id=session_id,
                ),
            )
            return result

        result = await asyncio.to_thread(run_review)
        _set_knowledge_review_state(**result, trigger_session_id=session_id)
    except Exception as exc:
        _set_knowledge_review_state(
            status="failed",
            trigger_session_id=session_id,
            progress={"completed": 0, "total": 0, "percent": 0},
            results=[],
            errors=[str(exc)],
            summary="",
        )
    finally:
        global _knowledge_review_task
        _knowledge_review_task = None


@app.post("/api/knowledge-review/start")
async def start_knowledge_review(body: KnowledgeReviewBody) -> JSONResponse:
    global _knowledge_review_task
    if _knowledge_review_task is not None and not _knowledge_review_task.done():
        return JSONResponse(_knowledge_review_snapshot(), status_code=202)

    _set_knowledge_review_state(
        status="running",
        trigger_session_id=body.session_id,
        progress={"completed": 0, "total": 0, "percent": 0},
        results=[],
        errors=[],
        summary="",
    )
    _knowledge_review_task = asyncio.create_task(_run_knowledge_review(body.session_id))
    return JSONResponse(_knowledge_review_snapshot(), status_code=202)


@app.get("/api/knowledge-review/status")
async def get_knowledge_review_status() -> JSONResponse:
    return JSONResponse(_knowledge_review_snapshot())


@app.post("/api/auth/login")
async def auth_login(body: LoginBody) -> JSONResponse:
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="display_name cannot be empty")

    # Special identity: "user" always allowed, no password required.
    if name == users_db.LEGACY_USER:
        if _MATCREATOR_MODE == "server":
            await asyncio.to_thread(ensure_worker_running, users_db.LEGACY_USER)
        return JSONResponse({
            "user_id": users_db.LEGACY_USER,
            "display_name": users_db.LEGACY_USER,
            "is_admin": _is_admin(users_db.LEGACY_USER),
        })

    user = users_db.get_by_display_name(name)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found. Please register first.")
    if user["password_hash"] is not None and not users_db.verify_password(user["password_hash"], body.password):
        raise HTTPException(status_code=401, detail="Invalid password.")

    if _MATCREATOR_MODE == "server":
        await asyncio.to_thread(ensure_worker_running, user["id"])

    return JSONResponse({
        "user_id": user["id"],
        "display_name": user["display_name"],
        "is_admin": _is_admin(user["id"]),
    })


@app.post("/api/auth/register")
async def auth_register(body: RegisterBody) -> JSONResponse:
    name = body.display_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="display_name cannot be empty")
    if name == users_db.LEGACY_USER:
        raise HTTPException(status_code=400, detail="'user' is a reserved username.")
    if not body.password:
        raise HTTPException(status_code=422, detail="Password is required for registration.")

    existing = users_db.get_by_display_name(name)
    if existing is not None:
        raise HTTPException(status_code=409, detail="Username already taken.")

    user = users_db.create_user(name, body.password)
    if _MATCREATOR_MODE == "server":
        await asyncio.to_thread(ensure_worker_running, user["id"])

    return JSONResponse({
        "user_id": user["id"],
        "display_name": user["display_name"],
        "is_admin": _is_admin(user["id"]),
    }, status_code=201)


@app.post("/api/auth/set-password")
async def auth_set_password(body: SetPasswordBody) -> JSONResponse:
    if body.user_id == users_db.LEGACY_USER:
        raise HTTPException(status_code=400, detail="Cannot set password for the 'user' account")
    user = users_db.get_by_id(body.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if not users_db.verify_password(user["password_hash"], body.old_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    users_db.set_password(body.user_id, body.new_password)
    return JSONResponse({"status": "ok"})


@app.post("/api/auth/logout")
async def auth_logout(body: LogoutBody) -> JSONResponse:
    """Log out the current user and stop their server-mode worker."""
    if _MATCREATOR_MODE == "server" and body.user_id:
        await asyncio.to_thread(stop_worker, body.user_id)
    return JSONResponse({"status": "ok"})


@app.get("/api/session-access/{user_id}")
async def get_session_access(user_id: str) -> JSONResponse:
    return JSONResponse({"user_id": user_id, "is_admin": _is_admin(user_id)})


@app.get("/api/users/{user_id}/sessions")
async def list_user_sessions(user_id: str) -> JSONResponse:
    return JSONResponse(_query_session_summaries(user_id))


@app.get("/api/users/{user_id}/sessions/{session_id}")
async def get_user_session(user_id: str, session_id: str) -> JSONResponse:
    session_db_path = next((db for _, db in _iter_session_db_paths(user_id)), None)
    if not session_db_path:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        with sqlite3.connect(session_db_path) as conn:
            conn.row_factory = sqlite3.Row
            if _MATCREATOR_MODE == "server":
                session = conn.execute(
                    """
                    SELECT app_name, user_id, id, state, create_time, update_time
                    FROM sessions
                    WHERE app_name = ? AND user_id = ? AND id = ?
                    """,
                    (APP_NAME, user_id, session_id),
                ).fetchone()
            else:
                session = conn.execute(
                    """
                    SELECT app_name, user_id, id, state, create_time, update_time
                    FROM sessions
                    WHERE app_name = ? AND id = ?
                    """,
                    (APP_NAME, session_id),
                ).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="Session not found")

            if _MATCREATOR_MODE == "server":
                event_rows = conn.execute(
                    """
                    SELECT event_data
                    FROM events
                    WHERE app_name = ? AND user_id = ? AND session_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (APP_NAME, user_id, session_id),
                ).fetchall()
            else:
                event_rows = conn.execute(
                    """
                    SELECT event_data
                    FROM events
                    WHERE app_name = ? AND session_id = ?
                    ORDER BY timestamp ASC
                    """,
                    (APP_NAME, session_id),
                ).fetchall()
    except sqlite3.Error as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read session: {exc}")

    summary = _session_row_to_summary(session)
    summary["summary"] = _get_session_summary(session_id, user_id if _MATCREATOR_MODE == "server" else None)
    summary["state"] = _load_json_field(session["state"], {})
    events = [
        _load_json_field(row["event_data"], {})
        for row in event_rows
    ]
    # Return the canonical session history as-is so the frontend reflects only
    # what was actually persisted in the session DB.
    summary["events"] = events
    return JSONResponse(summary)


@app.get("/api/sessions/{session_id}/remote-jobs")
async def list_session_remote_jobs(
    session_id: str,
    user_id: str = Query(..., description="Current signed-in user"),
) -> JSONResponse:
    """Return durable remote-job snapshots owned by one user/session."""
    return JSONResponse(
        {
            "session_id": session_id,
            "jobs": _remote_job_store_for_owner(user_id).list_jobs(
                owner_id=user_id, session_id=session_id
            ),
        }
    )


@app.get("/api/sessions/{session_id}/remote-jobs/{job_id}/events")
async def list_session_remote_job_events(
    session_id: str,
    job_id: str,
    user_id: str = Query(..., description="Current signed-in user"),
    after: int = Query(default=0, ge=0),
) -> JSONResponse:
    """Return a remote job's replayable durable event history."""
    store = _remote_job_store_for_owner(user_id)
    job = store.get_job(job_id)
    if job is None or job["owner_id"] != user_id or job["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Remote job not found")
    return JSONResponse({"job": job, "events": store.list_events(job_id, after=after)})


def _get_owned_remote_job(session_id: str, job_id: str, user_id: str) -> dict[str, Any]:
    job = _remote_job_store_for_owner(user_id).get_job(job_id)
    if job is None or job["owner_id"] != user_id or job["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Remote job not found")
    return job


@app.post("/api/sessions/{session_id}/remote-jobs/{job_id}/pause")
async def pause_session_remote_job(
    session_id: str,
    job_id: str,
    user_id: str = Query(..., description="Current signed-in user"),
) -> JSONResponse:
    """Pause one E2B sandbox and notify its linked executor without stopping it."""
    job = _get_owned_remote_job(session_id, job_id, user_id)
    try:
        paused = await asyncio.to_thread(_remote_job_service_for_owner(user_id).pause_e2b, job_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await asyncio.to_thread(
        _remote_job_store_for_owner(user_id).record_user_control,
        job_id,
        "pause",
    )
    return JSONResponse(paused)


@app.post("/api/sessions/{session_id}/remote-jobs/{job_id}/terminate")
async def terminate_session_remote_job(
    session_id: str,
    job_id: str,
    user_id: str = Query(..., description="Current signed-in user"),
) -> JSONResponse:
    """Terminate one E2B sandbox and notify its linked executor without stopping it."""
    job = _get_owned_remote_job(session_id, job_id, user_id)
    try:
        terminated = await asyncio.to_thread(_remote_job_service_for_owner(user_id).terminate_e2b, job_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await asyncio.to_thread(
        _remote_job_store_for_owner(user_id).record_user_control,
        job_id,
        "terminate",
    )
    return JSONResponse(terminated)


@app.post("/api/sessions/{session_id}/remote-jobs/{job_id}/refresh")
async def refresh_session_remote_job(
    session_id: str,
    job_id: str,
    user_id: str = Query(..., description="Current signed-in user"),
) -> JSONResponse:
    """Synchronize a caller-owned active E2B job with its sandbox."""
    job = _get_owned_remote_job(session_id, job_id, user_id)
    if job["provider"] != "e2b":
        raise HTTPException(status_code=409, detail="Remote job is not managed by E2B")
    try:
        refreshed = await asyncio.to_thread(_remote_job_service_for_owner(user_id).reconcile_e2b, job_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(refreshed)


@app.get("/api/admin/sessions")
async def list_all_sessions(user_id: str = Query(..., description="Current signed-in user")) -> JSONResponse:
    if not _is_admin(user_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return JSONResponse(_query_session_summaries())


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> JSONResponse:
    """Delete a session and all associated metadata. Workspace files are not deleted."""
    # 1. Delete from session DB (events cascade-deleted via FK)
    deleted_owners: list[str] = []
    db_paths = list(_iter_session_db_paths()) if _MATCREATOR_MODE == "server" else [("", SESSION_DB_PATH)]
    for owner_id, db_path in db_paths:
        if not db_path.exists():
            continue
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                cur = conn.execute(
                    "DELETE FROM sessions WHERE app_name = ? AND id = ?",
                    (APP_NAME, session_id),
                )
                if cur.rowcount:
                    deleted_owners.append(owner_id)
        except sqlite3.Error as exc:
            raise HTTPException(status_code=500, detail=f"Failed to delete session from DB: {exc}")

    # 2. Remove summary entry
    summaries = _load_summaries()
    if session_id in summaries:
        del summaries[session_id]
        _save_summaries(summaries)

    # 3. Delete session-scoped metadata artifacts (not workspace files)
    if _MATCREATOR_MODE == "server":
        targets = []
        for owner_id in deleted_owners:
            workspace = _user_workspace_root(owner_id)
            adk_dir = _user_adk_dir(owner_id)
            targets.extend([
                adk_dir / "agent_graphs" / f"{session_id}.json",
                workspace / "trajectories" / f"{session_id}.jsonl",
                workspace / "trajectories" / f"{session_id}_summary.json",
                workspace / "cancellation" / f"{session_id}.flag",
            ])
    else:
        workspace = get_workspace_root()
        targets = [
            _ADK_DIR / "agent_graphs" / f"{session_id}.json",
            workspace / "trajectories" / f"{session_id}.jsonl",
            workspace / "trajectories" / f"{session_id}_summary.json",
            workspace / "cancellation" / f"{session_id}.flag",
        ]
    for target in targets:
        try:
            if target.is_file():
                target.unlink()
        except OSError:
            pass  # best-effort cleanup

    return JSONResponse({"status": "ok", "deleted": session_id})


def _load_execution_graph(session_id: str) -> dict:
    """Read execution_graph from the SQLite session state."""
    for _, db_path in _iter_session_db_paths():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT state FROM sessions WHERE app_name = ? AND id = ?",
                    (APP_NAME, session_id),
                ).fetchone()
                if row is None:
                    continue
                state = _load_json_field(row["state"], {})
                raw = state.get("execution_graph")
                if isinstance(raw, str):
                    raw = _load_json_field(raw, None)
                if not isinstance(raw, dict):
                    return {"nodes": {}, "edges": []}
                return raw
        except sqlite3.Error:
            continue
    return {"nodes": {}, "edges": []}


def _load_session_log_export(session_id: str, user_id: str | None = None) -> dict:
    owner_id, state = _load_session_state(session_id, user_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    payload = build_session_log_export(session_id, state)
    payload["owner_id"] = owner_id
    return payload


def _build_evaluation_question_draft(session_log: dict[str, Any]) -> dict[str, Any]:
    """Build an editable question template from bounded, observable session evidence."""
    nodes = session_log.get("graph", {}).get("nodes", [])
    successful_steps = [
        node for node in nodes
        if isinstance(node, dict) and node.get("status") == "success"
    ][:8]
    artifacts = [str(path) for path in session_log.get("artifacts", [])][:20]
    source_session_id = str(session_log["session_id"])
    evidence_steps = [
        {
            "step_number": node.get("step_number"),
            "action": node.get("action") or "Unnamed step",
            "summary": node.get("summary") or "",
            "tool_call_count": node.get("tool_call_count", 0),
            "artifact_count": node.get("artifact_count", 0),
        }
        for node in successful_steps
    ]
    return {
        "status": "draft",
        "source": {
            "session_id": source_session_id,
            "owner_id": session_log.get("owner_id"),
            "event_count": session_log.get("event_count", 0),
            "artifact_count": session_log.get("artifact_count", 0),
        },
        "question": {
            "title": f"Session-derived task: {source_session_id}",
            "prompt": "",
            "expected_deliverables": [],
            "rubrics": [],
            "tags": ["generated_from_session"],
        },
        "evidence": {
            "successful_steps": evidence_steps,
            "artifacts": artifacts,
        },
        "publication": {
            "status": "local_preview",
            "message": (
                "This is an editable draft preview. MatBench publication is unavailable until "
                "its custom-question authoring API and rubric schema are configured."
            ),
        },
    }


def _session_question_staging_root(owner_id: str) -> Path:
    if _MATCREATOR_MODE == "server":
        if not owner_id:
            raise HTTPException(status_code=400, detail="user_id is required in server mode")
        return _user_matcreator_home(owner_id) / "evals" / "question-drafts"
    return _MATCREATOR_HOME / "evals" / "question-drafts"


def _legacy_session_question_staging_root(owner_id: str) -> Path:
    return _evaluation_workspace_for_owner(owner_id) / "question-drafts"


def _benchmark_question_bank_root() -> Path:
    benchmark_config = load_config().get("benchmark") or {}
    if not isinstance(benchmark_config, dict):
        benchmark_config = {}
    configured = (
        os.environ.get("MAT_BENCH_QUESTION_BANK_ROOT", "").strip()
        or str(benchmark_config.get("question_bank_root") or "").strip()
    )
    if not configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Benchmark question-bank export is not configured. Set MAT_BENCH_QUESTION_BANK_ROOT "
                "or benchmark.question_bank_root in config.yaml."
            ),
        )
    return Path(configured).expanduser().resolve()


def _session_question_template_path() -> Path:
    configured = load_config().get("session_question_generator") or {}
    if not isinstance(configured, dict):
        configured = {}
    override = str(configured.get("template_path") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(str(files("matcreator").joinpath("question_templates/mab_qa.json"))).resolve()


def _session_question_generator() -> BuiltinLlmQuestionGeneratorPlugin:
    configured = load_config().get("session_question_generator") or {}
    if not isinstance(configured, dict):
        configured = {}
    plugin_name = str(configured.get("plugin") or "builtin_llm")
    if plugin_name != "builtin_llm":
        raise HTTPException(status_code=422, detail=f"Unknown session question generator plugin: {plugin_name}")
    try:
        return BuiltinLlmQuestionGeneratorPlugin.from_config(load_config())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/execution-graph/{session_id}")
async def get_execution_graph(session_id: str) -> JSONResponse:
    """Return the execution graph (plan DAG) from session state for frontend visualization."""
    data = _load_execution_graph(session_id)
    return JSONResponse(data)


@app.get("/api/sessions/{session_id}/session-log")
async def download_session_log(
    session_id: str,
    user_id: str = Query(default="", description="Current user ID; required to scope server-mode sessions."),
) -> Response:
    payload = _load_session_log_export(session_id, user_id or None)
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    response = Response(content=content, media_type="application/json")
    safe_session_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="matcreator-session-log-{safe_session_id}.json"'
    )
    return response


@app.post("/api/sessions/{session_id}/evaluation-question-draft")
async def create_evaluation_question_draft(
    session_id: str,
    user_id: str = Query(default="", description="Current user ID; required to scope server-mode sessions."),
) -> JSONResponse:
    """Create a local editable template from a session without publishing it."""
    session_log = _load_session_log_export(session_id, user_id or None)
    return JSONResponse(_build_evaluation_question_draft(session_log))


@app.post("/api/sessions/{session_id}/evaluation-question-drafts")
async def generate_evaluation_question_draft(
    session_id: str,
    user_id: str = Query(default="", description="Current user ID; required to scope server-mode sessions."),
) -> JSONResponse:
    """Generate a review-only staged benchmark question from bounded session evidence."""
    session_log = _load_session_log_export(session_id, user_id or None)
    owner_id = str(session_log.get("owner_id") or user_id)
    if not owner_id:
        raise HTTPException(status_code=422, detail="A session owner is required to stage a benchmark question")
    model, _api_key, _base_url = _llm_config()
    started_at = time.monotonic()
    logger.info(
        "Session question generation started: session_id=%s owner_id=%s model=%s",
        session_id,
        owner_id,
        model or "unconfigured",
    )
    try:
        service = StagedSessionQuestionService(
            _session_question_staging_root(owner_id),
            _session_question_generator(),
            template_path=_session_question_template_path(),
            legacy_roots=[_legacy_session_question_staging_root(owner_id)],
        )
        draft = await service.create(session_log)
    except HTTPException as exc:
        logger.warning(
            "Session question generation rejected: session_id=%s owner_id=%s status_code=%s duration_seconds=%.2f",
            session_id,
            owner_id,
            exc.status_code,
            time.monotonic() - started_at,
        )
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "Session question generation rejected: session_id=%s owner_id=%s error_type=%s duration_seconds=%.2f",
            session_id,
            owner_id,
            type(exc).__name__,
            time.monotonic() - started_at,
        )
        raise HTTPException(status_code=422, detail=f"Could not generate benchmark question draft: {exc}") from exc
    except Exception as exc:
        logger.exception(
            "Session question generation failed: session_id=%s owner_id=%s error_type=%s duration_seconds=%.2f",
            session_id,
            owner_id,
            type(exc).__name__,
            time.monotonic() - started_at,
        )
        raise HTTPException(status_code=502, detail="Question generation provider request failed") from exc
    logger.info(
        "Session question generation completed: session_id=%s owner_id=%s draft_id=%s status=%s duration_seconds=%.2f",
        session_id,
        owner_id,
        draft.draft_id,
        draft.status,
        time.monotonic() - started_at,
    )
    return JSONResponse(draft.as_dict(), status_code=201)


def _staged_question_service(owner_id: str) -> StagedSessionQuestionService:
    if not owner_id:
        raise HTTPException(status_code=422, detail="A session owner is required to access benchmark question drafts")
    return StagedSessionQuestionService(
        _session_question_staging_root(owner_id),
        legacy_roots=[_legacy_session_question_staging_root(owner_id)],
    )


@app.get("/api/evaluation-question-drafts")
async def list_evaluation_question_drafts(user_id: str = Query(...)) -> JSONResponse:
    return JSONResponse({"drafts": _staged_question_service(user_id).list()})


@app.get("/api/evaluation-question-drafts/{draft_id}")
async def get_evaluation_question_draft(draft_id: str, user_id: str = Query(...)) -> JSONResponse:
    try:
        draft = _staged_question_service(user_id).get(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(draft.as_dict())


@app.put("/api/evaluation-question-drafts/{draft_id}")
async def update_evaluation_question_draft(
    draft_id: str, body: EvaluationQuestionDraftUpdateBody, user_id: str = Query(...)
) -> JSONResponse:
    try:
        draft = _staged_question_service(user_id).update(draft_id, body.question_yaml)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(draft.as_dict())


@app.post("/api/evaluation-question-drafts/{draft_id}/approve")
async def approve_evaluation_question_draft(draft_id: str, user_id: str = Query(...)) -> JSONResponse:
    try:
        draft = _staged_question_service(user_id).approve(draft_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(draft.as_dict())


@app.post("/api/evaluation-question-drafts/{draft_id}/refine")
async def refine_evaluation_question_draft(
    draft_id: str,
    body: EvaluationQuestionDraftRefineBody = Body(default=EvaluationQuestionDraftRefineBody()),
    user_id: str = Query(...),
) -> JSONResponse:
    try:
        service = StagedSessionQuestionService(
            _session_question_staging_root(user_id),
            _session_question_generator(),
            template_path=_session_question_template_path(),
            legacy_roots=[_legacy_session_question_staging_root(user_id)],
        )
        draft = await service.refine(draft_id, body.instruction)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Question refinement provider failed: draft_id=%s", draft_id)
        raise HTTPException(status_code=502, detail="Question refinement provider request failed") from exc
    return JSONResponse(draft.as_dict())


@app.post("/api/evaluation-question-drafts/{draft_id}/export")
async def export_evaluation_question_draft(draft_id: str, user_id: str = Query(...)) -> JSONResponse:
    try:
        draft = _staged_question_service(user_id).export(draft_id, _benchmark_question_bank_root())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not export benchmark question: {exc}") from exc
    return JSONResponse(draft.as_dict())



@app.get("/api/agent-graph/{session_id}")
async def get_agent_graph(session_id: str) -> JSONResponse:
    data = _load_agent_graph_data(session_id)
    if not data:
        return JSONResponse({"session_id": session_id, "nodes": {}, "edges": [], "updated_at": None})
    return JSONResponse(data)


@app.post("/api/workspace/cli")
async def run_workspace_cli(body: WorkspaceCliBody) -> JSONResponse:
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=422, detail="command cannot be empty")

    if _MATCREATOR_MODE == "server":
        if not body.user_id:
            raise HTTPException(status_code=400, detail="user_id required in server mode")
        result = await asyncio.to_thread(
            _run_worker_workspace_cli,
            body.user_id,
            command,
            body.cwd,
        )
    else:
        result = await _run_local_workspace_cli(command, body.cwd)
    return JSONResponse(result)


@app.post("/api/workspace/complete")
async def complete_workspace_cli(body: WorkspaceCompleteBody) -> JSONResponse:
    if _MATCREATOR_MODE == "server":
        if not body.user_id:
            raise HTTPException(status_code=400, detail="user_id required in server mode")
        workspace = _user_workspace_root(body.user_id)
    else:
        workspace = get_workspace_root()
    workspace.mkdir(parents=True, exist_ok=True)
    return JSONResponse(_complete_workspace_paths(workspace, body.cwd, body.token))


@app.websocket("/api/workspace/terminal")
async def workspace_terminal(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        if _MATCREATOR_MODE == "server":
            user_id = websocket.query_params.get("user_id", "")
            if not user_id:
                await websocket.send_text(json.dumps({
                    "type": "output",
                    "data": "user_id required in server mode\r\n",
                }))
                await websocket.close(code=1008)
                return
            await _worker_terminal_session(websocket, user_id)
        else:
            await _local_terminal_session(websocket)
    except Exception as exc:
        logger.exception("Workspace terminal failed")
        try:
            await websocket.send_text(json.dumps({
                "type": "output",
                "data": f"\r\n[terminal error] {exc}\r\n",
            }))
        except Exception:
            pass


@app.get("/api/workspace/files")
async def serve_workspace_file(
    path: str = Query(..., description="Absolute or workspace-relative file path"),
    session_id: str = Query(default="", description="Session ID to resolve custom workdir boundaries"),
) -> FileResponse:
    resolved = _resolve_readable_file_path(path, session_id)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(resolved)


@app.get("/api/structure/view")
async def view_structure(
    path: str = Query(..., description="Absolute or workspace-relative structure file path"),
    session_id: str = Query(default="", description="Session ID to resolve custom workdir boundaries"),
) -> JSONResponse:
    from io import StringIO

    try:
        from ase.io import write as ase_write
    except ImportError:
        raise HTTPException(status_code=500, detail="ASE is not installed")

    resolved = _resolve_readable_file_path(path, session_id)
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        atoms = _ase_read_structure(resolved)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse structure: {exc}")

    xyz_buf = StringIO()
    ase_write(xyz_buf, atoms, format="xyz")
    xyz_data = xyz_buf.getvalue()

    structure_buf = StringIO()
    ase_write(structure_buf, atoms, format="extxyz")

    return JSONResponse({
        "xyz": xyz_data,
        "structure_string": structure_buf.getvalue(),
        "formula": atoms.get_chemical_formula(),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
        "cell": atoms.cell.tolist() if atoms.pbc.any() else None,
    })


@app.post("/api/structure/model")
async def model_structure(request: Request) -> JSONResponse:
    """Apply a MatCraft Kit-backed modeling operation and save the result."""
    from io import StringIO

    from ase.io import write as ase_write
    from structure_modeling import (
        ModelingError,
        apply_modeling_operation,
        load_working_structure,
        save_generated_structure,
    )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    source_path = str(body.get("path", "")).strip()
    structure_string = str(body.get("structure_string", ""))
    session_id = str(body.get("session_id", "")).strip()
    operation = str(body.get("operation", "")).strip().lower()
    params = body.get("params") or {}
    if (
        (not source_path and not structure_string)
        or not operation
        or not isinstance(params, dict)
    ):
        raise HTTPException(status_code=400, detail="A source structure, operation, and params are required")
    if len(structure_string) > 50_000_000:
        raise HTTPException(status_code=400, detail="Current structure payload is too large")

    def load_source_path():
        resolved = _resolve_readable_file_path(source_path, session_id)
        if not resolved.is_file():
            raise HTTPException(status_code=404, detail="Source structure not found")
        return _ase_read_structure(resolved)

    try:
        source = load_working_structure(structure_string, load_source_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse current structure: {exc}")

    secondary = None
    secondary_path = str(params.get("secondary_path", "")).strip()
    if secondary_path:
        secondary_resolved = _resolve_readable_file_path(secondary_path, session_id)
        if not secondary_resolved.is_file():
            raise HTTPException(status_code=404, detail="Secondary structure not found")
        secondary = _ase_read_structure(secondary_resolved)

    try:
        result = apply_modeling_operation(
            source, operation, params, secondary=secondary
        )
        output_dir = _get_workdir_for_session(session_id).resolve()
        output_path = save_generated_structure(result, output_dir, operation)
    except ModelingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Structure modeling operation failed")
        raise HTTPException(status_code=500, detail=f"Modeling failed: {exc}")

    structure_buf = StringIO()
    ase_write(structure_buf, result, format="extxyz")
    owner_id, _ = _load_session_state(session_id) if session_id else (None, {})
    response_path = (
        _control_plane_path_to_worker(owner_id, output_path) if owner_id else str(output_path)
    )
    return JSONResponse({
        "path": response_path,
        "structure_string": structure_buf.getvalue(),
        "formula": result.get_chemical_formula(),
        "n_atoms": len(result),
        "periodic": bool(result.pbc.any()),
        "operation": operation,
    })


@app.post("/api/structure/save")
async def save_edited_structure(request: Request) -> JSONResponse:
    """Validate and persist an atom-edited ExtXYZ structure as a new artifact."""
    from io import StringIO

    from ase.io import read as ase_read, write as ase_write
    from structure_modeling import save_generated_structure

    try:
        body = await request.json()
        structure_string = str(body.get("structure_string", ""))
        session_id = str(body.get("session_id", "")).strip()
        if not structure_string or len(structure_string) > 50_000_000:
            raise HTTPException(status_code=400, detail="Invalid edited structure payload")
        atoms = ase_read(StringIO(structure_string), format="extxyz")
        output_path = save_generated_structure(
            atoms, _get_workdir_for_session(session_id).resolve(), "edited-structure"
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot save edited structure: {exc}")

    structure_buf = StringIO()
    ase_write(structure_buf, atoms, format="extxyz")
    owner_id, _ = _load_session_state(session_id) if session_id else (None, {})
    response_path = (
        _control_plane_path_to_worker(owner_id, output_path) if owner_id else str(output_path)
    )
    return JSONResponse({
        "path": response_path,
        "structure_string": structure_buf.getvalue(),
        "formula": atoms.get_chemical_formula(),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
        "operation": "atom-edit",
    })


@app.post("/api/structure/interfaces")
async def build_interface_candidates(request: Request) -> JSONResponse:
    """Generate coherent ZSL interface candidates without saving all candidates."""
    from io import StringIO

    from ase.io import write as ase_write
    from structure_modeling import (
        ModelingError,
        generate_coherent_interfaces,
        load_working_structure,
    )

    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    film_path = str(body.get("path", "")).strip()
    structure_string = str(body.get("structure_string", ""))
    substrate_path = str(body.get("secondary_path", "")).strip()
    session_id = str(body.get("session_id", "")).strip()
    params = body.get("params") or {}
    if (
        (not film_path and not structure_string)
        or not substrate_path
        or not isinstance(params, dict)
    ):
        raise HTTPException(status_code=400, detail="Film structure, substrate path, and params are required")
    if len(structure_string) > 50_000_000:
        raise HTTPException(status_code=400, detail="Current structure payload is too large")

    substrate_resolved = _resolve_readable_file_path(substrate_path, session_id)
    if not substrate_resolved.is_file():
        raise HTTPException(status_code=404, detail="Substrate structure not found")

    def load_film_path():
        film_resolved = _resolve_readable_file_path(film_path, session_id)
        if not film_resolved.is_file():
            raise HTTPException(status_code=404, detail="Film structure not found")
        return _ase_read_structure(film_resolved)

    try:
        film = load_working_structure(structure_string, load_film_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot parse current film structure: {exc}")

    try:
        candidates = generate_coherent_interfaces(
            film,
            _ase_read_structure(substrate_resolved),
            params,
        )
    except ModelingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Coherent interface generation failed")
        raise HTTPException(status_code=500, detail=f"Interface generation failed: {exc}")

    serialized = []
    for candidate in candidates:
        structure_buf = StringIO()
        ase_write(structure_buf, candidate.pop("atoms"), format="extxyz")
        serialized.append({**candidate, "structure_string": structure_buf.getvalue()})
    return JSONResponse({"interfaces": serialized})


@app.post("/api/structure/interfaces/save")
async def save_interface_candidate(request: Request) -> JSONResponse:
    """Validate and persist one generated interface candidate."""
    from io import StringIO

    from ase.io import read as ase_read, write as ase_write
    from structure_modeling import save_generated_structure

    try:
        body = await request.json()
        structure_string = str(body.get("structure_string", ""))
        session_id = str(body.get("session_id", "")).strip()
        if not structure_string or len(structure_string) > 50_000_000:
            raise HTTPException(status_code=400, detail="Invalid interface structure payload")
        atoms = ase_read(StringIO(structure_string), format="extxyz")
        output_path = save_generated_structure(
            atoms, _get_workdir_for_session(session_id).resolve(), "interface"
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Cannot save interface: {exc}")

    structure_buf = StringIO()
    ase_write(structure_buf, atoms, format="extxyz")
    owner_id, _ = _load_session_state(session_id) if session_id else (None, {})
    response_path = (
        _control_plane_path_to_worker(owner_id, output_path) if owner_id else str(output_path)
    )
    return JSONResponse({
        "path": response_path,
        "structure_string": structure_buf.getvalue(),
        "formula": atoms.get_chemical_formula(),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
        "operation": "interface",
    })


@app.get("/api/structure/files")
async def list_modeling_structure_files(
    session_id: str = Query(default="", description="Session whose working directory is listed"),
) -> JSONResponse:
    """List supported structure files below the active session working directory."""
    structure_suffixes = {".cif", ".xyz", ".extxyz", ".vasp", ".pdb", ".sdf", ".mol", ".mol2"}
    root = _get_workdir_for_session(session_id).resolve()
    if not root.is_dir():
        return JSONResponse({"files": []})

    owner_id, _ = _load_session_state(session_id) if session_id else (None, {})
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in structure_suffixes and path.name.lower() not in {"poscar", "contcar"}:
            continue
        files.append({
            "name": path.name,
            "path": _control_plane_path_to_worker(owner_id, path) if owner_id else str(path),
            "relative_path": str(path.relative_to(root)),
        })
    return JSONResponse({"files": files})


@app.get("/api/sessions/{session_id}/files")
async def list_session_files(session_id: str) -> JSONResponse:
    owner_id, _ = _load_session_state(session_id)
    session_dir = _get_workdir_for_session(session_id)
    if not session_dir.exists():
        return JSONResponse({"files": []})
    files = [
        {
            "name": f.name,
            "path": _control_plane_path_to_worker(owner_id, f) if owner_id else str(f),
            "size": f.stat().st_size,
        }
        for f in sorted(session_dir.rglob("*"))
        if f.is_file()
    ]
    return JSONResponse({"files": files})


@app.post("/api/sessions/{session_id}/files")
async def upload_session_file(session_id: str, file: UploadFile = File(...)) -> JSONResponse:
    owner_id, _ = _load_session_state(session_id)
    session_dir = _get_workdir_for_session(session_id)
    upload_dir = session_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    filename = _safe_upload_filename(file.filename or "")
    target = _available_upload_path(upload_dir, filename)

    size = 0
    try:
        with target.open("wb") as fh:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                fh.write(chunk)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {exc}")
    finally:
        await file.close()

    return JSONResponse({
        "name": target.name,
        "path": _control_plane_path_to_worker(owner_id, target) if owner_id else str(target),
        "size": size,
    })


@app.delete("/api/sessions/{session_id}/files")
async def delete_session_file(
    session_id: str,
    path: str = Query(..., description="Absolute or session-relative file path"),
) -> JSONResponse:
    owner_id, _ = _load_session_state(session_id)
    session_dir = _get_workdir_for_session(session_id).resolve()
    if _MATCREATOR_MODE == "server" and owner_id:
        resolved = _map_worker_path_to_control_plane(owner_id, path)
        if resolved is None:
            raise HTTPException(status_code=404, detail="File not found")
        resolved = resolved.resolve()
    else:
        p = Path(path)
        resolved = p.resolve() if p.is_absolute() else (session_dir / p).resolve()

    if not resolved.is_relative_to(session_dir):
        raise HTTPException(status_code=403, detail="Access denied: path is outside session")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        resolved.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {exc}")

    return JSONResponse({"deleted": True, "path": str(resolved)})


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session_execution(
    session_id: str,
    user_id: str = Query(default="", description="Current signed-in user"),
    reason: str = Query(default="user_requested", description="Cancellation reason"),
) -> JSONResponse:
    """Request cancellation of any ongoing execution for this session.

    The agent runner checks this flag before each step (graceful) and
    periodically during a step (force). The flag is cleared automatically
    when the orchestrator routes back to the planner.
    """
    paused_jobs = []
    if user_id:
        paused_jobs = await asyncio.to_thread(
            _remote_job_service_for_owner(user_id).pause_active_session_e2b_jobs,
            owner_id=user_id,
            session_id=session_id,
        )
    await asyncio.to_thread(request_cancellation, session_id, reason)
    await asyncio.to_thread(
        AgentGraphLogger(session_id).mark_running_nodes_cancelled,
        summary=f"Cancelled by user ({reason})"
    )
    for run in _run_registry.active_for_session(session_id):
        await _run_registry.request_cancel(run)
    return JSONResponse({
        "status": "ok",
        "session_id": session_id,
        "remote_jobs": paused_jobs,
        "message": "Cancellation requested. The running step will stop at the next checkpoint.",
    })


@app.get("/api/sessions/{session_id}/cancel")
async def get_cancellation_status(session_id: str) -> JSONResponse:
    """Check whether a cancellation is currently pending for this session."""
    flagged = is_cancellation_requested(session_id)
    return JSONResponse({
        "session_id": session_id,
        "cancellation_requested": flagged,
        "reason": get_cancellation_reason(session_id) if flagged else None,
    })


@app.delete("/api/sessions/{session_id}/cancel")
async def clear_cancellation_flag(session_id: str) -> JSONResponse:
    """Manually clear a pending cancellation flag."""
    clear_cancellation(session_id)
    return JSONResponse({
        "status": "ok",
        "session_id": session_id,
        "message": "Cancellation flag cleared.",
    })


@app.post("/api/sessions/{session_id}/cancel-step/{step_number}")
async def cancel_individual_step(
    session_id: str,
    step_number: int,
    reason: str = Query(default="user_requested", description="Cancellation reason"),
) -> JSONResponse:
    """Cancel a specific running step without stopping the whole session.

    The step executor polls the per-step flag and exits at the next checkpoint.
    The graph node for that step is updated immediately so the frontend reflects
    the cancellation before the executor polls.
    """
    await asyncio.to_thread(request_step_cancellation, session_id, step_number, reason)
    found = await asyncio.to_thread(
        AgentGraphLogger(session_id).cancel_step_node_by_number,
        step_number, f"Cancelled by user ({reason})"
    )
    return JSONResponse({
        "status": "ok",
        "session_id": session_id,
        "step_number": step_number,
        "graph_updated": found,
        "message": f"Step {step_number} cancellation requested.",
    })


def _skill_bundle_roots() -> list[Path]:
    return [_MODULE_SKILLS_ROOT, official_skills_dir(), workspace_skills_dir(), Path.home() / ".matcreator" / "skills"]


def _resolve_skill_dir(skill_name: str) -> Path:
    if not _SKILL_NAME_RE.match(skill_name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name: '{skill_name}'.")
    skill_dir = _skill_dir_map().get(skill_name)
    if skill_dir is None:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found.")
    resolved = skill_dir.resolve()
    allowed = [root.expanduser().resolve() for root in _skill_bundle_roots()]
    if not any(resolved.is_relative_to(root) for root in allowed):
        raise HTTPException(status_code=403, detail="Skill path is outside editable skill roots.")
    if not (resolved / "SKILL.md").is_file():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' has no SKILL.md.")
    return resolved


def _split_skill_md(text: str) -> tuple[dict, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid SKILL.md frontmatter: {exc}")
            if not isinstance(frontmatter, dict):
                frontmatter = {}
            return frontmatter, parts[2].lstrip("\n")
    return {}, text


def _compose_skill_md(frontmatter: dict, body: str) -> str:
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{dumped}\n---\n{body.rstrip()}\n"


def _skill_attachment_categories(skill_dir: Path) -> list[dict]:
    categories = []
    for folder in sorted(
        (path for path in skill_dir.rglob("*") if path.is_dir() and not any(part.startswith(".") for part in path.relative_to(skill_dir).parts)),
        key=lambda p: p.relative_to(skill_dir).as_posix(),
    ):
        if folder == skill_dir:
            continue
        files = []
        for item in sorted(folder.iterdir(), key=lambda p: p.name):
            if item.is_file():
                stat = item.stat()
                rel_path = item.relative_to(skill_dir).as_posix()
                files.append({
                    "path": rel_path,
                    "name": item.name,
                    "category": folder.relative_to(skill_dir).as_posix(),
                    "size": stat.st_size,
                })
        categories.append({"name": folder.relative_to(skill_dir).as_posix(), "files": files})
    return categories


def _resolve_skill_relative_file(skill_dir: Path, relative_path: str) -> Path:
    cleaned = relative_path.strip().lstrip("/")
    if not cleaned or cleaned in {"SKILL.md", ".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid attachment path.")
    target = (skill_dir / cleaned).resolve()
    if not target.is_relative_to(skill_dir.resolve()) or target == skill_dir.resolve():
        raise HTTPException(status_code=400, detail="Attachment path escapes the skill directory.")
    return target


def _sanitize_attachment_category(category: str) -> str:
    cleaned = category.strip().strip("/")
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="Attachment category is required.")
    parts = [_safe_upload_filename(part) for part in cleaned.split("/") if part.strip()]
    if not parts:
        raise HTTPException(status_code=400, detail="Attachment category is required.")
    return "/".join(parts)


def _delete_skill_graph_entries(skill_name: str) -> int:
    graph = _get_kg()
    entry_ids = []
    offset = 0
    page_size = 200
    while True:
        page = graph.list(limit=page_size, offset=offset)
        if not page:
            break
        for entry in page:
            if entry.title == skill_name and "matcreator-skill" in entry.tags:
                entry_ids.append(entry.id)
        if len(page) < page_size:
            break
        offset += len(page)
    deleted = sum(int(bool(graph.delete(entry_id))) for entry_id in entry_ids)
    try:
        graph.refresh()
    except Exception:
        pass
    return deleted


class SkillGraphEditBody(BaseModel):
    content: str
    description: str | None = None
    entry_type: str | None = None
    skill_level: str | None = None
    tags: list[str] = []
    dependent_skills: list[str] = []
    metadata: dict | None = None


class SkillGraphCreateBody(BaseModel):
    name: str
    description: str = ""
    content: str = ""
    entry_type: str = "capability"
    skill_level: str = "L1"
    tags: list[str] = []
    dependent_skills: list[str] = []


@app.post("/api/skill-graph/skills")
async def create_skill_graph_skill(body: SkillGraphCreateBody) -> JSONResponse:
    skill_name = body.name.strip()
    if not _SKILL_NAME_RE.match(skill_name):
        raise HTTPException(
            status_code=422,
            detail="Skill name must be lowercase alphanumeric with hyphens/underscores, starting with a letter or digit.",
        )
    if skill_name in {s.name for s in ALL_SKILLS} or skill_name in get_default_skill_names():
        raise HTTPException(status_code=409, detail=f"Skill '{skill_name}' already exists.")

    skill_dir = (workspace_skills_dir() / skill_name).resolve()
    workspace_root = workspace_skills_dir().resolve()
    if not skill_dir.is_relative_to(workspace_root):
        raise HTTPException(status_code=400, detail="Invalid skill path.")
    if skill_dir.exists():
        raise HTTPException(status_code=409, detail=f"Skill folder already exists: {skill_name}")

    frontmatter = {
        "name": skill_name,
        "description": body.description.strip() or f"Custom skill '{skill_name}'.",
        "metadata": {
            "entry_type": body.entry_type or "capability",
            "skill_level": body.skill_level or "L1",
            "tags": [str(tag).strip() for tag in body.tags if str(tag).strip()],
            "dependent_skills": [
                str(dep).strip()
                for dep in body.dependent_skills
                if str(dep).strip() and str(dep).strip() != skill_name
            ],
        },
    }
    content = _compose_skill_md(
        frontmatter,
        body.content.strip() or f"# {skill_name}\n\nDescribe how and when to use this skill.",
    )
    try:
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        refresh_result = refresh_skills()
    except OSError as exc:
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Failed to create skill: {exc}")
    except Exception as exc:
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Skill was written but failed to load: {exc}")
    return JSONResponse({"status": "ok", "skill_name": skill_name, "path": str(skill_dir), "refresh": refresh_result})


@app.delete("/api/skill-graph/skills/{skill_name}")
async def delete_skill_graph_skill(skill_name: str) -> JSONResponse:
    if not _SKILL_NAME_RE.match(skill_name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name: '{skill_name}'.")
    skill_dir = _resolve_skill_dir(skill_name)
    allowed_roots = [_MODULE_SKILLS_ROOT.resolve(), workspace_skills_dir().resolve()]
    if not any(skill_dir.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(status_code=400, detail="This skill root cannot be removed from the graph UI.")
    try:
        shutil.rmtree(skill_dir)
        deleted_graph_nodes = _delete_skill_graph_entries(skill_name)
        refresh_result = refresh_skills()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to remove skill: {exc}")
    return JSONResponse({
        "status": "ok",
        "deleted": skill_name,
        "deleted_graph_nodes": deleted_graph_nodes,
        "refresh": refresh_result,
    })


@app.get("/api/skill-graph/skills/{skill_name}/edit")
async def get_skill_graph_skill_edit(skill_name: str) -> JSONResponse:
    skill_dir = _resolve_skill_dir(skill_name)
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, body = _split_skill_md(text)
    metadata = dict(frontmatter.get("metadata") or {})
    known_skills = sorted({s.name for s in ALL_SKILLS if s.name != skill_name})
    return JSONResponse({
        "status": "ok",
        "skill_name": skill_name,
        "path": str(skill_dir),
        "frontmatter": _json_ready(frontmatter),
        "content": body,
        "description": frontmatter.get("description", ""),
        "entry_type": metadata.get("entry_type") or metadata.get("type") or "capability",
        "skill_level": metadata.get("skill_level") or "L1",
        "tags": metadata.get("tags") or [],
        "dependent_skills": metadata.get("dependent_skills") or [],
        "metadata": _json_ready(metadata),
        "attachments": _skill_attachment_categories(skill_dir),
        "available_skills": known_skills,
        "entry_types": [
            "capability",
            "procedure",
            "workflow",
            "tool",
            "repository",
            "environment",
            "dependency",
            "data",
            "analytical",
            "heuristic",
            "constraint",
            "generic",
        ],
        "skill_levels": ["L1", "L2", "L3", "L4"],
    })


@app.put("/api/skill-graph/skills/{skill_name}/edit")
async def update_skill_graph_skill_edit(skill_name: str, body: SkillGraphEditBody) -> JSONResponse:
    skill_dir = _resolve_skill_dir(skill_name)
    skill_md = skill_dir / "SKILL.md"
    frontmatter, _ = _split_skill_md(skill_md.read_text(encoding="utf-8"))
    frontmatter["name"] = skill_name
    if body.description is not None:
        frontmatter["description"] = body.description.strip()
    metadata = dict(frontmatter.get("metadata") or {})
    metadata.update(body.metadata or {})
    if body.entry_type:
        metadata["entry_type"] = body.entry_type
        metadata.pop("type", None)
    if body.skill_level:
        metadata["skill_level"] = body.skill_level
    metadata["tags"] = [str(tag).strip() for tag in body.tags if str(tag).strip()]
    metadata["dependent_skills"] = [
        str(dep).strip()
        for dep in body.dependent_skills
        if str(dep).strip() and str(dep).strip() != skill_name
    ]
    frontmatter["metadata"] = metadata
    content = _compose_skill_md(frontmatter, body.content)
    _validate_skill_md_name(content.encode("utf-8"), skill_name)
    try:
        skill_md.write_text(content, encoding="utf-8")
        refresh_result = refresh_skills()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save skill: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Skill saved but failed to reload: {exc}")
    return JSONResponse({"status": "ok", "skill_name": skill_name, "refresh": refresh_result})


@app.post("/api/skill-graph/skills/{skill_name}/attachments")
async def upload_skill_graph_attachment(
    skill_name: str,
    category: str = Form(...),
    files: List[UploadFile] = File(default=[]),
) -> JSONResponse:
    skill_dir = _resolve_skill_dir(skill_name)
    category_path = _sanitize_attachment_category(category)
    upload_dir = (skill_dir / category_path).resolve()
    if not upload_dir.is_relative_to(skill_dir.resolve()):
        raise HTTPException(status_code=400, detail="Attachment category escapes the skill directory.")
    upload_dir.mkdir(parents=True, exist_ok=True)
    written = []
    try:
        for file in files:
            if not file.filename:
                continue
            safe_name = _safe_upload_filename(file.filename)
            target = _available_upload_path(upload_dir, safe_name)
            target.write_bytes(await file.read())
            written.append(str(target.relative_to(skill_dir)))
        refresh_result = refresh_skills()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to upload attachment: {exc}")
    finally:
        for file in files:
            await file.close()
    return JSONResponse({"status": "ok", "skill_name": skill_name, "files": written, "refresh": refresh_result})


@app.delete("/api/skill-graph/skills/{skill_name}/attachments")
async def delete_skill_graph_attachment(skill_name: str, path: str = Query(...)) -> JSONResponse:
    skill_dir = _resolve_skill_dir(skill_name)
    target = _resolve_skill_relative_file(skill_dir, unquote(path))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Attachment not found.")
    try:
        target.unlink()
        parent = target.parent
        while parent != skill_dir and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
        refresh_result = refresh_skills()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete attachment: {exc}")
    return JSONResponse({"status": "ok", "skill_name": skill_name, "deleted": path, "refresh": refresh_result})


@app.get("/api/skills")
async def list_skills(user_id: str = Query(default="")) -> JSONResponse:
    """Return all loaded skills with their planning_enabled status and parent skill (if any)."""
    from matcreator.skill import _MODULE_SKILLS_ROOT, _discover_skill_dirs  # noqa: PLC0415

    parent_map: dict[str, str] = {}
    for root in [_MODULE_SKILLS_ROOT, official_skills_dir(), workspace_skills_dir()]:
        for path in _discover_skill_dirs(root):
            parent_skill_md = path.parent / "SKILL.md"
            if parent_skill_md.is_file():
                parent_map[path.name] = path.parent.name

    default_skill_names = get_default_skill_names()
    config = _load_config_for_user(user_id)
    planning_skills = set((config.get("planning") or {}).get("extra_skills") or [])
    disabled_skills = set((config.get("skills") or {}).get("disabled") or [])
    skills = []
    for s in sorted(ALL_SKILLS, key=lambda s: s.name):
        source = get_skill_source(s.name)
        skills.append({
            "name": s.name,
            "description": s.description or "",
            "planning_enabled": s.name in PLANNING_SKILL_NAMES or s.name in planning_skills,
            "enabled": s.name not in disabled_skills,
            "parent": parent_map.get(s.name),
            "source": source.name if source else None,
            "editable": bool(source and source.editable),
            "managed": bool(source and source.managed),
            "trusted": bool(source and source.trusted),
            "is_custom": bool(source and source.name in {"custom", "workspace"}),
        })
    return JSONResponse(skills)


@app.get("/api/skills/defaults")
async def list_default_skill_names() -> JSONResponse:
    """Return the names of all bundled default skills."""
    return JSONResponse({"names": sorted(get_default_skill_names())})


_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _validate_skill_md_name(content: bytes, expected_name: str) -> None:
    """Raise ValueError if the SKILL.md frontmatter name field doesn't match expected_name."""
    text = content.decode("utf-8", errors="replace")
    parts = text.split("---")
    if len(parts) < 3:
        return  # No frontmatter; ADK will catch structural issues later
    try:
        frontmatter = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ValueError(f"SKILL.md has invalid YAML frontmatter: {exc}")
    if not isinstance(frontmatter, dict):
        return
    md_name = frontmatter.get("name")
    if md_name is not None and md_name != expected_name:
        raise ValueError(
            f"SKILL.md 'name' field ('{md_name}') does not match the skill directory name ('{expected_name}'). "
            f"Update the 'name' field in SKILL.md to '{expected_name}'."
        )


@app.post("/api/skills/custom")
async def create_custom_skill(
    name: str = Form(...),
    skill_md: UploadFile = File(...),
    references: List[UploadFile] = File(default=[]),
    scripts: List[UploadFile] = File(default=[]),
) -> JSONResponse:
    """Upload a custom skill to the workspace skills directory."""
    name = name.strip()
    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(
            status_code=422,
            detail="Skill name must be lowercase alphanumeric with hyphens/underscores, starting with a letter or digit.",
        )
    if name in get_default_skill_names():
        raise HTTPException(
            status_code=409,
            detail=f"'{name}' is a built-in default skill. Custom skills cannot use the same name.",
        )

    skill_dir = workspace_skills_dir() / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    try:
        skill_md_content = await skill_md.read()
        try:
            _validate_skill_md_name(skill_md_content, name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        (skill_dir / "SKILL.md").write_bytes(skill_md_content)

        ref_names = []
        non_empty_refs = [r for r in references if r.filename]
        if non_empty_refs:
            ref_dir = skill_dir / "references"
            ref_dir.mkdir(exist_ok=True)
            for ref_file in non_empty_refs:
                safe_name = _safe_upload_filename(ref_file.filename or "ref")
                (ref_dir / safe_name).write_bytes(await ref_file.read())
                ref_names.append(safe_name)

        script_names = []
        non_empty_scripts = [s for s in scripts if s.filename]
        if non_empty_scripts:
            scripts_dir = skill_dir / "scripts"
            scripts_dir.mkdir(exist_ok=True)
            for script_file in non_empty_scripts:
                safe_name = _safe_upload_filename(script_file.filename or "script")
                (scripts_dir / safe_name).write_bytes(await script_file.read())
                script_names.append(safe_name)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to write skill files: {exc}")
    finally:
        await skill_md.close()
        for r in references:
            await r.close()
        for s in scripts:
            await s.close()

    try:
        refresh_skills()
    except Exception as exc:
        shutil.rmtree(skill_dir, ignore_errors=True)
        raise HTTPException(status_code=422, detail=f"Skill files were written but failed to load: {exc}")
    return JSONResponse({"status": "ok", "name": name, "references": ref_names, "scripts": script_names})


@app.delete("/api/skills/custom/{skill_name}")
async def delete_custom_skill(skill_name: str) -> JSONResponse:
    """Delete a custom workspace skill. Default skills cannot be deleted."""
    if not _SKILL_NAME_RE.match(skill_name):
        raise HTTPException(status_code=400, detail=f"Invalid skill name: '{skill_name}'.")
    if skill_name in get_default_skill_names():
        raise HTTPException(
            status_code=400,
            detail=f"'{skill_name}' is a built-in default skill and cannot be deleted.",
        )
    root = workspace_skills_dir()
    skill_dir = root / skill_name
    if skill_dir.resolve() == root.resolve() or not skill_dir.resolve().is_relative_to(root.resolve()):
        raise HTTPException(status_code=400, detail="Invalid skill path.")
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found in workspace.")
    try:
        shutil.rmtree(skill_dir)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete skill: {exc}")

    try:
        refresh_skills()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Skill deleted but registry reload failed: {exc}")
    return JSONResponse({"status": "ok", "deleted": skill_name})


@app.get("/api/settings")
async def get_settings(user_id: str = Query(default="")) -> JSONResponse:
    """Return the current user config."""
    return JSONResponse(_load_config_for_user(user_id))


class SettingsBody(BaseModel):
    planning: dict | None = None
    user: dict | None = None
    skills: dict | None = None
    workspace: dict | None = None
    llm: dict | None = None


@app.put("/api/settings")
async def update_settings(body: SettingsBody, user_id: str = Query(default="")) -> JSONResponse:
    """Merge *body* into the config, persist it, and reload skills."""
    config = _load_config_for_user(user_id)
    if body.planning is not None:
        config.setdefault("planning", {}).update(body.planning)
    if body.user is not None:
        config.setdefault("user", {}).update(body.user)
    if body.skills is not None:
        config.setdefault("skills", {}).update(body.skills)
    if body.workspace is not None:
        config.setdefault("workspace", {}).update(body.workspace)
    if body.llm is not None:
        config.setdefault("llm", {}).update(body.llm)
    _save_config_for_user(config, user_id)
    try:
        refresh_skills()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Settings saved but skill registry reload failed: {exc}")
    return JSONResponse({
        "status": "ok",
        "planning_skill_names": sorted(PLANNING_SKILL_NAMES),
    })


@app.get("/api/env-config")
async def get_env_config(user_id: str = Query(default="")) -> JSONResponse:
    """Return current LLM/custom environment overrides, masking sensitive fields.

    In local mode, values come from ~/.matcreator/config.yaml with process env
    fallback. In server mode, values come from the user's mounted config.yaml;
    deployment defaults are intentionally not returned.
    """
    mode = os.environ.get("MATCREATOR_MODE", "local")
    result: dict[str, str] = {}

    config = _load_config_for_user(user_id)

    if mode == "local":
        for env_key in _ENV_FIELDS:
            yaml_key = ENV_TO_YAML.get(env_key)
            if yaml_key:
                val = _get_nested_config_value(config, yaml_key)
            else:
                val = ""
            if not val:
                val = os.environ.get(env_key, "")
            result[env_key] = _masked_env_value(env_key, val)
    else:
        for field in _ENV_FIELDS:
            yaml_key = ENV_TO_YAML.get(field)
            val = _get_nested_config_value(config, yaml_key) if yaml_key else ""
            result[field] = _masked_env_value(field, val)

    result[_CUSTOM_ENV_CONFIG_KEY] = {
        key: _masked_env_value(key, value)
        for key, value in sorted(_custom_env_from_config(config).items())
    }

    return JSONResponse(result)


class EnvConfigBody(BaseModel):
    values: dict[str, Any]


@app.put("/api/env-config")
async def update_env_config(body: EnvConfigBody, user_id: str = Query(default="")) -> JSONResponse:
    """Write updated configuration fields.

    In local mode, writes to ~/.matcreator/config.yaml.
    In server mode, writes to the user's mounted ~/.matcreator/config.yaml.
    """
    mode = os.environ.get("MATCREATOR_MODE", "local")

    config = _load_config_for_user(user_id)
    previous_custom_env = _custom_env_from_config(config)

    for key, raw_value in body.values.items():
        if key == _CUSTOM_ENV_CONFIG_KEY:
            continue
        value = "" if raw_value is None else str(raw_value)
        if key not in _ENV_FIELDS:
            continue
        yaml_key = ENV_TO_YAML.get(key)
        if yaml_key is None:
            continue
        sensitive = yaml_key in SENSITIVE_YAML_KEYS or _is_sensitive_env_key(key)
        if sensitive and value == _ENV_VALUE_MASK:
            continue
        _set_nested_config_value(config, yaml_key, value)

    custom_env_raw = body.values.get(_CUSTOM_ENV_CONFIG_KEY)
    if isinstance(custom_env_raw, dict):
        existing_custom_env = _custom_env_from_config(config)
        next_custom_env: dict[str, str] = {}
        for raw_key, raw_value in custom_env_raw.items():
            env_key = str(raw_key).strip()
            if not env_key:
                continue
            _validate_user_env_key(env_key)
            value = "" if raw_value is None else str(raw_value)
            if _is_sensitive_env_key(env_key) and value == _ENV_VALUE_MASK:
                value = existing_custom_env.get(env_key, "")
            if value:
                next_custom_env[env_key] = value
        if next_custom_env:
            config["env"] = dict(sorted(next_custom_env.items()))
        else:
            config.pop("env", None)

    _save_config_for_user(config, user_id)

    if mode == "local":
        for key, raw_value in body.values.items():
            if key == _CUSTOM_ENV_CONFIG_KEY:
                continue
            value = "" if raw_value is None else str(raw_value)
            if key not in _ENV_FIELDS:
                continue
            yaml_key = ENV_TO_YAML.get(key)
            if yaml_key is None:
                continue
            sensitive = yaml_key in SENSITIVE_YAML_KEYS
            if sensitive and value == _ENV_VALUE_MASK:
                continue
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        if isinstance(custom_env_raw, dict):
            next_custom_env = _custom_env_from_config(config)
            for env_key in previous_custom_env:
                if env_key not in next_custom_env:
                    os.environ.pop(env_key, None)
            for env_key, value in _custom_env_from_config(config).items():
                os.environ[env_key] = value

    return JSONResponse({"status": "ok"})


@app.post("/api/restart-backend")
async def restart_backend(user_id: str = Query(default="")) -> JSONResponse:
    """Restart the ADK backend.

    In server mode: restart the user's worker container.
    In local mode: kill and relaunch the local ADK process on the configured port.
    """
    global _adk_process

    if _MATCREATOR_MODE == "server":
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id required in server mode")
        try:
            await asyncio.to_thread(remove_worker, user_id)
            target = await asyncio.to_thread(ensure_worker_running, user_id)
            return JSONResponse({"status": "recreated", "user_id": user_id, "target": target})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    _kill_port(get_adk_port())
    await asyncio.sleep(1.5)

    try:
        _adk_process = subprocess.Popen(
            get_local_adk_command(),
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_local_adk_env(),
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="'matcreator' command not found in PATH")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to start backend: {exc}")

    return JSONResponse({"status": "restarting", "pid": _adk_process.pid})


@app.get("/api/backend-status")
async def get_backend_status(user_id: str = Query(default="")) -> JSONResponse:
    """Check whether the ADK backend is reachable.

    In server mode: checks the user's worker container port.
    In local mode: checks the configured ADK port on localhost.
    """
    if _MATCREATOR_MODE == "server" and user_id:
        target = _worker_supervisor.target_for(user_id)
        if target is None:
            return JSONResponse({"ready": False})
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(f"{target.rstrip('/')}/list-apps")
            return JSONResponse({"ready": resp.status_code < 500})
        except httpx.HTTPError:
            return JSONResponse({"ready": False})

    return JSONResponse({"ready": _is_port_open(port=get_adk_port())})


# Serve built frontend in production
_dist = Path(__file__).parent / "vite-frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    host = "0.0.0.0" if _MATCREATOR_MODE == "server" else "127.0.0.1"
    uvicorn.run(app, host=host, port=get_web_port())
