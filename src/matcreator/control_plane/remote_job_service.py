"""Provider operations coordinated with durable remote-job records."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .e2b import E2BSandboxAdapter, E2BSandboxSpec
from .remote_jobs import RemoteJobStore


@dataclass(frozen=True)
class E2BConnectionConfig:
    api_key: str
    api_url: str
    project_id: str
    template: str


class RemoteJobService:
    """Coordinates provider side effects with persisted job state."""

    def __init__(self, store: RemoteJobStore, *, e2b_adapter: E2BSandboxAdapter | None = None) -> None:
        self.store = store
        self.e2b_adapter = e2b_adapter or E2BSandboxAdapter()

    def submit_e2b(
        self,
        *,
        owner_id: str,
        session_id: str,
        idempotency_key: str,
        connection: E2BConnectionConfig,
        node_id: str | None = None,
        step_number: int | None = None,
        timeout: int = 600,
        lifecycle: dict[str, Any] | None = None,
        metadata: dict[str, str] | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        """Create an E2B sandbox once and persist the resulting sandbox ID.

        The stored specification intentionally excludes ``api_key``. Replays with
        the same idempotency key return the already-created record instead of
        creating another sandbox.
        """
        job = self.store.create_job(
            owner_id=owner_id,
            session_id=session_id,
            provider="e2b",
            idempotency_key=idempotency_key,
            node_id=node_id,
            step_number=step_number,
            specification={
                "template": connection.template,
                "api_url": connection.api_url,
                "project_id": connection.project_id,
                "timeout": timeout,
                "lifecycle": lifecycle or {},
                "metadata": metadata or {},
            },
            output_dir=output_dir,
        )
        if job["external_id"] or job["status"] != "created":
            return job

        submitting = self.store.transition_job(job["job_id"], "submitting")
        try:
            sandbox_id = self.e2b_adapter.create(
                E2BSandboxSpec(
                    template=connection.template,
                    api_key=connection.api_key,
                    api_url=connection.api_url,
                    project_id=connection.project_id,
                    timeout=timeout,
                    lifecycle=lifecycle or {},
                    metadata=metadata or {},
                )
            )
        except Exception as exc:
            return self.store.transition_job(
                job["job_id"],
                "failed",
                error=f"E2B sandbox creation failed: {exc}",
                expected_revision=submitting["state_revision"],
            )
        return self.store.transition_job(
            job["job_id"],
            "running",
            external_id=sandbox_id,
            snapshot={"provider_status": "running", "sandbox_id": sandbox_id},
            expected_revision=submitting["state_revision"],
        )

    def pause_e2b(self, job_id: str) -> dict[str, Any]:
        self._get_e2b_job(job_id)
        requested = self.store.transition_job(job_id, "pause_requested")
        try:
            self.e2b_adapter.pause(requested["external_id"])
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "failed",
                error=f"E2B pause failed: {exc}",
                expected_revision=requested["state_revision"],
            )
        return self.store.transition_job(
            job_id, "paused", expected_revision=requested["state_revision"]
        )

    def terminate_e2b(self, job_id: str) -> dict[str, Any]:
        self._get_e2b_job(job_id)
        requested = self.store.transition_job(job_id, "terminate_requested")
        try:
            self.e2b_adapter.terminate(requested["external_id"])
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "lost",
                error=f"E2B termination could not be confirmed: {exc}",
                expected_revision=requested["state_revision"],
            )
        return self.store.transition_job(
            job_id, "terminated", expected_revision=requested["state_revision"]
        )

    def pause_active_session_e2b_jobs(self, *, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        """Request a provider pause for each active E2B job in one session."""
        results: list[dict[str, Any]] = []
        for job in self.store.list_jobs(owner_id=owner_id, session_id=session_id):
            if job["provider"] != "e2b" or job["status"] not in {"queued", "running"}:
                continue
            try:
                results.append(self.pause_e2b(job["job_id"]))
            except Exception as exc:
                results.append(
                    {
                        "job_id": job["job_id"],
                        "status": job["status"],
                        "pause_error": str(exc),
                    }
                )
        return results

    def reconcile_e2b(self, job_id: str) -> dict[str, Any]:
        """Probe a persisted active sandbox after a process restart or refresh."""
        job = self._get_e2b_job(job_id)
        if job["status"] not in {"queued", "running", "submitting", "resuming"}:
            return job
        try:
            snapshot = self.e2b_adapter.probe(job["external_id"])
        except Exception as exc:
            return self.store.record_observation(
                job_id,
                snapshot={"provider_status": "unreachable"},
                error=f"E2B reconciliation failed: {exc}",
                expected_revision=job["state_revision"],
            )
        return self.store.record_observation(
            job_id,
            snapshot=snapshot,
            error=None,
            expected_revision=job["state_revision"],
        )

    def run_e2b_command(self, job_id: str, command: str, *, user: str = "root") -> dict[str, Any]:
        """Run one command inside a tracked E2B sandbox without persisting command text."""
        job = self._get_e2b_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"E2B job '{job_id}' cannot run commands while {job['status']}")
        result = self.e2b_adapter.run_command(job["external_id"], command, user=user)
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "last_command_exit_code": result["exit_code"]},
            error=None,
        )
        return result

    def upload_e2b_file(self, job_id: str, source: str | Path, destination: str) -> dict[str, Any]:
        """Upload one local input file into a tracked E2B sandbox."""
        job = self._get_e2b_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"E2B job '{job_id}' cannot receive files while {job['status']}")
        source_path = Path(source).expanduser().resolve()
        self.e2b_adapter.upload_file(job["external_id"], source_path, destination)
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "last_upload": source_path.name},
            error=None,
        )
        return {"source": str(source_path), "destination": destination}

    def _get_e2b_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Remote job '{job_id}' was not found")
        if job["provider"] != "e2b":
            raise ValueError(f"Remote job '{job_id}' is not managed by E2B")
        if not job["external_id"]:
            raise ValueError(f"Remote job '{job_id}' has no sandbox ID")
        return job