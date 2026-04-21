#!/usr/bin/env python3
"""MatCreator CLI.

Usage:
    matcreator [OPTIONS] COMMAND [ARGS]

Commands:
    init        Set the project root path (saved to ~/.matcreator/config.yaml).
    web         Launch the ADK web UI.
    api-server  Launch the ADK API server (used by the Streamlit app).
    run         Run the agent non-interactively on a single prompt.

Examples:
    matcreator init .
    matcreator init /path/to/PFD-Agent
    matcreator web
    matcreator web --reload-agents --port 8080
    matcreator api-server
    matcreator run -p "Build a silicon FCC structure"
    matcreator run -f prompt.txt --output-format json -o result.json
    MATCLAW_WORKSPACE=/data/ws matcreator run -p "hello"
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import yaml
import click

_CONFIG_PATH = Path("~/.matcreator/config.yaml").expanduser()


def _resolve_project_root() -> tuple[Path, bool]:
    """Resolve the MatCreator project root.

    Resolution order (first match wins):
      1. ``MATCREATOR`` environment variable
      2. ``project_root`` in ``~/.matcreator/config.yaml``
      3. Fallback: derive from ``__file__`` (works for editable / source installs)

    Returns (path, explicitly_configured).
    """
    # 1. Environment variable
    env_val = os.environ.get("MATCREATOR")
    if env_val:
        return Path(env_val).expanduser().resolve(), True

    # 2. Config file
    if _CONFIG_PATH.is_file():
        with open(_CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}
        cfg_val = cfg.get("project_root")
        if cfg_val:
            return Path(cfg_val).expanduser().resolve(), True

    # 3. Fallback: __file__-based (src/matcreator/scripts/start_agent.py → repo root)
    return Path(__file__).resolve().parent.parent.parent.parent, False


PROJECT_ROOT, _PROJECT_ROOT_CONFIGURED = _resolve_project_root()
AGENTS_DIR = PROJECT_ROOT / "agents"


def _warn_if_unconfigured() -> None:
    """Emit a warning when the project root was not explicitly configured."""
    if not _PROJECT_ROOT_CONFIGURED:
        click.secho(
            "Warning: project root is not configured. "
            "Using fallback path: " + str(PROJECT_ROOT),
            fg="yellow", err=True,
        )
        click.secho(
            "Run 'matcreator init <project-dir>' or set the MATCREATOR "
            "environment variable.",
            fg="yellow", err=True,
        )

# Shared options applied to both subcommands
_shared_options = [
    click.option("--host", default="127.0.0.1", show_default=True,
                 help="Binding host for the ADK server."),
    click.option("--port", default=8000, show_default=True, type=int,
                 help="Port for the ADK server."),
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


def _resolve_workspace() -> Path:
    """Resolve the workspace root using the same logic as workspace.py."""
    env_val = os.environ.get("MATCLAW_WORKSPACE", "")
    if env_val:
        return Path(env_val).expanduser().resolve()
    return AGENTS_DIR / "MatCreator" / ".workspace"


def _setup_workspace(workspace: str | None) -> Path:
    """Resolve, create, and chdir into the workspace root."""
    if workspace:
        ws_root = Path(workspace).expanduser().resolve()
        os.environ["MATCLAW_WORKSPACE"] = str(ws_root)
    else:
        ws_root = _resolve_workspace()

    ws_root.mkdir(parents=True, exist_ok=True)
    click.echo(f"Workspace : {ws_root}")
    click.echo(f"Agents dir: {AGENTS_DIR}")
    os.chdir(ws_root)
    click.echo(f"Working directory changed to: {ws_root}\n")
    return ws_root


def _run(cmd: list[str]) -> None:
    click.echo("Starting: " + " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def main():
    """MatCreator CLI — manage and run the MatCreator agent."""


@main.command("init")
@click.argument("project_dir", default=".", type=click.Path(exists=True, file_okay=False))
def init_cmd(project_dir):
    """Set the project root path (saved to ~/.matcreator/config.yaml).

    PROJECT_DIR defaults to the current directory. The path must contain an
    ``agents/`` subdirectory.

    \b
    Examples:
        matcreator init .
        matcreator init /home/user/dev/PFD-Agent
    """
    root = Path(project_dir).expanduser().resolve()
    agents = root / "agents"
    if not agents.is_dir():
        raise click.ClickException(
            f"'{root}' does not look like a MatCreator project "
            f"(missing agents/ directory)."
        )

    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    cfg = {}
    if _CONFIG_PATH.is_file():
        with open(_CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}

    cfg["project_root"] = str(root)

    with open(_CONFIG_PATH, "w") as fh:
        yaml.safe_dump(cfg, fh, default_flow_style=False)

    click.echo(f"Project root saved to {_CONFIG_PATH}")
    click.echo(f"  project_root: {root}")


@main.command("web")
@add_shared_options
@click.option("--reload-agents", is_flag=True, default=False,
              help="Enable live reload when agent files change.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload for the FastAPI server.")
def web(host, port, workspace, log_level, verbose, reload_agents, reload):
    """Launch the ADK web UI (browser-based chat interface)."""
    _warn_if_unconfigured()
    _setup_workspace(workspace)

    cmd = [
        "adk", "web",
        str(AGENTS_DIR),
        "--host", host,
        "--port", str(port),
        "--log_level", "debug" if verbose else log_level,
    ]
    if reload_agents:
        cmd.append("--reload_agents")
    if reload:
        cmd.append("--reload")

    _run(cmd)


@main.command("api-server")
@add_shared_options
@click.option("--reload-agents", is_flag=True, default=False,
              help="Enable live reload when agent files change.")
@click.option("--reload", is_flag=True, default=False,
              help="Enable auto-reload for the FastAPI server.")
def api_server(host, port, workspace, log_level, reload_agents,
               verbose, reload):
    """Launch the ADK API server (used by the Streamlit app)."""
    _warn_if_unconfigured()
    _setup_workspace(workspace)

    cmd = [
        "adk", "api_server",
        str(AGENTS_DIR),
        "--host", host,
        "--port", str(port),
        "--log_level", "debug" if verbose else log_level,
    ]
    if reload:
        cmd.append("--reload")
        
    if reload_agents:
        cmd.append("--reload_agents")

    _run(cmd)


# ---------------------------------------------------------------------------
# Non-interactive runner core (used by `run` subcommand and benchmarks)
# ---------------------------------------------------------------------------

async def run_agent_async(
    workspace_dir: str,
    prompt: str,
    max_turns: int = 50,
    session_dir: str | None = None,
) -> dict:
    """Run the agent non-interactively on a single prompt.

    Returns a dict with keys: answer, model_name, num_turns, is_error,
    duration_ms, num_events.
    """
    os.environ["MATCLAW_WORKSPACE"] = str(Path(workspace_dir).resolve())
    if session_dir:
        os.environ["MATCLAW_SESSION_DIR"] = str(Path(session_dir).resolve())

    from google.adk.runners import InMemoryRunner
    from google.genai import types

    # Make the top-level `agents` package importable.
    _project_root = str(PROJECT_ROOT)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    from agents.MatCreator.agent import app

    runner = InMemoryRunner(app=app)

    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id="cli",
        state={"benchmark_mode": True},
    )

    user_message = types.Content(
        role="user",
        parts=[types.Part(text=prompt)],
    )

    start_ms = time.monotonic_ns() // 1_000_000
    events = []
    turn_count = 0
    is_error = False
    error_msg = ""

    try:
        async for event in runner.run_async(
            user_id="cli",
            session_id=session.id,
            new_message=user_message,
        ):
            events.append(event)
            if hasattr(event, "is_final_response") and event.is_final_response():
                turn_count += 1
            if turn_count >= max_turns:
                break
    except Exception as exc:
        is_error = True
        error_msg = str(exc)

    duration_ms = (time.monotonic_ns() // 1_000_000) - start_ms

    # Extract final answer from the last event with text content
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
@click.option("--session-dir", default=None, metavar="DIR",
              help="Override session directory (also settable via MATCLAW_SESSION_DIR).")
def run_cmd(workspace, prompt_text, prompt_file, output_format, output_file, max_turns, session_dir):
    """Run the agent non-interactively on a single prompt."""
    _warn_if_unconfigured()
    if prompt_text and prompt_file:
        raise click.UsageError("Use either -p/--prompt or -f/--prompt-file, not both.")
    if not prompt_text and not prompt_file:
        raise click.UsageError("Provide a prompt via -p/--prompt or -f/--prompt-file.")

    if prompt_file:
        prompt_text = Path(prompt_file).read_text().strip()

    ws_root = Path(workspace).expanduser().resolve() if workspace else _resolve_workspace()
    ws_root.mkdir(parents=True, exist_ok=True)

    result = asyncio.run(run_agent_async(str(ws_root), prompt_text, max_turns, session_dir=session_dir))

    if output_format == "json":
        content = json.dumps(result, ensure_ascii=False, indent=2)
    else:
        content = result["answer"]

    if output_file:
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)
        Path(output_file).write_text(content)
    else:
        click.echo(content)


if __name__ == "__main__":
    main()
