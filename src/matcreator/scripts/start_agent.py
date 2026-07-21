#!/usr/bin/env python3
"""MatCreator CLI — launch, run, and manage the MatCreator agent.

Usage:
    matcreator [OPTIONS] COMMAND [ARGS]

Core Commands:
    web           Launch the ADK web UI (browser-based chat interface).
    api-server    Launch the ADK API server (used by the Streamlit app).
    run           Run the agent non-interactively on a single prompt.
    chat          Run the agent interactively in the terminal (workspace = CWD by default).

Knowledge Graph Commands (matcreator graph ...):
    serve         Launch the Know-Do Graph HTTP UI/server.
    stats         Show graph-wide statistics (nodes, edges).
    neighbors     Show graph neighbors for a given entry ID.
    export        Export graph entries.
    clear-memory  Delete memory nodes without changing skill nodes.
    reset         Rebuild the graph from installed built-in and custom skills.

Knowledge Management Commands (matcreator knowledge ...):
    query         Search the knowledge graph for nodes matching a query string.
    search-skills Find skill nodes by semantic similarity to a query.
    related-skills Traverse the dependency graph from a known skill node.
    stats         Print knowledge graph and in-memory entry counts.
    seed          Seed the Know-Do Graph from all SKILL.md and guide files.
    migrate       Import legacy skill definitions, memories, and MEMORY.md data.
    distill       Promote frequently successful memory entries into durable knowledge.

Configuration Commands (matcreator config ...):
    set           Set a configuration value (e.g. llm.api_key=sk-xxx).
    get           Print a single configuration value.
    show          Print all current configuration settings.

Skill Commands:
    install official  Install/update corporation-maintained official skills from Git.

Examples:
    matcreator web
    matcreator web --reload-agents --port 8080
    matcreator api-server
    matcreator run -p "Build a silicon FCC structure"
    matcreator run -f prompt.txt --output-format json -o result.json
    matcreator chat
    matcreator chat --workspace ~/my-project
    matcreator chat --session <id>        # resume a previous session
    matcreator graph stats
    matcreator graph neighbors <entry_id>
    matcreator knowledge seed
    matcreator knowledge query "DFT calculation"
    matcreator install official --repo git@gitlab.example.com:group/matcreator-official-skills.git --ref main
    matcreator config set llm.api_key=sk-xxx
    matcreator config show
    MATCLAW_WORKSPACE=/data/ws matcreator run -p "hello"
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
import warnings
from pathlib import Path
from typing import Union
import yaml

import click

from matcreator.ports import get_adk_host, get_adk_port
from matcreator.config import apply_config_env_overrides


_PRE_CONFIG_ENV = frozenset(os.environ.keys())
_MATCREATOR_HOME = Path(os.environ.get("MATCREATOR_HOME", "~/.matcreator")).expanduser()
_CONFIG_PATH = _MATCREATOR_HOME / "config.yaml"
_DEFAULT_ADK_DIR = _MATCREATOR_HOME / ".adk"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _apply_harness_config_env() -> None:
    apply_config_env_overrides(
        override_existing=os.environ.get("MATCREATOR_MODE", "local") == "server",
        pre_env=_PRE_CONFIG_ENV,
    )


def _configure_logging(log_level: str) -> None:
    """Route MatCreator/ADK logger output to stderr for launcher log capture."""
    level_name = (log_level or "info").upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = "%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
    logging.basicConfig(level=level, format=log_format)
    logging.getLogger().setLevel(level)
    logging.getLogger("google_adk").setLevel(level)
    logging.getLogger("matcreator").setLevel(level)


def _make_agent_loader():
    """Return a custom AgentLoader that serves matcreator.agent.app directly.

    This avoids ADK's file-based discovery (which is case-sensitive and tightly
    coupled to a specific directory structure) and ensures session data lands in
    ~/.matcreator/.adk/ regardless of where the CLI is invoked from.
    """
    from google.adk.cli.utils.base_agent_loader import BaseAgentLoader

    class _MatCreatorLoader(BaseAgentLoader):
        def load_agent(self, agent_name: str):
            from matcreator.agent import app
            return app

        def list_agents(self) -> list[str]:
            return ["MatCreator"]

    return _MatCreatorLoader()


def _start_adk_server(
    host: str | None,
    port: int | None,
    log_level: str,
    web_ui: bool,
    reload_agents: bool = False,
    reload: bool = False,
) -> None:
    """Start the ADK server programmatically with controlled session storage."""
    _apply_harness_config_env()
    if host is None:
        host = get_adk_host()
    if port is None:
        port = get_adk_port()

    import uvicorn
    from google.adk.cli.fast_api import get_fast_api_app

    _configure_logging(log_level)

    session_db = _DEFAULT_ADK_DIR / "session.db"
    session_db.parent.mkdir(parents=True, exist_ok=True)

    fast_api = get_fast_api_app(
        agents_dir=str(_DEFAULT_ADK_DIR),   # unused for discovery; loader overrides it
        agent_loader=_make_agent_loader(),
        session_service_uri=f"sqlite:///{session_db}",
        web=web_ui,
        host=host,
        port=port,
        reload_agents=reload_agents,
    )

    config = uvicorn.Config(
        fast_api,
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        pass


# Shared options applied to web/api-server subcommands
_shared_options = [
    click.option("--host", default=None,
                 help="Binding host for the ADK server (default: resolved from config/MATCREATOR_ADK_HOST env)."),
    click.option("--port", type=int, default=None,
                 help="Port for the ADK server (default: resolved from config/MATCREATOR_ADK_PORT env)."),
    click.option("--workspace", default=None, metavar="DIR",
                 help="Override the workspace root directory (also settable via MATCLAW_WORKSPACE)."),
    click.option("--log-level",
                 type=click.Choice(["debug", "info", "warning", "error", "critical"],
                                   case_sensitive=False),
                 default="info", show_default=True,
                 help="Logging level for the ADK server."),
    click.option("-v", "--verbose", is_flag=True, default=False,
                 help="Shortcut for --log-level debug."),
]


def add_shared_options(fn):
    for opt in reversed(_shared_options):
        fn = opt(fn)
    return fn


def _default_workspace() -> Path:
    """Resolve the default workspace using the same logic as workspace.py."""
    env_val = os.environ.get("MATCLAW_WORKSPACE", "")
    if env_val:
        return Path(env_val).expanduser().resolve()

    from matcreator.config import load_config

    cfg_workdir = (load_config().get("workspace") or {}).get("default_workdir") or ""
    if cfg_workdir:
        return Path(cfg_workdir).expanduser().resolve()
    return _MATCREATOR_HOME / "workspace"


def _setup_workspace(workspace: str | None) -> Path:
    """Set MATCLAW_WORKSPACE env var and return the resolved path."""
    if workspace:
        ws_root = Path(workspace).expanduser().resolve()
        os.environ["MATCLAW_WORKSPACE"] = str(ws_root)
    else:
        ws_root = _default_workspace()
    ws_root.mkdir(parents=True, exist_ok=True)
    click.echo(f"Workspace: {ws_root}")
    return ws_root


def _run_with_env(cmd: list[str], env: dict[str, str]) -> None:
    click.echo("Starting: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=True, env=env)
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


def _ensure_project_imports() -> None:
    """Add project root and agents dir to sys.path for imports."""
    for p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "agents")):
        if p not in sys.path:
            sys.path.insert(0, p)


def _resolve_kdg_cli() -> str:
    """Resolve the know-do-graph executable from the current environment."""
    local_bin = Path(sys.executable).resolve().with_name("know-do-graph")
    if local_bin.is_file():
        return str(local_bin)

    found = shutil.which("know-do-graph")
    if found:
        return found

    raise click.ClickException(
        "Could not find the `know-do-graph` executable in the current environment."
    )


def _matcreator_kdg_env() -> dict[str, str]:
    """Return an environment that pins KDG CLI calls to MatCreator's database."""
    _ensure_project_imports()
    from matcreator.constants import KNOW_DO_GRAPH_DB

    env = os.environ.copy()
    env["KDG_DB_PATH"] = str(KNOW_DO_GRAPH_DB)
    return env


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main():
    """MatCreator CLI — launch, run, and manage the MatCreator agent."""


