"""Shared subprocess boundary for `bohr`-CLI-backed provider adapters.

Both ``bohr_sandbox`` (interactive) and ``bohr_job`` (batch) adapters shell
out to the same ``bohr`` binary and expect the same JSON envelope
(``{"ok": bool, "data": ..., "error": {...}}``), so the invocation and
error-handling logic lives here once instead of being duplicated per adapter.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any


class BohrCLIError(RuntimeError):
    """Raised when a `bohr` CLI invocation fails or returns unusable output.

    Bohrium's JSON envelope includes lifecycle fields needed to make describe
    and delete idempotent. Preserve them while retaining the normal exception
    string for existing callers.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        subtype: str | None = None,
        http_status: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.subtype = subtype
        self.http_status = http_status
        self.retryable = retryable


def resolve_bohr_binary(preferred_env: str | None = None) -> str:
    """Resolve a `bohr` executable, honoring a provider-specific override."""
    return (
        (os.environ.get(preferred_env) if preferred_env else None)
        or os.environ.get("BOHR_CLI_PATH")
        or shutil.which("bohr")
        or "bohr"
    )


def run_bohr_json(args: list[str], *, timeout: float | None = 120) -> Any:
    """Run one `bohr` CLI invocation and return its parsed ``data`` payload.

    Every invocation appends ``-o json --no-interactive -y`` so output is
    machine-parseable and no command blocks on an interactive confirmation
    prompt. Raises :class:`BohrCLIError` with the CLI's own error message on
    failure, so callers never need to parse stderr or exit codes themselves.
    """
    command_group = args[0] if args else ""
    provider_env = {
        "sandbox": "BOHR_SANDBOX_CLI_PATH",
        "job": "BOHR_JOB_CLI_PATH",
    }.get(command_group)
    command = [
        resolve_bohr_binary(provider_env),
        *args,
        "-o",
        "json",
        "--no-interactive",
        "-y",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
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
        raise BohrCLIError(
            message or f"bohr {' '.join(args)} failed",
            code=(str(error.get("code")) if isinstance(error, dict) and error.get("code") else None),
            subtype=(
                str(error.get("subtype"))
                if isinstance(error, dict) and error.get("subtype")
                else None
            ),
            http_status=(
                int(error.get("http"))
                if isinstance(error, dict) and error.get("http") is not None
                else None
            ),
            retryable=(
                bool(error.get("retryable"))
                if isinstance(error, dict) and error.get("retryable") is not None
                else None
            ),
        )
    return payload.get("data")


def extract_id(data: Any, keys: tuple[str, ...]) -> str | None:
    """Return the first present, truthy value among ``keys`` in a dict payload."""
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if value:
                return str(value)
    return None
