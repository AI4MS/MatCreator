"""Interactive adapter over `bohr sandbox` (create/exec/files/describe/delete).

Mirrors the E2B adapter's capability surface (interactive exec + file
transfer) so agent tools can treat a Bohrium CLI sandbox the same way as an
E2B one. The installed `bohr` CLI has no sandbox pause/resume subcommand
(only create/delete/describe/exec/files/list/...), so
``RemoteJobCapability.PAUSE`` is intentionally not declared here — add it if
a future CLI version exposes one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._bohr_cli import BohrCLIError, extract_id, run_bohr_json
from .base import (
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobPreflightError,
    RemoteJobStatus,
    provider_query_timeout_seconds,
)

# Sandboxes reporting one of these as their describe-output status/state are
# no longer usable; treat them the same as an E2B "unreachable" liveness
# probe failure so the monitor marks the job lost rather than looping.
_UNREACHABLE_STATUSES = {"deleted", "terminated", "stopped", "killed"}


def _provider_absent(error: BohrCLIError) -> bool:
    message = str(error).lower()
    return (
        error.code == "RESOURCE_NOT_FOUND"
        or error.http_status == 404
        or "sandbox not found" in message
    )


def _provider_destroying(error: BohrCLIError) -> bool:
    return error.subtype == "sandbox_destroying"


class BohrSandboxAdapter(RemoteJobAdapter):
    provider = "bohr_sandbox"
    capabilities = frozenset({RemoteJobCapability.INTERACTIVE_EXEC, RemoteJobCapability.FILE_TRANSFER})
    poll_interval_seconds = 15.0

    def create(self, spec: dict[str, Any]) -> str:
        project_id = spec.get("project_id")
        if not project_id:
            raise RemoteJobPreflightError("bohr_sandbox spec requires 'project_id'")
        template = spec.get("template")
        if not template:
            # Never fall back to the CLI's default template (sdbxagent): it
            # silently creates the wrong (and possibly costlier) sandbox.
            raise RemoteJobPreflightError("bohr_sandbox spec requires 'template'")
        args = ["sandbox", "create", "--template", str(template), "--project-id", str(project_id)]
        if spec.get("timeout"):
            args += ["--timeout", str(spec["timeout"])]
        if spec.get("image"):
            args += ["--image", str(spec["image"])]
        if spec.get("gpu"):
            args += ["--gpu", str(spec["gpu"])]
        if spec.get("never_timeout"):
            args.append("--never-timeout")
        if spec.get("mount_user_storage"):
            args.append("--mount-user-storage")
        if spec.get("share_subpath"):
            args += ["--share-subpath", str(spec["share_subpath"])]
        if spec.get("session_id"):
            args += ["--session-id", str(spec["session_id"])]
        for key, value in dict(spec.get("env") or {}).items():
            args += ["--env", f"{key}={value}"]

        data = run_bohr_json(args)
        # The CLI is inconsistent about the ID key across subcommands:
        # `create`/`exec` return "sandboxID", `files read` returns
        # "sandbox_id" — accept every observed spelling.
        sandbox_id = extract_id(data, ("sandbox_id", "sandboxID", "sandboxId", "id"))
        if not sandbox_id:
            keys = sorted(data) if isinstance(data, dict) else type(data).__name__
            raise BohrCLIError(
                f"bohr sandbox create did not return a sandbox ID (data: {keys})"
            )
        return sandbox_id

    def status(self, external_id: str) -> RemoteJobStatus:
        try:
            data = run_bohr_json(
                ["sandbox", "describe", external_id],
                timeout=provider_query_timeout_seconds(),
            ) or {}
        except BohrCLIError as exc:
            if _provider_absent(exc):
                return RemoteJobStatus(
                    normalized_status="lost",
                    snapshot={
                        "provider_status": "unreachable",
                        "raw_status": "deleted",
                    },
                    error=None,
                )
            raise
        raw_status = str(data.get("status") or data.get("state") or "").lower()
        normalized = "lost" if raw_status in _UNREACHABLE_STATUSES else None
        return RemoteJobStatus(
            normalized_status=normalized,
            snapshot={
                "provider_status": "unreachable" if normalized else "reachable",
                "raw_status": raw_status or None,
            },
            error=None,
        )

    def cancel(self, external_id: str) -> None:
        try:
            run_bohr_json(["sandbox", "delete", external_id, "--force"])
        except BohrCLIError as exc:
            # Delete expresses a desired state. Provider 404 means it is
            # already absent; 409/sandbox_destroying means close is already
            # irreversibly in progress. Both are successful idempotent replay.
            if _provider_absent(exc) or _provider_destroying(exc):
                return
            raise

    def run_command(
        self,
        external_id: str,
        command: str,
        *,
        user: str = "root",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        # `bohr sandbox exec` caps a command at 90s by default; pass
        # `--timeout 0` to disable that CLI-side cap, and also don't bound our
        # own subprocess wait, so a long-running command isn't silently
        # truncated. This matches the E2B adapter's `timeout=0` semantics
        # (see e2b.py run_command) so command duration behaves the same
        # regardless of which interactive sandbox provider is in use.
        timeout = (
            None
            if timeout_seconds is None
            else provider_query_timeout_seconds(timeout_seconds)
        )
        cli_timeout = "0" if timeout is None else f"{timeout:g}"
        data = run_bohr_json(
            [
                "sandbox",
                "exec",
                external_id,
                "--command",
                command,
                "--user",
                user,
                "--timeout",
                cli_timeout,
            ],
            timeout=timeout,
        ) or {}
        return {
            "stdout": str(data.get("stdout", "")),
            "stderr": str(data.get("stderr", "")),
            "exit_code": data.get("exit_code", data.get("exitCode")),
        }

    def upload_file(self, external_id: str, source: str | Path, destination: str) -> None:
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        run_bohr_json(["sandbox", "files", "write", external_id, destination, "--source", str(source_path)])

    def download_file(
        self,
        external_id: str,
        source: str,
        destination: str | Path,
        *,
        user: str | None = None,
    ) -> Path:
        dest_path = Path(destination).expanduser().resolve()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        run_bohr_json(["sandbox", "files", "read", external_id, source, "--destination", str(dest_path)])
        return dest_path