@main.command("web")
@add_shared_options
@click.option("--reload-agents", is_flag=True, default=False,
              help="Enable live reload when agent files change.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload for the FastAPI server.")
def web(host, port, workspace, log_level, verbose, reload_agents, reload):
    """Launch the ADK web UI (browser-based chat interface)."""
    _setup_workspace(workspace)
    _start_adk_server(
        host=host,
        port=port,
        log_level="debug" if verbose else log_level,
        web_ui=True,
        reload_agents=reload_agents,
        reload=reload,
    )


@main.command("api-server")
@add_shared_options
@click.option("--reload-agents", is_flag=True, default=False,
              help="Enable live reload when agent files change.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload for the FastAPI server.")
def api_server(host, port, workspace, log_level, reload_agents, verbose, reload):
    """Launch the ADK API server (used by the Streamlit app)."""
    _setup_workspace(workspace)
    _start_adk_server(
        host=host,
        port=port,
        log_level="debug" if verbose else log_level,
        web_ui=False,
        reload_agents=reload_agents,
        reload=reload,
    )


@main.group(
    "graph",
    context_settings={"ignore_unknown_options": True},
    invoke_without_command=True,
)
@click.option(
    "--memorize-frequency", "--memorize_frequency", "memorize_frequency",
    type=click.IntRange(min=0), default=None, metavar="N",
    help="Persist automatic memorization frequency; 0 disables it.",
)
@click.option(
    "--review-frequency", "--review_frequency", "review_frequency",
    type=click.IntRange(min=0), default=None, metavar="N",
    help="Persist automatic review frequency; 0 disables it.",
)
@click.pass_context
def graph_group(ctx: click.Context, memorize_frequency, review_frequency):
    """Run Know-Do Graph inspection commands against MatCreator's active database."""
    from matcreator.config import set_config_value

    changed = False
    if memorize_frequency is not None:
        set_config_value("knowledge.memorization_frequency", str(memorize_frequency))
        click.echo(f"Set MATCREATOR_MEMORIZATION_FREQUENCY={memorize_frequency}")
        changed = True
    if review_frequency is not None:
        set_config_value("knowledge.review_frequency", str(review_frequency))
        click.echo(f"Set MATCREATOR_REVIEW_FREQUENCY={review_frequency}")
        changed = True
    if ctx.invoked_subcommand is None and not changed:
        click.echo(ctx.get_help())


