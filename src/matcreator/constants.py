"""MatCreator agent configuration helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path

_script_dir = Path(__file__).parent.resolve()
_MATCREATOR_DIR = Path(os.environ.get("MATCREATOR_HOME", str(Path.home() / ".matcreator"))).expanduser()
_KDG_EMBED_ALIASES = {
    "KDG_EMBED_MODEL": "EMBEDDING_MODEL",
    "KDG_EMBED_API_KEY": "LLM_API_KEY",
    "KDG_EMBED_BASE_URL": "LLM_BASE_URL",
}
_LEGACY_ENV_ALIASES = {
    "LLM_API_KEY": "MINIMAX_API_KEY",
    "LLM_BASE_URL": "MINIMAX_API_BASE",
}
_CONFIG_OVERRIDES_PRE_ENV = os.environ.get("MATCREATOR_MODE", "local") == "server"
_USER_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PROTECTED_USER_ENV_KEYS = frozenset({
    "HOME",
    "PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "MATCREATOR_HOME",
    "MATCREATOR_MODE",
    "MATCREATOR_USER_ID",
})

# Priority: explicit env vars (highest) > config.yaml.
# Capture what was set before we apply config.yaml values.
_pre_env = frozenset(os.environ.keys())

# Apply config.yaml values, but do not override vars that were already present
# in the environment before this module was imported unless running as a server worker.
from .config import get_llm_config, get_bohrium_config, get_compute_config, get_env_overrides, load_config  # noqa: E402

_llm_cfg = get_llm_config()
_bohrium_cfg = get_bohrium_config()
_compute_cfg = get_compute_config()

_yaml_to_env: dict[str, str | None] = {
    "LLM_MODEL":            _llm_cfg.get("model"),
    "LLM_API_KEY":          _llm_cfg.get("api_key"),
    "LLM_BASE_URL":         _llm_cfg.get("base_url"),
    "EMBEDDING_MODEL":      _llm_cfg.get("embedding_model"),
    "GRAPH_AGENT_MODEL":    _llm_cfg.get("graph_agent_model"),
    "REVIEW_AGENT_MODEL":   _llm_cfg.get("review_agent_model"),
    "BOHRIUM_USERNAME":     _bohrium_cfg.get("email"),
    "BOHRIUM_PASSWORD":     _bohrium_cfg.get("password"),
    "BOHRIUM_ACCESS_KEY":   _bohrium_cfg.get("access_key"),
    "BOHRIUM_API_URL":      _bohrium_cfg.get("api_url"),
    "BOHRIUM_PROJECT_ID":   str(_bohrium_cfg["project_id"]) if _bohrium_cfg.get("project_id") else None,
    "BOHRIUM_VASP_IMAGE":   _compute_cfg.get("vasp_image"),
    "BOHRIUM_VASP_MACHINE": _compute_cfg.get("vasp_machine"),
    "BOHRIUM_DEEPMD_IMAGE": _compute_cfg.get("deepmd_image"),
    "BOHRIUM_DEEPMD_MACHINE": _compute_cfg.get("deepmd_machine"),
    "DEEPMD_MODEL_PATH":    _compute_cfg.get("deepmd_model_path"),
}

for _env_key, _yaml_val in _yaml_to_env.items():
    if _yaml_val and (_CONFIG_OVERRIDES_PRE_ENV or _env_key not in _pre_env):
        os.environ[_env_key] = _yaml_val

for _env_key, _yaml_val in get_env_overrides().items():
    if not _USER_ENV_KEY_RE.fullmatch(_env_key) or _env_key in _PROTECTED_USER_ENV_KEYS:
        continue
    if _yaml_val and (_CONFIG_OVERRIDES_PRE_ENV or _env_key not in _pre_env):
        os.environ[_env_key] = _yaml_val

for _env_key, _legacy_key in _LEGACY_ENV_ALIASES.items():
    if not os.environ.get(_env_key) and os.environ.get(_legacy_key):
        os.environ[_env_key] = os.environ[_legacy_key]


def _normalize_kdg_embedding_env() -> None:
    """Map MatCreator's generic embedding settings into KDG's embedder env."""
    for kdg_key, source_key in _KDG_EMBED_ALIASES.items():
        if not os.environ.get(kdg_key) and os.environ.get(source_key):
            os.environ[kdg_key] = os.environ[source_key]

    if (
        not os.environ.get("KDG_EMBED_PROVIDER")
        and os.environ.get("KDG_EMBED_MODEL")
    ):
        os.environ["KDG_EMBED_PROVIDER"] = "openai"


_normalize_kdg_embedding_env()

LLM_MODEL: str = os.environ.get("LLM_MODEL", "")
GRAPH_AGENT_MODEL: str = os.environ.get("GRAPH_AGENT_MODEL", LLM_MODEL)
LLM_API_KEY: str = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL: str = os.environ.get("LLM_BASE_URL", "")
BOHRIUM_USERNAME: str = os.environ.get("BOHRIUM_USERNAME", "")
BOHRIUM_PASSWORD: str = os.environ.get("BOHRIUM_PASSWORD", "")
BOHRIUM_PROJECT_ID: int | str = os.environ.get("BOHRIUM_PROJECT_ID", 00000)
BOHRIUM_ACCESS_KEY: str = os.environ.get("BOHRIUM_ACCESS_KEY", "")
BOHRIUM_API_URL: str = os.environ.get("BOHRIUM_API_URL", "")
EXECUTION_ENABLE_WITHIN_INVOCATION_COMPACTION: str|int = os.environ.get("EXECUTION_ENABLE_WITHIN_INVOCATION_COMPACTION", 1)
EXECUTION_COMPACT_KEEP_TAIL: int = int(os.environ.get("EXECUTION_COMPACT_KEEP_TAIL", "10"))
EXECUTION_COMPACT_EVERY_EVENTS: int = int(os.environ.get("EXECUTION_COMPACT_EVERY_EVENTS", "5"))


_AGENT_PATH = _script_dir
_ADK_DIR = _MATCREATOR_DIR / ".adk"     # ADK internal storage (session.db, KDG DB, etc.)
_KNOWLEDGE_PATH= _script_dir / "knowledge"
_SKILLS_DIR = _script_dir / "skills"
_GUIDES_DIR = _script_dir/ "guides"
_MEMORY_PATH = _KNOWLEDGE_PATH /"MEMORY.md"
_PROJECT_ROOT = _AGENT_PATH.parents[1]

# Active Know-Do Graph storage lives under the user-global MatCreator ADK dir.
DEFAULT_KDG_DB_DIR = _ADK_DIR
DEFAULT_KDG_DB_PATH = DEFAULT_KDG_DB_DIR / "know_do_graph.db"

# Unified Know-Do Graph storage. Prefer the ~/.matcreator default unless the
# caller explicitly overrides it with KDG_DB_PATH.
os.environ.setdefault("KDG_DB_PATH", str(DEFAULT_KDG_DB_PATH))
_kdg_db_path = Path(os.environ["KDG_DB_PATH"]).expanduser()
if not _kdg_db_path.is_absolute():
    _kdg_db_path = (_PROJECT_ROOT / _kdg_db_path).resolve()
if _kdg_db_path.suffix != ".db":
    _kdg_db_path = _kdg_db_path / "know_do_graph.db"
KNOW_DO_GRAPH_DB = _kdg_db_path
KNOW_DO_MEMORY_DIR = KNOW_DO_GRAPH_DB.parent / "memory"

# Read-only migration sources from previous storage layouts. New code must not
# write here unless the caller explicitly points KDG_DB_PATH at them.
_LEGACY_REPO_ADK_DIR = _PROJECT_ROOT / "agents" / "MatCreator" / ".adk"
_LEGACY_ADK_DIR = _ADK_DIR
LEGACY_UNIFIED_GRAPH_DB = _LEGACY_REPO_ADK_DIR / "know_do_graph.db"
LEGACY_UNIFIED_MEMORY_DIR = _LEGACY_REPO_ADK_DIR / "memory"
# Older split-graph releases stored separate skill/memory DBs under ~/.matcreator/.adk.
LEGACY_SKILL_GRAPH_DB = _LEGACY_ADK_DIR / "skill_graph.db"
LEGACY_MEMORY_GRAPH_DB = _LEGACY_ADK_DIR / "memory_graph.db"

# Workspace paths — resolved lazily at runtime via workspace.get_workspace_root()
# These are re-exported here for convenience so other modules only need one import.
def _workspace_root() -> "Path":
    from .workspace import get_workspace_root
    return get_workspace_root()

def _workspace_skills_dir() -> "Path":
    return _workspace_root() / "skills"

def _workspace_guides_dir() -> "Path":
    return _workspace_root() / "guides"

def _workspace_memory_path() -> "Path":
    return _workspace_root() / "MEMORY.md"
