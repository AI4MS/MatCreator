"""Provider operations coordinated with durable remote-job records.

``RemoteJobService`` never branches on a provider name itself: every
operation looks up the adapter registered for ``job["provider"]`` (see
``providers/registry.py``) and checks its declared capabilities before
calling an optional method. Adding a new remote-job provider is therefore a
pure plugin: implement ``RemoteJobAdapter`` and register it — no changes
needed here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from .providers import (
    CapabilityError,
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobSubmissionUncertainError,
    get_adapter,
)
from .providers.registry import RetiredProviderError, require_supported_provider
from .remote_jobs import RemoteJobStore


class RemoteJobService:
    """Coordinates provider side effects with persisted job state."""

    def __init__(
        self,
        store: RemoteJobStore,
        *,
        adapter_overrides: dict[str, RemoteJobAdapter] | None = None,
    ) -> None:
        """Create a service backed by ``store``.

        ``adapter_overrides`` lets callers (chiefly tests) inject a fake
        adapter for one provider without mutating the global registry;
        providers not present in the override map fall back to
        ``providers.get_adapter``.
        """
        self.store = store
        self._adapter_overrides = dict(adapter_overrides or {})

    def adapter_for(self, provider: str) -> RemoteJobAdapter:
        """Resolve the adapter this service would use for ``provider``.

        Public so callers that need adapter metadata without performing an
        operation (e.g. :class:`RemoteJobMonitor` reading
        ``poll_interval_seconds``) resolve through the same override-aware
        lookup as every service method, instead of querying the global
        registry directly and silently ignoring test overrides.
        """
        require_supported_provider(provider)
        if provider in self._adapter_overrides:
            return self._adapter_overrides[provider]
        return get_adapter(provider)

    def _adapter(self, provider: str) -> RemoteJobAdapter:
        return self.adapter_for(provider)

    def _get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Remote job '{job_id}' was not found")
        require_supported_provider(job["provider"])
        if not job["external_id"]:
            raise ValueError(f"Remote job '{job_id}' has no provider-side ID")
        return job

    def submit_job(
        self,
        *,
        owner_id: str,
        session_id: str,
        provider: str,
        idempotency_key: str,
        spec: dict[str, Any],
        node_id: str | None = None,
        step_number: int | None = None,
        output_dir: str | None = None,
        persisted_specification: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create one external job/sandbox once and persist its external ID.

        ``spec`` is passed to the provider adapter's ``create`` verbatim and
        may contain secrets (e.g. an API key). ``persisted_specification`` —
        which must never contain secrets — is stored in the durable record
        instead; if omitted, ``spec`` itself is persisted, so callers whose
        spec has no secrets can rely on the default. Replays with the same
        idempotency key return the already-created record instead of
        creating a second external job; a record that failed before ever
        acquiring an external ID is reset and retried instead.
        """
        require_supported_provider(provider)
        if node_id is not None:
            for existing in self.store.list_jobs(owner_id=owner_id, session_id=session_id):
                if existing["node_id"] == node_id:
                    try:
                        require_supported_provider(existing["provider"])
                    except RetiredProviderError as exc:
                        raise RetiredProviderError(
                            f"Step already tracks legacy job '{existing['job_id']}'. {exc.args[0]}"
                        ) from exc
        job = self.store.create_job(
            owner_id=owner_id,
            session_id=session_id,
            provider=provider,
            idempotency_key=idempotency_key,
            node_id=node_id,
            step_number=step_number,
            specification=persisted_specification if persisted_specification is not None else spec,
            output_dir=output_dir,
        )
        if job["status"] == "failed" and not job["external_id"]:
            # The previous attempt died before the provider handed back an
            # external ID, so nothing external exists to duplicate — retry
            # instead of returning the poisoned record forever.
            job = self.store.reset_failed_job_for_retry(job["job_id"])
        if job["external_id"] or job["status"] != "created":
            return job

        adapter = self._adapter(provider)
        submitting = self.store.transition_job(job["job_id"], "submitting")
        try:
            external_id = adapter.create(spec)
        except RemoteJobSubmissionUncertainError as exc:
            return self.store.transition_job(
                job["job_id"],
                "lost",
                error=f"{provider} submission outcome is uncertain: {exc}",
                snapshot={"provider_status": "submission_uncertain"},
                expected_revision=submitting["state_revision"],
            )
        except Exception as exc:
            return self.store.transition_job(
                job["job_id"],
                "failed",
                error=f"{provider} job creation failed: {exc}",
                expected_revision=submitting["state_revision"],
            )

        # Give the adapter a chance to report an initial lifecycle status
        # (e.g. a batch provider that queues before it runs) instead of
        # always assuming "running".
        initial_status = "queued" if RemoteJobCapability.BATCH_COLLECT in adapter.capabilities else "running"
        initial_snapshot: dict[str, Any] = {"provider_status": initial_status}
        initial_error = None
        try:
            probe = adapter.status(external_id)
        except Exception as exc:
            probe = None
            initial_snapshot = {"provider_status": "unreachable"}
            initial_error = f"{provider} initial status check failed: {exc}"
        if probe is not None:
            if probe.normalized_status in {"queued", "running", "succeeded", "failed", "cancelled", "lost"}:
                initial_status = probe.normalized_status
            initial_snapshot = {**initial_snapshot, **probe.snapshot}
            initial_error = probe.error

        return self.store.transition_job(
            job["job_id"],
            initial_status,
            external_id=external_id,
            snapshot=initial_snapshot,
            error=initial_error,
            expected_revision=submitting["state_revision"],
        )

    def attach_job(
        self,
        *,
        owner_id: str,
        session_id: str,
        provider: str,
        external_id: str,
        node_id: str | None = None,
        step_number: int | None = None,
    ) -> dict[str, Any]:
        """Track an already-submitted job after a read-only status check; never submit.

        Reuse the record for this owner/session/provider/external ID, including
        records created through submit_job. A job tracked elsewhere cannot be
        attached here. Unreadable IDs are rejected without creating a record.
        """
        require_supported_provider(provider)
        if not owner_id or not session_id:
            raise ValueError("owner_id and session_id are required for attachment")
        if not isinstance(external_id, str) or not external_id.strip():
            raise ValueError("An explicit nonempty string external_id is required")
        external_id = external_id.strip()
        existing_jobs = self.store.find_jobs_by_external_id(provider=provider, external_id=external_id)
        if any(
            job["owner_id"] != owner_id or job["session_id"] != session_id
            for job in existing_jobs
        ):
            raise ValueError("This remote job is already tracked in another session")
        if existing_jobs:
            return self.reconcile_job(existing_jobs[0]["job_id"])

        adapter = self._adapter(provider)
        try:
            probe = adapter.status(external_id)
        except Exception as exc:
            raise ValueError(
                f"Could not verify the existing {provider} job: {exc}. "
                "Check the ID and provider account access, then retry attachment. "
                "No job was submitted."
            ) from exc

        # A provider-global key prevents concurrent attachments from claiming
        # the same external job for different owners or sessions.
        identity = json.dumps([provider, external_id])
        key = f"remote-job:attach:{hashlib.sha256(identity.encode()).hexdigest()}"
        job = self.store.create_job(
            owner_id=owner_id,
            session_id=session_id,
            provider=provider,
            idempotency_key=key,
            node_id=node_id,
            step_number=step_number,
            specification={"attached": True},
        )
        if job["external_id"]:
            return self.reconcile_job(job["job_id"])
        if job["status"] == "created":
            job = self.store.transition_job(
                job["job_id"], "submitting", expected_revision=job["state_revision"],
            )
        if job["status"] != "submitting":
            return job
        initial_status = probe.normalized_status
        if initial_status not in {"queued", "running", "succeeded", "failed", "cancelled", "lost"}:
            initial_status = "queued"
        return self.store.transition_job(
            job["job_id"],
            initial_status,
            external_id=external_id,
            snapshot=probe.snapshot,
            error=probe.error,
            expected_revision=job["state_revision"],
        )

    def pause_job(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.PAUSE not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.PAUSE)
        requested = self.store.transition_job(job_id, "pause_requested")
        try:
            adapter.pause(requested["external_id"])
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "failed",
                error=f"{job['provider']} pause failed: {exc}",
                expected_revision=requested["state_revision"],
            )
        return self.store.transition_job(job_id, "paused", expected_revision=requested["state_revision"])

    def resume_job(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.RESUME not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.RESUME)
        requested = self.store.transition_job(job_id, "resume_requested")
        resuming = self.store.transition_job(job_id, "resuming", expected_revision=requested["state_revision"])
        try:
            adapter.resume(resuming["external_id"])
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "failed",
                error=f"{job['provider']} resume failed: {exc}",
                expected_revision=resuming["state_revision"],
            )
        return self.store.transition_job(job_id, "running", expected_revision=resuming["state_revision"])

    def terminate_job(self, job_id: str) -> dict[str, Any]:
        job = self._get_job(job_id)
        adapter = self._adapter(job["provider"])
        requested = self.store.transition_job(job_id, "terminate_requested")
        try:
            adapter.cancel(requested["external_id"])
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "lost",
                error=f"{job['provider']} termination could not be confirmed: {exc}",
                expected_revision=requested["state_revision"],
            )
        return self.store.transition_job(job_id, "terminated", expected_revision=requested["state_revision"])

    def pause_active_session_jobs(self, *, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        """Request a provider pause for each active, pausable job in one session."""
        results: list[dict[str, Any]] = []
        for job in self.store.list_jobs(owner_id=owner_id, session_id=session_id):
            if job["status"] not in {"queued", "running"}:
                continue
            try:
                adapter = self._adapter(job["provider"])
            except KeyError:
                continue
            if RemoteJobCapability.PAUSE not in adapter.capabilities:
                continue
            try:
                results.append(self.pause_job(job["job_id"]))
            except Exception as exc:
                results.append({"job_id": job["job_id"], "status": job["status"], "pause_error": str(exc)})
        return results

    def reconcile_job(self, job_id: str) -> dict[str, Any]:
        """Probe a persisted active job after a process restart or refresh.

        Transitions the durable status only when the adapter reports a
        normalized status that differs from the current one; otherwise the
        probe result is merged as a non-lifecycle observation. An illegal
        transition reported by a confused/stale adapter observation falls
        back to an observation rather than raising, since a monitor loop
        must never crash on one bad probe.
        """
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Remote job '{job_id}' was not found")
        try:
            require_supported_provider(job["provider"])
        except RetiredProviderError as exc:
            return self.store.record_observation(
                job_id,
                snapshot={**job["snapshot"], "provider_status": "unsupported", "provider_supported": False},
                error=exc.args[0],
                expected_revision=job["state_revision"],
            )
        if job["status"] not in {"queued", "running", "submitting", "resuming"}:
            return job
        if not job["external_id"]:
            return job
        try:
            adapter = self._adapter(job["provider"])
            probe = adapter.status(job["external_id"])
        except Exception as exc:
            return self.store.record_observation(
                job_id,
                snapshot={"provider_status": "unreachable"},
                error=f"{job['provider']} reconciliation failed: {exc}",
                expected_revision=job["state_revision"],
            )
        if probe.normalized_status and probe.normalized_status != job["status"]:
            try:
                return self.store.transition_job(
                    job_id,
                    probe.normalized_status,
                    snapshot=probe.snapshot,
                    error=probe.error,
                    expected_revision=job["state_revision"],
                )
            except ValueError:
                # Provider reported a status this job's current state cannot
                # legally move to (e.g. a stale/out-of-order observation).
                # Recording it as telemetry is always safe; only a lifecycle
                # transition needs the strict check.
                pass
        return self.store.record_observation(
            job_id,
            snapshot=probe.snapshot,
            error=probe.error,
            expected_revision=job["state_revision"],
        )

    def run_job_command(self, job_id: str, command: str, *, user: str = "root") -> dict[str, Any]:
        """Run one command inside a tracked interactive job without persisting command text.

        This blocks the caller for the command's full duration with no
        timeout of its own. Fine for short commands (seconds); for anything
        that might run more than a minute or two, use
        ``start_job_command``/``poll_job_command`` instead, which never
        blocks longer than one bounded status check and durably survives a
        process restart mid-command.
        """
        job = self._get_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"Job '{job_id}' cannot run commands while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.INTERACTIVE_EXEC not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.INTERACTIVE_EXEC)
        result = adapter.run_command(job["external_id"], command, user=user)
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "last_command_exit_code": result.get("exit_code")},
            error=None,
        )
        return result

    def start_job_command(self, job_id: str, command: str, *, user: str = "root") -> dict[str, Any]:
        """Launch one command in the background inside a tracked interactive job.

        Built entirely on the existing ``run_command`` capability — no new
        adapter method or capability is required, so this works for any
        current or future ``INTERACTIVE_EXEC`` provider automatically. The
        launch call itself returns almost immediately (only the wrapper
        shell backgrounds and detaches; it does not wait for ``command`` to
        finish). ``command`` is base64-encoded before being embedded in the
        wrapper so arbitrary shell content (quotes, `$`, backticks, newlines)
        can never break out of or reinterpret the wrapper script.

        The command's stdout/stderr and exit code are redirected to marker
        files whose paths are derived only from ``job_id`` and persisted in
        the job's durable snapshot. This is what makes the command
        recoverable: if this process crashes or the connection drops while
        the command is still running, a fresh process re-attaches to the
        same job, reads the same marker-file paths from the durable record,
        and calls ``poll_job_command`` — it never has to guess whether an
        earlier command already ran or re-issue it, which would be unsafe
        for a non-idempotent computation.
        """
        job = self._get_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"Job '{job_id}' cannot run commands while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.INTERACTIVE_EXEC not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.INTERACTIVE_EXEC)

        marker = f"/tmp/matcreator-cmd-{job_id}"
        log_path = f"{marker}.log"
        exit_path = f"{marker}.exit"
        payload = base64.b64encode(command.encode("utf-8")).decode("ascii")
        launch = (
            f"rm -f {exit_path}; "
            f"nohup sh -c 'echo {payload} | base64 -d | sh; echo $? > {exit_path}' "
            f"> {log_path} 2>&1 < /dev/null & echo LAUNCHED"
        )
        launch_result = adapter.run_command(job["external_id"], launch, user=user)
        handle = {"log_path": log_path, "exit_path": exit_path, "started_at": time.time()}
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "background_command": handle},
            error=None,
        )
        return {"job_id": job_id, "launch": launch_result, "handle": handle}

    def poll_job_command(self, job_id: str, *, tail_bytes: int = 8000) -> dict[str, Any]:
        """Check on the job's most recently started background command.

        Reads only the durable marker-file paths from the job's snapshot, so
        this works identically whether it's the same process that started
        the command or a freshly re-attached one after a restart. Returns
        ``{"running": True, ...}`` while the exit marker hasn't appeared yet,
        or ``{"running": False, "exit_code": ..., "output_tail": ...}`` once
        it has — ``output_tail`` is the last ``tail_bytes`` of combined
        stdout/stderr; use ``download_job_file`` on ``log_path`` for the
        full output of a long-running command. If the monitor already consumed
        completion, return its durable result without probing the remote again.
        """
        job = self._get_job(job_id)
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.INTERACTIVE_EXEC not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.INTERACTIVE_EXEC)
        handle = (job.get("snapshot") or {}).get("background_command")
        if not isinstance(handle, dict) or not handle.get("exit_path"):
            completed = job["snapshot"].get("last_command_result")
            if isinstance(completed, dict):
                return dict(completed)
            raise ValueError(f"Job '{job_id}' has no in-flight background command")

        exit_path = handle["exit_path"]
        log_path = handle["log_path"]
        check = adapter.run_command(
            job["external_id"],
            f"if [ -f {exit_path} ]; then echo DONE:$(cat {exit_path}); else echo RUNNING; fi",
            user="root",
        )
        stdout = str(check.get("stdout", "")).strip()
        if not stdout.startswith("DONE:"):
            self.store.merge_observation(
                job_id, snapshot={"provider_status": "reachable"}, error=None
            )
            return {"running": True, "log_path": log_path}

        try:
            exit_code = int(stdout.split(":", 1)[1].strip())
        except (IndexError, ValueError) as exc:
            # Shell redirection creates the marker before echo writes its exit code.
            raise RuntimeError(f"Job '{job_id}' has an incomplete or invalid exit marker") from exc
        tail = adapter.run_command(
            job["external_id"], f"tail -c {int(tail_bytes)} {log_path} 2>/dev/null || true", user="root"
        )
        result = {
            "running": False,
            "exit_code": exit_code,
            "output_tail": tail.get("stdout", ""),
            "log_path": log_path,
        }
        self.store.record_command_completion(job_id, handle=handle, result=result)
        return result

    def upload_job_file(self, job_id: str, source: str | Path, destination: str) -> dict[str, Any]:
        """Upload one local input file into a tracked interactive job."""
        job = self._get_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"Job '{job_id}' cannot receive files while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.FILE_TRANSFER not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.FILE_TRANSFER)
        source_path = Path(source).expanduser().resolve()
        adapter.upload_file(job["external_id"], source_path, destination)
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "last_upload": source_path.name},
            error=None,
        )
        return {"source": str(source_path), "destination": destination}

    def download_job_file(self, job_id: str, source: str, destination: str | Path) -> dict[str, Any]:
        """Download one file from a tracked interactive job to a local path."""
        job = self._get_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"Job '{job_id}' cannot serve files while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.FILE_TRANSFER not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.FILE_TRANSFER)
        dest_path = Path(destination).expanduser().resolve()
        adapter.download_file(job["external_id"], source, dest_path)
        self.store.merge_observation(
            job_id,
            snapshot={"provider_status": "reachable", "last_download": Path(source).name},
            error=None,
        )
        return {"source": source, "destination": str(dest_path)}

    def collect_job_outputs(self, job_id: str, destination_dir: str | Path) -> dict[str, Any]:
        """Pull a finished batch job's output files into ``destination_dir``.

        Only valid once the job has reached ``succeeded``; transitions the
        job through ``collecting`` -> ``collected`` so a repeated collection
        request is a durable no-op rather than a duplicate download.
        """
        job = self._get_job(job_id)
        if job["status"] == "collected":
            return job
        if job["status"] != "succeeded":
            raise ValueError(f"Job '{job_id}' cannot collect outputs while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.BATCH_COLLECT not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.BATCH_COLLECT)
        collecting = self.store.transition_job(job_id, "collecting")
        dest_path = Path(destination_dir).expanduser().absolute()
        try:
            artifacts = adapter.collect_outputs(job["external_id"], dest_path)
        except Exception as exc:
            # A failed collection (occupied destination, transient download
            # error, ...) is a local file-management problem. The provider-side
            # computation is still succeeded, so return there — keeping the
            # outputs collectable with a new destination — rather than durably
            # marking a successful job as failed.
            return self.store.transition_job(
                job_id,
                "succeeded",
                error=f"{job['provider']} output collection failed: {exc}",
                expected_revision=collecting["state_revision"],
            )
        return self.store.transition_job(
            job_id,
            "collected",
            artifacts=artifacts,
            error=None,
            expected_revision=collecting["state_revision"],
        )
