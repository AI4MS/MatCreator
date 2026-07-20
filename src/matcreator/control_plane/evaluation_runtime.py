"""Isolated local runtime launcher for one benchmark attempt."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Mapping, Protocol


_CONTROL_PLANE_ONLY_ENV = frozenset({"MAT_BENCH_TOKEN", "MAT_BENCH_SERVER_URL"})


@dataclass(frozen=True)
class RuntimeSpec:
    workspace: Path
    runtime_home: Path
    prompt_path: Path
    session_id: str
    max_turns: int
    timeout_seconds: int
    event_log_path: Path | None = None
    flash: bool = False
    environment: Mapping[str, str] | None = None
    on_managed_run_started: Callable[[str], Awaitable[None]] | None = None


@dataclass(frozen=True)
class RuntimeOutcome:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_seconds: float
    result: dict
    error: str | None = None


class EvaluationRuntime(Protocol):
    async def run(self, spec: RuntimeSpec) -> RuntimeOutcome: ...


class LocalEvaluationRuntimeLauncher:
    """Run one non-interactive MatCreator process in an isolated environment."""

    def __init__(self, command: str = "matcreator") -> None:
        self.command = command

    @staticmethod
    def _result_from_stdout(stdout: str) -> dict:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    async def run(self, spec: RuntimeSpec) -> RuntimeOutcome:
        workspace = spec.workspace.resolve()
        runtime_home = spec.runtime_home.resolve()
        prompt_path = spec.prompt_path.resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        runtime_home.mkdir(parents=True, exist_ok=True)

        environment = dict(os.environ)
        for key in _CONTROL_PLANE_ONLY_ENV:
            environment.pop(key, None)
        environment.update(spec.environment or {})
        for key in _CONTROL_PLANE_ONLY_ENV:
            environment.pop(key, None)
        environment.update(
            {
                "MATCLAW_WORKSPACE": str(workspace),
                "MATCLAW_SESSION_DIR": str(workspace),
                "MATCREATOR_HOME": str(runtime_home),
            }
        )
        command = [
            self.command,
            "run",
            "--workspace",
            str(workspace),
            "--prompt-file",
            str(prompt_path),
            "--session-id",
            spec.session_id,
            "--max-turns",
            str(spec.max_turns),
            "--output-format",
            "json",
        ]
        if spec.flash:
            command.append("--flash")
        if spec.event_log_path is not None:
            command.extend(["--event-log", str(spec.event_log_path.resolve())])

        started_at = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace),
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(), timeout=spec.timeout_seconds
            )
        except asyncio.TimeoutError:
            process.terminate()
            stdout_bytes, stderr_bytes = await process.communicate()
            return RuntimeOutcome(
                exit_code=None,
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                duration_seconds=time.monotonic() - started_at,
                result={},
                error=f"runtime timed out after {spec.timeout_seconds}s",
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        return RuntimeOutcome(
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_seconds=time.monotonic() - started_at,
            result=self._result_from_stdout(stdout),
            error=None if process.returncode == 0 else f"runtime exited with code {process.returncode}",
        )