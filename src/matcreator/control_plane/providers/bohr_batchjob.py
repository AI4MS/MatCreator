"""Sandbox-based Batch Jobs over the versioned ``bohr batchjob`` CLI."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ._bohr_cli import BohrCLIError, run_bohr_json
from .base import (
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobStatus,
    RemoteJobSubmissionUncertainError,
)

_STATUS_TO_NORMALIZED = {
    "prepared": "queued",
    "pending": "queued",
    "active": "running",
    "running": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "deleted": "cancelled",
    "killed": "cancelled",
}
_TERMINAL_STATUSES = {"succeeded", "failed", "deleted", "killed"}
_DOWNLOAD_TIMEOUT_SECONDS = 2 * 60 * 60 + 120


class BohrBatchJobAdapter(RemoteJobAdapter):
    provider = "bohr_batchjob"
    capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})
    poll_interval_seconds = 60.0

    def create(self, spec: dict[str, Any]) -> str:
        for field in ("name", "image", "command"):
            value = spec.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"bohr_batchjob spec requires a nonempty {field}")
        project_id = spec.get("project_id")
        if (
            not isinstance(project_id, (str, int))
            or isinstance(project_id, bool)
            or not str(project_id).strip()
        ):
            raise ValueError("bohr_batchjob spec requires project_id")

        selectors = [
            field for field in ("machine_type", "sku_id") if spec.get(field) is not None
        ]
        if len(selectors) != 1:
            raise ValueError("bohr_batchjob requires exactly one of machine_type or sku_id")
        selector = selectors[0]
        value = spec[selector]
        if (
            not isinstance(value, (str, int))
            or isinstance(value, bool)
            or not str(value).strip()
            or (selector == "machine_type" and not isinstance(value, str))
        ):
            raise ValueError(f"bohr_batchjob requires a nonempty {selector}")

        args = [
            "batchjob", "submit",
            "--name", spec["name"],
            "--image", spec["image"],
            "--command", spec["command"],
            "--project-id", str(project_id),
            f"--{selector.replace('_', '-')}", str(value),
        ]
        for field, default in (("max_run_time", "24h"), ("max_wait_time", "30m")):
            duration = spec.get(field, default)
            if not isinstance(duration, str) or not duration.strip():
                raise ValueError(f"{field} must be a nonempty Go duration string")
            args += [f"--{field.replace('_', '-')}", duration]

        input_path = spec.get("input_path")
        if input_path is not None:
            if not isinstance(input_path, (str, Path)) or not str(input_path).strip():
                raise ValueError("input_path must be a nonempty local file or directory path")
            args += ["--input", str(input_path)]
        # input_root pins the CLI's working directory so a relative input_path
        # resolves against the staged workspace; absent on legacy specs whose
        # input_path was absolutized by the tool instead.
        input_root = spec.get("input_root")
        if input_root is not None:
            if not isinstance(input_root, (str, Path)) or not str(input_root).strip():
                raise ValueError("input_root must be a nonempty directory path when present")
        out_files = spec.get("out_files")
        if out_files is not None:
            if not isinstance(out_files, (list, tuple)) or any(
                not isinstance(path, str) or not path.strip() for path in out_files
            ):
                raise ValueError("out_files must be a list of nonempty result paths")
            for path in out_files:
                args += ["--out-file", path]

        # The CLI owns Go-duration parsing and local-tree safety validation.
        # Preflight even without input so invalid durations never create a job.
        run_bohr_json([*args, "--dry-run"], cwd=input_root)
        try:
            data = run_bohr_json(args, cwd=input_root)
            job_id = data.get("jobId") if isinstance(data, dict) else None
            if not isinstance(job_id, str) or not job_id.strip():
                raise BohrCLIError(
                    "bohr batchjob submit did not return a nonempty string jobId"
                )
        except BohrCLIError as exc:
            raise RemoteJobSubmissionUncertainError(
                f"{exc}. Submission may have created a prepared job. Inspect "
                "'bohr batchjob list -o json' manually before retrying; "
                "do not resubmit blindly."
            ) from exc
        return job_id

    def status(self, external_id: str) -> RemoteJobStatus:
        data = run_bohr_json(["batchjob", "describe", external_id])
        if not isinstance(data, dict):
            raise BohrCLIError("bohr batchjob describe returned an invalid job object")
        status_name = data.get("status_name")
        if status_name is not None and not isinstance(status_name, str):
            raise BohrCLIError("bohr batchjob describe returned an invalid status_name")
        status_name = (status_name or "").strip().lower()
        snapshot: dict[str, Any] = {
            "provider_status": status_name or "unknown",
            "status_name": status_name or None,
            "terminal": status_name in _TERMINAL_STATUSES,
        }
        for field in ("errorMessage", "errorCode"):
            value = data.get(field)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                snapshot[field] = value
        error = None
        if status_name == "failed":
            message = str(snapshot.get("errorMessage") or "").strip()
            code = snapshot.get("errorCode")
            error = message or (f"Batch Job failed (errorCode: {code})" if code is not None else None)
        return RemoteJobStatus(
            normalized_status=_STATUS_TO_NORMALIZED.get(status_name),
            snapshot=snapshot,
            error=error,
        )

    def cancel(self, external_id: str) -> None:
        data = run_bohr_json(["batchjob", "kill", external_id])
        if not isinstance(data, dict) or data.get("confirmed") is not True:
            raise BohrCLIError(
                f"Batch Job {external_id} termination was not confirmed. "
                f"Re-check with 'bohr batchjob describe {external_id} -o json'; "
                "an accepted stop request does not prove termination."
            )

    def collect_outputs(self, external_id: str, destination_dir: str | Path) -> list[dict[str, Any]]:
        requested = Path(destination_dir).expanduser()
        # Check before resolving, too: a dangling destination symlink is occupied.
        if requested.exists() or requested.is_symlink():
            raise ValueError("Batch Job output destination must not exist; choose a new directory")
        dest = requested.resolve()
        if dest.exists():
            raise ValueError("Batch Job output destination must not exist; choose a new directory")
        run_bohr_json(
            ["batchjob", "download", external_id, "--dest", str(dest), "--timeout", "2h"],
            timeout=_DOWNLOAD_TIMEOUT_SECONDS,
        )
        if not dest.is_dir() or dest.is_symlink():
            raise BohrCLIError("bohr batchjob download did not create the requested output directory")
        return [
            {"source": external_id, "destination": str(path)}
            for path in sorted(dest.rglob("*"))
            if path.is_file()
        ]
