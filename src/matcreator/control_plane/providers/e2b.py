"""E2B sandbox adapter used by control-plane remote-job services."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import os

from .base import RemoteJobAdapter, RemoteJobCapability, RemoteJobStatus

# e2b SDK >=2.20 validates the API key format client-side (requires the
# "e2b_" + hex pattern, see e2b.api.validate_api_key) and raises
# AuthenticationException in ApiClient.__init__ before any network call.
# Bohrium endpoints use bare hex keys that fail this check. Disable the
# client-side guard by default; the key is still sent verbatim as the
# X-API-KEY header, so server-side auth is unaffected. An explicit
# E2B_VALIDATE_API_KEY in the environment takes precedence.
os.environ.setdefault("E2B_VALIDATE_API_KEY", "false")


class E2BConfigurationError(ValueError):
    """Raised when a sandbox request lacks required E2B configuration."""


class E2BUnavailableError(RuntimeError):
    """Raised when the optional E2B SDK is unavailable at runtime."""


@dataclass(frozen=True)
class E2BConnectionConfig:
    """Server-side E2B/Bohrium endpoint configuration for one sandbox request."""

    api_key: str
    api_url: str
    project_id: str
    template: str

    def to_spec_dict(self, *, timeout: int = 600, lifecycle: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build the generic ``spec`` dict passed to ``E2BSandboxAdapter.create``."""
        return {
            "template": self.template,
            "api_key": self.api_key,
            "api_url": self.api_url,
            "project_id": self.project_id,
            "timeout": timeout,
            "lifecycle": lifecycle or {},
        }


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

    @classmethod
    def from_dict(cls, spec: dict[str, Any]) -> "E2BSandboxSpec":
        """Build a validated spec from the generic dict a submit tool provides."""
        return cls(
            template=str(spec.get("template", "")),
            api_key=str(spec.get("api_key", "")),
            api_url=str(spec.get("api_url", "")),
            project_id=str(spec.get("project_id", "")),
            timeout=int(spec.get("timeout", 600)),
            lifecycle=dict(spec.get("lifecycle") or {}),
            metadata=dict(spec.get("metadata") or {}),
        )

# The backend E2B SDK is imported lazily to avoid a hard dependency on the SDK for users who don't need it. The E2BSandboxAdapter class wraps the SDK and provides a simple interface for creating, connecting to, and managing E2B sandboxes.
class E2BSandboxAdapter(RemoteJobAdapter):
    """Small boundary around the E2B SDK with no SDK import at module load."""

    provider = "e2b"
    # Resume is not implemented: the E2B SDK's "pause" produces a snapshot that
    # is restored by simply connecting to the same sandbox_id again, so there
    # is no separate resume operation to expose.
    capabilities = frozenset(
        {
            RemoteJobCapability.PAUSE,
            RemoteJobCapability.INTERACTIVE_EXEC,
            RemoteJobCapability.FILE_TRANSFER,
        }
    )
    poll_interval_seconds = 15.0

    @staticmethod
    def _sandbox_class():
        try:
            from e2b_code_interpreter import Sandbox
        except ImportError as exc:
            raise E2BUnavailableError(
                "e2b-code-interpreter is required for E2B remote jobs"
            ) from exc
        return Sandbox

    def create(self, spec: dict[str, Any]) -> str:
        sandbox_spec = E2BSandboxSpec.from_dict(spec)
        try:
            sandbox = self._sandbox_class().create(**sandbox_spec.create_kwargs())
        except (E2BConfigurationError, E2BUnavailableError):
            raise
        except Exception as exc:
            # Include every non-secret request parameter so a provider-side
            # error (404 wrong URL, unknown template, bad project) is
            # diagnosable from the durable job record alone.
            raise RuntimeError(
                f"{exc} [create against api_url={sandbox_spec.api_url!r}, "
                f"template={sandbox_spec.template!r}, project_id={sandbox_spec.project_id!r}]"
            ) from exc
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

    def download_file(
        self,
        sandbox_id: str,
        source: str,
        destination: str | Path,
        *,
        user: str | None = None,
    ) -> Path:
        """Stream one sandbox file to a local destination path.

        Uses the E2B filesystem streaming API so large outputs (CHGCAR,
        vasprun.xml, PNG) are not truncated by command-output limits. The
        destination leaf is opened with ``O_NOFOLLOW`` so a swapped symlink
        cannot redirect the write, and a failed transfer never leaves a
        half-written file for callers to consume.
        """
        dest_path = Path(os.fspath(destination))
        if not dest_path.is_absolute():
            raise ValueError("E2B download destination must be an absolute path")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        sandbox = self._connect(sandbox_id)
        stream = sandbox.files.read(source, format="stream", user=user)
        fd = None
        file_handle = None
        try:
            fd = os.open(
                str(dest_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
            )
            file_handle = os.fdopen(fd, "wb")
            fd = None  # the file object now owns the descriptor
            for chunk in stream:
                file_handle.write(chunk)
            file_handle.flush()
        except BaseException:
            if file_handle is not None:
                try:
                    file_handle.close()
                except OSError:
                    pass
                file_handle = None
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
            # Never leave a truncated/corrupt output on disk.
            try:
                dest_path.unlink()
            except OSError:
                pass
            raise
        finally:
            if file_handle is not None:
                try:
                    file_handle.close()
                except OSError:
                    pass
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return dest_path

    def pause(self, sandbox_id: str) -> None:
        self._connect(sandbox_id).pause()

    def terminate(self, sandbox_id: str) -> None:
        self._connect(sandbox_id).kill()

    def cancel(self, external_id: str) -> None:
        self.terminate(external_id)

    def probe(self, sandbox_id: str) -> dict[str, Any]:
        """Confirm an active sandbox is reachable without changing its files."""
        result = self.run_command(sandbox_id, "true")
        if result["exit_code"] not in (0, None):
            raise RuntimeError(result["stderr"] or "E2B sandbox liveness probe failed")
        return {"provider_status": "reachable", "probe": result}

    def status(self, external_id: str) -> RemoteJobStatus:
        """Report liveness only: an E2B sandbox stays "running" until an agent
        or user explicitly pauses/terminates it, so there is no independent
        lifecycle status for the monitor to observe beyond reachability.
        """
        snapshot = self.probe(external_id)
        return RemoteJobStatus(normalized_status=None, snapshot=snapshot, error=None)

    def _connect(self, sandbox_id: str):
        if not sandbox_id:
            raise ValueError("sandbox_id is required")
        # Reconnects (run_command/pause/kill, possibly from a fresh process
        # long after create) must target the same Bohrium endpoint as create.
        # The SDK would silently fall back to the public e2b.dev API when
        # called bare, so pass the configured connection explicitly and fail
        # loudly when it is missing.
        api_key = os.environ.get("E2B_API_KEY", "")
        api_url = os.environ.get("E2B_API_URL", "")
        if not api_key or not api_url:
            raise E2BConfigurationError(
                "E2B_API_KEY and E2B_API_URL must be set to connect to sandbox "
                f"'{sandbox_id}'"
            )
        opts: dict[str, Any] = {"api_key": api_key, "api_url": api_url}
        project_id = os.environ.get("BOHRIUM_PROJECT_ID", "")
        if project_id:
            opts["headers"] = {"X-Project-Id": project_id}
        return self._sandbox_class().connect(sandbox_id, **opts)