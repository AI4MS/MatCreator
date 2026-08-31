"""Batch/HPC-style adapter over `bohr job` (submit/describe/download/terminate).

Inputs are staged once at submission time (``--input_directory``); there is
no interactive exec or incremental file transfer, matching how HPC batch
schedulers work (submit, poll a queue, collect outputs once terminal). Only
single-job submission is supported here — `bohr job_group` fan-out (multiple
jobs sharing one group) is a possible future adapter, not this one.
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

# `bohr job list`/`describe` report a lowercase `phase` string. Map it onto
# the canonical statuses defined in remote_jobs.py. Any phase not listed here
# (e.g. a future platform phase) leaves normalized_status as None so the
# service records an observation instead of guessing a lifecycle transition.
_PHASE_TO_NORMALIZED = {
    "pending": "queued",
    "scheduling": "queued",
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    # "stopped" is the phase used for a job terminated by the user (see
    # `bohr job terminate`); "cancelled" is the closest existing canonical
    # status for a non-failure, user-initiated stop.
    "stopped": "cancelled",
}

_REQUIRED_SPEC_FIELDS = ("project_id", "job_name", "machine_type", "image_address", "command")


class BohrJobAdapter(RemoteJobAdapter):
    provider = "bohr_job"
    capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})
    # Batch job status changes on the order of minutes, not seconds; poll far
    # less often than an interactive sandbox to avoid hammering the platform.
    poll_interval_seconds = 60.0

    def create(self, spec: dict[str, Any]) -> str:
        missing = [name for name in _REQUIRED_SPEC_FIELDS if not spec.get(name)]
        if missing:
            raise RemoteJobPreflightError(
                f"bohr_job spec is missing required field(s): {', '.join(missing)}"
            )
        args = [
            "job",
            "submit",
            "--project_id",
            str(spec["project_id"]),
            "--job_name",
            str(spec["job_name"]),
            "--machine_type",
            str(spec["machine_type"]),
            "--image_address",
            str(spec["image_address"]),
            "--command",
            str(spec["command"]),
        ]
        if spec.get("input_directory"):
            args += ["--input_directory", str(spec["input_directory"])]
        if spec.get("log_file"):
            args += ["--log_file", str(spec["log_file"])]
        if spec.get("result_path"):
            args += ["--result_path", str(spec["result_path"])]
        if spec.get("max_run_time"):
            args += ["--max_run_time", str(spec["max_run_time"])]
        if spec.get("nnode"):
            args += ["--nnode", str(spec["nnode"])]
        if spec.get("max_reschedule_times") is not None:
            args += ["--max_reschedule_times", str(spec["max_reschedule_times"])]
        if spec.get("job_group_id"):
            args += ["--job_group_id", str(spec["job_group_id"])]

        data = run_bohr_json(args)
        bohr_id = extract_id(data, ("bohrId", "bohr_id", "bohrID", "id", "jobId", "jobID"))
        if not bohr_id:
            keys = sorted(data) if isinstance(data, dict) else type(data).__name__
            raise BohrCLIError(
                f"bohr job submit did not return a Bohr job ID (data: {keys})"
            )
        return bohr_id

    def status(self, external_id: str) -> RemoteJobStatus:
        data = run_bohr_json(
            ["job", "describe", "-i", str(external_id)],
            timeout=provider_query_timeout_seconds(),
        ) or {}
        phase = str(data.get("phase", "")).lower()
        normalized = _PHASE_TO_NORMALIZED.get(phase)
        error = None
        if phase == "failed":
            error = str(data.get("errorInfo") or "").strip() or None
        return RemoteJobStatus(
            normalized_status=normalized,
            snapshot={"phase": phase or None, "terminal": bool(data.get("terminal", False))},
            error=error,
        )

    def cancel(self, external_id: str) -> None:
        run_bohr_json(["job", "terminate", "--id", str(external_id), "--no-wait"])

    def collect_outputs(self, external_id: str, destination_dir: str | Path) -> list[dict[str, Any]]:
        dest = Path(destination_dir).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        run_bohr_json(["job", "download", "-i", str(external_id), "--out", str(dest)])
        return [
            {"source": external_id, "destination": str(path)}
            for path in sorted(dest.rglob("*"))
            if path.is_file()
        ]
