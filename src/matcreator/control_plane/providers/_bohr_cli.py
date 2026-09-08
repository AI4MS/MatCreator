"""Shared subprocess boundary for `bohr`-CLI-backed provider adapters.

Both ``bohr_sandbox`` (interactive) and ``bohr_batchjob`` (batch) adapters shell
out to the same ``bohr`` binary and expect the same JSON envelope
(``{"ok": bool, "data": ..., "error": {...}}``), so the invocation and
error-handling logic lives here once instead of being duplicated per adapter.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class BohrCLIError(RuntimeError):
    """Raised when a `bohr` CLI invocation fails or returns unusable output."""


def resolve_bohr_binary() -> str:
    """Resolve the `bohr` executable, honoring an explicit override."""
    return os.environ.get("BOHR_CLI_PATH") or shutil.which("bohr") or "bohr"


def run_bohr_json(
    args: list[str], *, timeout: float | None = 120, cwd: str | Path | None = None
) -> Any:
    """Run one `bohr` CLI invocation and return its parsed ``data`` payload.

    Every invocation appends ``-o json --no-interactive -y`` so output is
    machine-parseable and no command blocks on an interactive confirmation
    prompt. Raises :class:`BohrCLIError` with the CLI's own error message on
    failure, so callers never need to parse stderr or exit codes themselves.
    ``cwd`` pins the CLI's working directory so relative paths in ``args``
    (e.g. ``--input``) resolve against the caller's chosen root rather than
    the server process cwd.
    """
    command = [resolve_bohr_binary(), *args, "-o", "json", "--no-interactive", "-y"]
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "check": False,
    }
    if cwd is not None:
        # Checked here because subprocess.run maps a missing cwd to the same
        # FileNotFoundError as a missing binary, which would be misreported
        # below as the CLI not being installed.
        if not Path(cwd).is_dir():
            raise BohrCLIError(f"bohr working directory does not exist: {cwd}")
        run_kwargs["cwd"] = str(cwd)
    try:
        completed = subprocess.run(command, **run_kwargs)
    except FileNotFoundError as exc:
        raise BohrCLIError("The 'bohr' CLI is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise BohrCLIError(f"bohr {' '.join(args)} timed out after {timeout}s") from exc

    stdout = (completed.stdout or "").strip()
    if not stdout:
        message = (completed.stderr or "").strip()
        raise BohrCLIError(
            message or f"bohr {' '.join(args)} produced no output (exit {completed.returncode})"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BohrCLIError(
            f"bohr {' '.join(args)} returned non-JSON output: {stdout[:500]}"
        ) from exc

    if not isinstance(payload, dict) or not payload.get("ok", False):
        error = (payload or {}).get("error") if isinstance(payload, dict) else None
        message = (error or {}).get("message") if isinstance(error, dict) else None
        raise BohrCLIError(message or f"bohr {' '.join(args)} failed")
    return payload.get("data")


def extract_id(data: Any, keys: tuple[str, ...]) -> str | None:
    """Return the first present, truthy value among ``keys`` in a dict payload."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value:
                return str(value)
    return None
