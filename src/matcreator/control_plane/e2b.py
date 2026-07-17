"""E2B sandbox adapter used by control-plane remote-job services."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class E2BConfigurationError(ValueError):
    """Raised when a sandbox request lacks required E2B configuration."""


class E2BUnavailableError(RuntimeError):
    """Raised when the optional E2B SDK is unavailable at runtime."""


@dataclass(frozen=True)
class E2BSandboxSpec:
    """Validated inputs for creating one E2B sandbox."""

    template: str
    api_key: str
    api_url: str
    project_id: str
    timeout: int = 600
    lifecycle: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, str] = field(default_factory=dict)

    def create_kwargs(self) -> dict[str, Any]:
        if not self.template or not self.api_key or not self.api_url or not self.project_id:
            raise E2BConfigurationError(
                "template, api_key, api_url, and project_id are required for E2B"
            )
        if self.timeout < 1:
            raise E2BConfigurationError("timeout must be positive")
        return {
            "template": self.template,
            "api_key": self.api_key,
            "api_url": self.api_url,
            "timeout": self.timeout,
            "lifecycle": self.lifecycle,
            "headers": {"X-Project-Id": self.project_id},
            "metadata": self.metadata,
        }

# The backend E2B SDK is imported lazily to avoid a hard dependency on the SDK for users who don't need it. The E2BSandboxAdapter class wraps the SDK and provides a simple interface for creating, connecting to, and managing E2B sandboxes.
class E2BSandboxAdapter:
    """Small boundary around the E2B SDK with no SDK import at module load."""

    @staticmethod
    def _sandbox_class():
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            raise E2BUnavailableError(
                "e2b-code-interpreter is required for E2B remote jobs"
            ) from exc
        return Sandbox

    def create(self, spec: E2BSandboxSpec) -> str:
        sandbox = self._sandbox_class().create(**spec.create_kwargs())
        sandbox_id = getattr(sandbox, "sandbox_id", "")
        if not sandbox_id:
            raise RuntimeError("E2B create returned a sandbox without sandbox_id")
        return str(sandbox_id)

    def run_command(self, sandbox_id: str, command: str, *, user: str = "root") -> dict[str, Any]:
        sandbox = self._connect(sandbox_id)
        result = sandbox.commands.run(command, user=user, timeout=0)
        return {
            "stdout": str(getattr(result, "stdout", "")),
            "stderr": str(getattr(result, "stderr", "")),
            "exit_code": getattr(result, "exit_code", None),
        }

    def upload_file(self, sandbox_id: str, source: str | Path, destination: str) -> None:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        sandbox = self._connect(sandbox_id)
        with source_path.open("rb") as file_handle:
            sandbox.files.write(destination, file_handle)

    def pause(self, sandbox_id: str) -> None:
        self._connect(sandbox_id).pause()

    def terminate(self, sandbox_id: str) -> None:
        self._connect(sandbox_id).kill()

    def probe(self, sandbox_id: str) -> dict[str, Any]:
        """Confirm an active sandbox is reachable without changing its files."""
        result = self.run_command(sandbox_id, "true")
        if result["exit_code"] not in (0, None):
            raise RuntimeError(result["stderr"] or "E2B sandbox liveness probe failed")
        return {"provider_status": "reachable", "probe": result}

    def _connect(self, sandbox_id: str):
        if not sandbox_id:
            raise ValueError("sandbox_id is required")
        return self._sandbox_class().connect(sandbox_id)