@graph_group.command(
    "serve",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def graph_serve(ctx: click.Context) -> None:
    """Launch the Know-Do Graph HTTP UI/server using MatCreator's active database."""
    env = _matcreator_kdg_env()
    cmd = [_resolve_kdg_cli(), "serve", *ctx.args]
    _run_with_env(cmd, env)


@graph_group.command(
    "stats",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def graph_stats(ctx: click.Context) -> None:
    """Show graph statistics using MatCreator's configured Know-Do Graph database."""
    env = _matcreator_kdg_env()
    cmd = [_resolve_kdg_cli(), "graph", "stats", *ctx.args]
    _run_with_env(cmd, env)


@graph_group.command(
    "neighbors",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("entry_id", metavar="ENTRY_ID")
@click.pass_context
def graph_neighbors(ctx: click.Context, entry_id: str) -> None:
    """Show graph neighbors for the given ENTRY_ID using MatCreator's active database."""
    env = _matcreator_kdg_env()
    cmd = [_resolve_kdg_cli(), "graph", "neighbors", entry_id, *ctx.args]
    _run_with_env(cmd, env)


@graph_group.command(
    "export",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.pass_context
def graph_export(ctx: click.Context) -> None:
    """Export graph entries using MatCreator's configured Know-Do Graph database."""
    env = _matcreator_kdg_env()
    cmd = [_resolve_kdg_cli(), "graph", "export", *ctx.args]
    _run_with_env(cmd, env)


@graph_group.command("clear-memory")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--no-backup", is_flag=True, help="Do not back up the graph before clearing memory.")
def graph_clear_memory(yes: bool, no_backup: bool) -> None:
    """Delete all memory nodes while preserving the rest of the skill graph."""
    if not yes:
        click.confirm("Delete all memory nodes from the skill graph?", abort=True)
    from matcreator.skill import clear_skill_graph_memory

    try:
        result = clear_skill_graph_memory(create_backup=not no_backup)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if result.get("backup_path"):
        click.echo(f"Backup: {result['backup_path']}")
    click.echo(result["message"])


@graph_group.command("reset")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
@click.option("--no-backup", is_flag=True, help="Do not back up the graph before resetting it.")
def graph_reset(yes: bool, no_backup: bool) -> None:
    """Rebuild the graph from installed built-in and custom skill files."""
    if not yes:
        click.confirm(
            "Remove learned graph data and rebuild from installed skills?",
            abort=True,
        )
    from matcreator.skill import reset_skill_graph

    try:
        result = reset_skill_graph(create_backup=not no_backup)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    if result.get("backup_path"):
        click.echo(f"Backup: {result['backup_path']}")
    click.echo(result["message"])


# ---------------------------------------------------------------------------
# Skill registry management
# ---------------------------------------------------------------------------

def _git(args: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise click.ClickException(detail)
    return proc.stdout.strip()


def _configured_official_repo() -> tuple[str, str]:
    from matcreator.config import load_config

    official_cfg = (load_config().get("skills") or {}).get("official") or {}
    repo = os.environ.get("MATCREATOR_OFFICIAL_SKILLS_REPO") or official_cfg.get("repo") or ""
    ref = os.environ.get("MATCREATOR_OFFICIAL_SKILLS_REF") or official_cfg.get("ref") or "main"
    return str(repo).strip(), str(ref).strip() or "main"


@main.group("install")
def install_group():
    """Install MatCreator managed skill bundles.

    \b
    Available commands:
      official    Clone or update corporation-maintained official skills into
                  ~/.matcreator/skills/official.

    Run `matcreator install official --help` to see all installer arguments:
    --repo, --ref, and --force.
    """


@install_group.command("official")
@click.option(
    "--repo",
    default=None,
    metavar="URL",
    help=(
        "Git repository URL for official skills. If omitted, uses "
        "MATCREATOR_OFFICIAL_SKILLS_REPO or skills.official.repo in ~/.matcreator/config.yaml."
    ),
)
@click.option(
    "--ref",
    "git_ref",
    default=None,
    metavar="REF",
    help=(
        "Branch, tag, or commit to install. If omitted, uses "
        "MATCREATOR_OFFICIAL_SKILLS_REF or skills.official.ref in config.yaml; default: main."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Replace ~/.matcreator/skills/official if it exists but is not a git checkout.",
)
def install_official(repo: str | None, git_ref: str | None, force: bool) -> None:
    """Install or update official skills.

        \b
    Destination:
      ~/.matcreator/skills/official

        \b
    Arguments/options:
      --repo URL   Git repository to clone or fetch from.
      --ref REF    Branch, tag, or commit to checkout.
      --force      Replace an existing non-git official directory.

    Repository authentication is handled by Git itself, for example through SSH
    keys or the user's configured credential helper.
    """
    from matcreator.skill import official_skills_dir, refresh_skills, _discover_skill_dirs

    configured_repo, configured_ref = _configured_official_repo()
    repo = (repo or configured_repo).strip()
    git_ref = (git_ref or configured_ref).strip() or "main"
    if not repo:
        raise click.UsageError(
            "Official skills repo is required. Pass --repo or set skills.official.repo in config.yaml."
        )

    target = official_skills_dir().expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not (target / ".git").is_dir():
        if not force:
            raise click.ClickException(
                f"{target} exists but is not a git checkout. Re-run with --force to replace it."
            )
        shutil.rmtree(target)

    if not target.exists():
        click.echo(f"Cloning official skills from {repo} into {target}")
        _git(["clone", repo, str(target)])
    else:
        click.echo(f"Updating official skills in {target}")
    _git(["fetch", "--tags", "--prune", "origin", git_ref], cwd=target)
    _git(["checkout", "--detach", "FETCH_HEAD"], cwd=target)

    commit = _git(["rev-parse", "--short", "HEAD"], cwd=target)
    skill_dirs = _discover_skill_dirs(target)
    refresh_result = refresh_skills()
    click.echo(f"Installed {len(skill_dirs)} official skills at {commit}.")
    click.echo(f"Registry now has {refresh_result['count']} skills.")


# ---------------------------------------------------------------------------
# Non-interactive runner core (used by `run` and benchmarks)
# ---------------------------------------------------------------------------

async def run_agent_async(
    workspace_dir: str,
    prompt: str,
    max_turns: int = 50,
    session_id: str | None = None,
    flash: bool = False,
    event_log: str | None = None,
) -> dict:
    """Run the agent non-interactively on a single prompt.

    Returns a dict with keys: answer, model_name, num_turns, is_error,
    duration_ms, num_events.
    """
    os.environ["MATCLAW_WORKSPACE"] = str(Path(workspace_dir).resolve())

    from google.adk.runners import Runner
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.genai import types

    from matcreator.agent import app

    _adk_db = _DEFAULT_ADK_DIR / "session.db"
    _adk_db.parent.mkdir(parents=True, exist_ok=True)
    session_service = SqliteSessionService(db_path=str(_adk_db))
    runner = Runner(app=app, session_service=session_service)

    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="user",
        state={"agent_mode": "flash" if flash else "bench"},
        session_id=session_id,
    )

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    start_ms = time.monotonic_ns() // 1_000_000
    events = []
    event_log_handle = None
    if event_log:
        event_log_path = Path(event_log).expanduser().resolve()
        event_log_path.parent.mkdir(parents=True, exist_ok=True)
        event_log_handle = event_log_path.open("a", encoding="utf-8")
    turn_count = 0
    is_error = False
    error_msg = ""

    try:
        async for event in runner.run_async(
            user_id="user",
            session_id=session.id,
            new_message=user_message,
        ):
            events.append(event)
            if event_log_handle is not None:
                try:
                    payload = event.model_dump(mode="json", exclude_none=True)
                except AttributeError:
                    payload = {"text": str(event)}
                event_log_handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
                event_log_handle.flush()
            if hasattr(event, "is_final_response") and event.is_final_response():
                turn_count += 1
            if turn_count >= max_turns:
                break
    except Exception as exc:
        is_error = True
        error_msg = str(exc)
    finally:
        if event_log_handle is not None:
            event_log_handle.close()

    duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

    answer = ""
    for event in reversed(events):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    answer = part.text
                    break
            if answer:
                break

    if is_error and not answer:
        answer = error_msg

    return {
        "answer": answer,
        "model_name": os.environ.get("LLM_MODEL", "matcreator"),
        "num_turns": turn_count,
        "is_error": is_error,
        "duration_ms": duration_ms,
        "num_events": len(events),
    }


# ---------------------------------------------------------------------------
# `matcreator run` subcommand
# ---------------------------------------------------------------------------

@main.command("run")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory (also settable via MATCLAW_WORKSPACE).")
@click.option("-p", "--prompt", "prompt_text", default=None,
              help="Inline prompt text to send to the agent.")
@click.option("-f", "--prompt-file", default=None, type=click.Path(exists=True),
              help="Read prompt from a file (mutually exclusive with -p).")
@click.option("--output-format", "output_format",
              type=click.Choice(["text", "json"], case_sensitive=False),
              default="text", show_default=True,
              help="Output format: 'text' prints the answer, 'json' prints full structured result.")
@click.option("-o", "--output", "output_file", default=None, type=click.Path(),
              help="Write output to a file instead of stdout.")
@click.option("--max-turns", default=50, show_default=True, type=int,
              help="Maximum agent turns before stopping.")
@click.option("--session-id", default=None, metavar="ID",
              help="Custom session ID (default: auto-generated by ADK).")
@click.option("--flash", is_flag=True, default=False,
              help="Run in Flash mode: thinking agent executes directly, no DAG or approval required.")
@click.option("--event-log", default=None, type=click.Path(), metavar="FILE",
              help="Append each ADK event as JSONL while the run is active.")
def run_cmd(workspace, prompt_text, prompt_file, output_format, output_file, max_turns, session_id, flash, event_log):
    """Run the agent non-interactively on a single prompt and print the result.

    Provide a prompt via -p/--prompt (inline text) or -f/--prompt-file (path to
    a file).  Use --output-format json for structured output or -o to write
    to a file.
    """
    if prompt_text and prompt_file:
        raise click.UsageError("Use either -p/--prompt or -f/--prompt-file, not both.")
    if not prompt_text and not prompt_file:
        raise click.UsageError("Provide a prompt via -p/--prompt or -f/--prompt-file.")

    if prompt_file:
        prompt_text = Path(prompt_file).read_text().strip()

    ws_root = Path(workspace).expanduser().resolve() if workspace else _default_workspace()
    ws_root.mkdir(parents=True, exist_ok=True)

    result = asyncio.run(run_agent_async(str(ws_root), prompt_text, max_turns, session_id, flash, event_log))

    if output_format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        content = result["answer"]

    if output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(content)
    else:
        click.echo(content)


# ---------------------------------------------------------------------------
# `matcreator chat` subcommand — interactive terminal REPL
# ---------------------------------------------------------------------------

async def _chat_loop(workspace_dir: Path, session_id: str, agent_mode: str = "flash") -> None:
    """Run the interactive agent REPL."""
    from google.adk.runners import Runner
    from google.adk.sessions.sqlite_session_service import SqliteSessionService
    from google.genai import types

    from matcreator.agent import app
    from matcreator.workspace import init_workspace

    init_workspace()

    _adk_db = _DEFAULT_ADK_DIR / "session.db"
    _adk_db.parent.mkdir(parents=True, exist_ok=True)
    session_service = SqliteSessionService(db_path=str(_adk_db))
    runner = Runner(app=app, session_service=session_service)

    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="user",
        state={"agent_mode": agent_mode},
        session_id=session_id,
    )
    actual_id = session.id

    mode_label = "Flash" if agent_mode == "flash" else "Plan"
    click.echo(f"\nMatCreator chat  |  workspace: {workspace_dir}  |  mode: {mode_label}")
    click.echo(f"Session: {actual_id}")
    click.echo("Type /quit or /exit to end.  /session shows the session ID.  /workspace shows the path.")
    click.echo("─" * 60)

    while True:
        try:
            user_input = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo(f"\n\nSession saved.  Resume with: matcreator chat --session {actual_id}")
            break

        if not user_input:
            continue

        if user_input in ("/quit", "/exit", "quit", "exit"):
            click.echo(f"\nSession saved.  Resume with: matcreator chat --session {actual_id}")
            break
        if user_input == "/session":
            click.echo(f"Session ID: {actual_id}")
            continue
        if user_input == "/workspace":
            click.echo(f"Workspace: {workspace_dir}")
            continue

        user_message = types.Content(
            role="user",
            parts=[types.Part(text=user_input)],
        )

        click.echo("\nMatCreator> ", nl=False)
        answer_parts: list[str] = []
        try:
            async for event in runner.run_async(
                user_id="user",
                session_id=actual_id,
                new_message=user_message,
            ):
                if event.content and event.content.parts:
                    for part in event.content.parts:
                        if hasattr(part, "text") and part.text:
                            click.echo(part.text, nl=False)
                            answer_parts.append(part.text)
        except KeyboardInterrupt:
            click.echo("\n[interrupted]")
            continue
        except Exception as exc:
            click.echo(f"\n[error: {exc}]")
            continue

        if answer_parts:
            click.echo()  # newline after streamed answer


@main.command("chat")
@click.option("--workspace", default=None, metavar="DIR",
              help="Workspace directory (defaults to current working directory).")
@click.option("--session", "session_id", default=None, metavar="SESSION_ID",
              help="Resume an existing session by ID.")
@click.option("--plan", "use_plan", is_flag=True, default=False,
              help="Use standard plan mode (thinking + DAG execution) instead of the default Flash mode.")
def chat_cmd(workspace, session_id, use_plan):
    """Run the agent interactively in the terminal.

    Defaults to Flash mode for fast, direct responses.  Pass --plan to use
    the full planning + execution pipeline instead.

    The workspace defaults to the current working directory, making it easy
    to use MatCreator directly from any project folder.  All session state
    is stored in ~/.matcreator/.adk/.  Resume a previous session with
    --session <id>.
    """
    if workspace:
        ws_root = Path(workspace).expanduser().resolve()
    else:
        ws_root = Path.cwd()

    ws_root.mkdir(parents=True, exist_ok=True)
    os.environ["MATCLAW_WORKSPACE"] = str(ws_root)
    warnings.filterwarnings("ignore", category=UserWarning)

    agent_mode = "normal" if use_plan else "flash"
    sid = session_id or str(uuid.uuid4())
    asyncio.run(_chat_loop(ws_root, sid, agent_mode))


# ---------------------------------------------------------------------------
# `matcreator knowledge` subcommand group
# ---------------------------------------------------------------------------

@main.group("knowledge")
def knowledge_group():
    """Inspect and manage the knowledge graph."""


def _ensure_workspace(workspace: str | None) -> None:
    if workspace:
        ws_root = Path(workspace).expanduser().resolve()
        os.environ["MATCLAW_WORKSPACE"] = str(ws_root)
        ws_root.mkdir(parents=True, exist_ok=True)


@knowledge_group.command("query")
@click.argument("text", metavar="TEXT")
@click.option("--top-k", default=15, show_default=True, type=int,
              help="Maximum number of nodes to return.")
@click.option("--depth", default=2, show_default=True, type=int,
              help="BFS expansion depth from seed nodes.")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_query(text, top_k, depth, workspace):
    """Query the memory knowledge graph for nodes matching TEXT.

    TEXT is a free-form query string that is matched against node content
    using semantic similarity.  Increase --depth to explore further from
    the initial matches.
    """
    _ensure_workspace(workspace)
    from matcreator.knowledge.query import query_knowledge_graph
    result = query_knowledge_graph(text, depth=depth, top_k=top_k)
    click.echo(result)


@knowledge_group.command("search-skills")
@click.argument("text", metavar="TEXT")
@click.option("--top-k", default=5, show_default=True, type=int,
              help="Maximum number of skills to return.")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_search_skills(text, top_k, workspace):
    """Search for skill nodes semantically matching TEXT.

    TEXT is matched against skill names and descriptions using embedding
    similarity.  Use this to discover skills relevant to a task or concept.
    """
    _ensure_workspace(workspace)
    from matcreator.knowledge.query import search_skills
    result = search_skills(text, top_k=top_k)
    click.echo(result)


@knowledge_group.command("related-skills")
@click.argument("start_node", metavar="START_NODE")
@click.option("--top-k", default=5, show_default=True, type=int,
              help="Maximum number of related skills to return.")
@click.option("--depth", default=2, show_default=True, type=int,
              help="BFS depth limit.")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_related_skills(start_node, top_k, depth, workspace):
    """Traverse the dependency graph from START_NODE to find related skills.

    START_NODE is the name of a known skill (e.g. "deepmd" or "lammps").
    The command follows dependency edges outward to discover skills that are
    related through shared concepts, tools, or workflows.
    """
    _ensure_workspace(workspace)
    from matcreator.knowledge.query import get_related_skills
    result = get_related_skills(start_node, top_k=top_k, depth=depth)
    click.echo(result)


@knowledge_group.command("stats")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_stats(workspace):
    """Print node and edge counts for the knowledge graph and in-memory entries."""
    _ensure_workspace(workspace)
    from matcreator.knowledge.kdg_memory import iter_memory
    from matcreator.knowledge.query import _get_kg
    graph = _get_kg()
    stats = graph.stats()
    memory_count = 0
    unpromoted_count = 0
    for memory in iter_memory(graph):
        memory_count += 1
        unpromoted_count += int(not memory.promoted)
    click.echo("Know-Do graph (all native nodes):")
    click.echo(f"  Nodes: {stats['nodes']}  Edges: {stats['edges']}")
    click.echo("MemGraph nodes:")
    click.echo(
        f"  Entries: {memory_count}  "
        f"Unpromoted: {unpromoted_count}"
    )


@knowledge_group.command("seed")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_seed(workspace):
    """Seed Know-Do Graph with all SKILL.md and guide entries."""
    _ensure_workspace(workspace)
    from matcreator.skill import seed_skills_to_graph
    result = seed_skills_to_graph()
    click.echo(
        f"Created {result['seeded']} entries and "
        f"{result['edges_created']} dependency edges."
    )


@knowledge_group.command("migrate")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
@click.option("--memory-md", default=None, metavar="FILE",
              help="Also import a legacy MEMORY.md file.")
def knowledge_migrate(workspace, memory_md):
    """Migrate legacy skill, memory, and optional MEMORY.md data."""
    _ensure_workspace(workspace)
    from matcreator.constants import KNOW_DO_GRAPH_DB
    from matcreator.knowledge.kdg_memory import iter_memory
    from matcreator.knowledge.migrate import migrate_memory_md
    from matcreator.knowledge.query import _get_kg, get_migration_result

    result = get_migration_result()
    click.echo(
        "Newly migrated from legacy sources: "
        f"{result.get('know_do_nodes', 0)} durable entries, "
        f"{result.get('memory_entries', 0)} memories, "
        f"{result.get('edges', 0)} edges."
    )
    if memory_md:
        md_result = migrate_memory_md(memory_md)
        click.echo(md_result["message"])

    graph = _get_kg()
    stats = graph.stats()
    memory_count = sum(1 for _ in iter_memory(graph))
    click.echo(f"Database: {KNOW_DO_GRAPH_DB}")
    click.echo(
        f"Current graph: {stats['nodes']} entries "
        f"({memory_count} memories), {stats['edges']} edges."
    )


@knowledge_group.command("distill")
@click.option("--min-evidence", default=3, show_default=True, type=int,
              help="Minimum similar memories required for promotion.")
@click.option("--stale-days", default=30, show_default=True, type=int,
              help="Age threshold for pruning unsuccessful memory.")
@click.option("--workspace", default=None, metavar="DIR",
              help="Override the workspace root directory.")
def knowledge_distill(min_evidence, stale_days, workspace):
    """Promote repeated successful memory into durable Know-Do entries."""
    _ensure_workspace(workspace)
    from matcreator.knowledge.synthesizer import run_knowledge_synthesizer
    result = run_knowledge_synthesizer(
        stale_days=stale_days,
        min_insights_for_workflow=min_evidence,
    )
    click.echo(result["message"])


# ---------------------------------------------------------------------------
# `matcreator config` subcommand group
# ---------------------------------------------------------------------------

_SENSITIVE_CONFIG_KEYS = frozenset({"llm.api_key", "bohrium.password"})


def _mask(key: str, value: str) -> str:
    return "***" if key in _SENSITIVE_CONFIG_KEYS and value else value


@main.group("config")
def config_group():
    """Read and write ~/.matcreator/config.yaml settings."""


@config_group.command("set")
@click.argument("assignment", metavar="KEY=VALUE")
def config_set(assignment: str):
    """Set a configuration value in ~/.matcreator/config.yaml.

    KEY uses dotted notation (section.field) and is separated from VALUE
    by an equals sign, e.g. ``llm.api_key=sk-xxx``.
    """
    if "=" not in assignment:
        raise click.UsageError("Expected KEY=VALUE format (e.g. llm.api_key=sk-xxx)")
    key, _, value = assignment.partition("=")
    key = key.strip()
    value = value.strip()
    if not key:
        raise click.UsageError("Key must not be empty.")
    from matcreator.config import set_config_value
    set_config_value(key, value)
    display = _mask(key, value)
    click.echo(f"Set {key} = {display}")


@config_group.command("get")
@click.argument("key", metavar="KEY")
@click.option("--reveal", is_flag=True, default=False, help="Show secret values in plain text.")
def config_get(key: str, reveal: bool):
    """Print the value of KEY from ~/.matcreator/config.yaml.

    Sensitive values (API keys, passwords) are masked by default.
    Use --reveal to display them in plain text.
    """
    from matcreator.config import get_config_value
    value = get_config_value(key)
    display = value if reveal else _mask(key, value)
    if display:
        click.echo(display)
    else:
        click.echo("(not set)", err=True)
        raise SystemExit(1)


@config_group.command("show")
@click.option("--reveal-secrets", is_flag=True, default=False,
              help="Show API keys and passwords in plain text.")
def config_show(reveal_secrets: bool):
    """Print all settings from ~/.matcreator/config.yaml."""
    from matcreator.config import load_config, SENSITIVE_YAML_KEYS
    cfg = load_config()
    if not cfg:
        click.echo("No config file found at ~/.matcreator/config.yaml")
        return

    def _mask_cfg(section: str, key: str, value: object) -> object:
        dotted = f"{section}.{key}"
        if not reveal_secrets and dotted in SENSITIVE_YAML_KEYS and value:
            return "***"
        return value

    for section, content in cfg.items():
        if isinstance(content, dict):
            click.echo(f"{section}:")
            for k, v in content.items():
                click.echo(f"  {k}: {_mask_cfg(section, k, v)}")
        else:
            click.echo(f"{section}: {content}")


if __name__ == "__main__":
    main()
