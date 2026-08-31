"""Provider operations coordinated with durable remote-job records.

``RemoteJobService`` never branches on a provider name itself: every
operation looks up the adapter registered for ``job["provider"]`` (see
``providers/registry.py``) and checks its declared capabilities before
calling an optional method. Adding a new remote-job provider is therefore a
pure plugin: implement ``RemoteJobAdapter`` and register it — no changes
needed here.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .providers import (
    CapabilityError,
    RemoteJobAdapter,
    RemoteJobCapability,
    RemoteJobPreflightError,
    get_adapter,
)
from .remote_jobs import TERMINAL_REMOTE_JOB_STATUSES, RemoteJobStore
from .remote_task_monitor import (
    MONITOR_PROTOCOL,
    bootstrap_remote_task_monitor,
    build_monitor_progress_command,
    build_monitor_state_command,
    build_monitored_launch_command,
    remote_task_monitor_paths,
    sync_remote_task_monitor,
)


# A normal provider create/cancel call owns its durable transition for this
# bounded window. Reconciliation waits rather than racing a still-live owner;
# after a process crash the stale record is recovered on the next monitor pass.
_SUBMISSION_RECOVERY_GRACE_SECONDS = 180.0
_TERMINATION_OWNER_GRACE_SECONDS = 120.0
_TERMINATION_PROBE_FAILURE_LIMIT = 3


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
        if provider in self._adapter_overrides:
            return self._adapter_overrides[provider]
        return get_adapter(provider)

    def _adapter(self, provider: str) -> RemoteJobAdapter:
        return self.adapter_for(provider)

    def _get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Remote job '{job_id}' was not found")
        if not job["external_id"]:
            raise ValueError(f"Remote job '{job_id}' has no provider-side ID")
        return job

    @staticmethod
    def _supports_task_monitor(adapter: RemoteJobAdapter) -> bool:
        return RemoteJobCapability.INTERACTIVE_EXEC in adapter.capabilities

    def _ensure_task_monitor(
        self, job: dict[str, Any], adapter: RemoteJobAdapter
    ) -> dict[str, Any]:
        """Start the Sandbox observer once without changing lifecycle state."""
        if not self._supports_task_monitor(adapter) or not job.get("external_id"):
            return job
        snapshot = job.get("snapshot") or {}
        monitor = snapshot.get("remote_monitor") or {}
        if (
            isinstance(monitor, dict)
            and monitor.get("protocol") == MONITOR_PROTOCOL
            and monitor.get("status") not in {None, "bootstrap_failed"}
        ):
            return job
        return bootstrap_remote_task_monitor(store=self.store, job=job, adapter=adapter)

    def _sync_task_monitor(
        self, job: dict[str, Any], adapter: RemoteJobAdapter
    ) -> dict[str, Any]:
        if not self._supports_task_monitor(adapter) or not job.get("external_id"):
            return job
        snapshot = job.get("snapshot") or {}
        monitor = snapshot.get("remote_monitor") or {}
        if not isinstance(monitor, dict) or monitor.get("protocol") != MONITOR_PROTOCOL:
            return job
        return sync_remote_task_monitor(store=self.store, job=job, adapter=adapter)

    @staticmethod
    def _provider_identity_available(job: dict[str, Any]) -> bool:
        specification = job.get("specification") or {}
        return bool(
            specification.get("provider_session_id")
            or specification.get("provider_request_id")
        )

    def _mark_submission_recovery_required(
        self,
        job: dict[str, Any],
        *,
        cause: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        """Stop an ambiguous create from ever being replayed automatically."""

        provider_identity_available = self._provider_identity_available(job)
        try:
            return self.store.transition_job(
                job["job_id"],
                "lost",
                snapshot={
                    **(job.get("snapshot") or {}),
                    "submission_recovery": {
                        "status": "manual_recovery_required",
                        "cause": cause,
                        "automatic_create_replay": False,
                        "provider_identity_available": provider_identity_available,
                    },
                },
                error=(
                    f"{job['provider']} creation could not be confirmed; a provider "
                    "resource may exist, automatic create replay is disabled, and "
                    "manual provider reconciliation is required"
                ),
                expected_revision=expected_revision,
                audit_event_type="submission_recovery_required",
                audit_payload={
                    "cause": cause,
                    "automatic_create_replay": False,
                    "provider_identity_available": provider_identity_available,
                },
            )
        except RuntimeError:
            return self.store.get_job(job["job_id"]) or job

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
        creating a second external job. Only an explicit provider preflight
        failure is reset and retried; an ambiguous provider error may mean the
        resource was created before its response was lost, so it is held for
        manual reconciliation and never replayed automatically.
        """
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
            # Only RemoteJobPreflightError reaches this state without an ID;
            # provider-side or transport ambiguity is held in ``lost``.
            job = self.store.reset_failed_job_for_retry(job["job_id"])
        if job["external_id"] or job["status"] != "created":
            return job

        adapter = self._adapter(provider)
        try:
            submitting = self.store.transition_job(
                job["job_id"],
                "submitting",
                expected_revision=job["state_revision"],
            )
        except RuntimeError:
            # A concurrent caller already claimed this idempotency key. It is
            # safer to return the durable in-flight record than to call
            # provider.create twice.
            return self.store.get_job(job["job_id"]) or job
        try:
            external_id = adapter.create(spec)
        except RemoteJobPreflightError as exc:
            return self.store.transition_job(
                job["job_id"],
                "failed",
                error=f"{provider} job preflight failed: {exc}",
                expected_revision=submitting["state_revision"],
            )
        except Exception as exc:
            return self._mark_submission_recovery_required(
                submitting,
                cause=f"ambiguous_{type(exc).__name__}",
                expected_revision=submitting["state_revision"],
            )

        # Persist the provider ID before any optional status probe. The
        # unavoidable create-return/SQLite-write window is therefore only a
        # local write, not a potentially slow network round trip. Keeping the
        # lifecycle as ``submitting`` allows the provider's first observation
        # to choose queued/running/terminal without an illegal backwards move.
        identified = self.store.transition_job(
            job["job_id"],
            "submitting",
            external_id=external_id,
            snapshot={"provider_status": "created"},
            expected_revision=submitting["state_revision"],
            audit_event_type="provider_id_recorded",
        )

        # Give the adapter a chance to report an initial lifecycle status
        # (e.g. a batch provider that queues before it runs) instead of
        # always assuming "running".
        initial_status = "running"
        initial_snapshot: dict[str, Any] = {"provider_status": "running"}
        try:
            probe = adapter.status(external_id)
        except Exception:
            probe = None
        if probe is not None:
            if probe.normalized_status:
                initial_status = probe.normalized_status
            initial_snapshot = {**initial_snapshot, **probe.snapshot}

        try:
            submitted = self.store.transition_job(
                job["job_id"],
                initial_status,
                external_id=external_id,
                snapshot=initial_snapshot,
                expected_revision=identified["state_revision"],
            )
        except RuntimeError:
            # The background monitor may have reconciled the now-durable ID
            # while the optional initial probe was in flight.
            submitted = self.store.get_job(job["job_id"]) or identified
        if submitted["status"] not in {"queued", "running", "resuming"}:
            return submitted
        return self._ensure_task_monitor(submitted, adapter)

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
        job = self.store.get_job(job_id)
        if job is None:
            raise KeyError(f"Remote job '{job_id}' was not found")
        if job["status"] in TERMINAL_REMOTE_JOB_STATUSES:
            return job
        if job["status"] == "terminate_requested":
            return self._reconcile_termination(job)
        if not job["external_id"]:
            raise ValueError(f"Remote job '{job_id}' has no provider-side ID")
        adapter = self._adapter(job["provider"])
        requested_at = time.time()
        try:
            requested = self.store.transition_job(
                job_id,
                "terminate_requested",
                snapshot={
                    **(job.get("snapshot") or {}),
                    "termination_control": {
                        "status": "cancel_in_flight",
                        "claimed_at": requested_at,
                    },
                },
                expected_revision=job["state_revision"],
            )
        except RuntimeError:
            # Another caller or the monitor won the durable owner claim.
            return self.store.get_job(job_id) or job
        return self._dispatch_termination(requested, adapter)

    def _dispatch_termination(
        self,
        requested: dict[str, Any],
        adapter: RemoteJobAdapter,
    ) -> dict[str, Any]:
        """Execute one already-claimed provider cancel and close its record."""
        job_id = requested["job_id"]
        try:
            adapter.cancel(requested["external_id"])
        except Exception as exc:
            try:
                return self.store.transition_job(
                    job_id,
                    "lost",
                    snapshot={
                        **(requested.get("snapshot") or {}),
                        "termination_control": {
                            "status": "manual_recovery_required",
                            "cancel_replayed": False,
                        },
                    },
                    error=(
                        f"{requested['provider']} termination could not be confirmed: {exc}"
                    ),
                    expected_revision=requested["state_revision"],
                    audit_event_type="termination_manual_recovery_required",
                    audit_payload={"cancel_replayed": False},
                )
            except RuntimeError:
                return self.store.get_job(job_id) or requested
        try:
            return self.store.transition_job(
                job_id,
                "terminated",
                snapshot={
                    **(requested.get("snapshot") or {}),
                    "termination_control": {
                        "status": "confirmed",
                        "cancel_replayed": False,
                    },
                },
                error=None,
                expected_revision=requested["state_revision"],
                audit_event_type="termination_confirmed",
                audit_payload={"cancel_replayed": False},
            )
        except RuntimeError:
            return self.store.get_job(job_id) or requested

    @staticmethod
    def _owner_claim_is_fresh(job: dict[str, Any]) -> bool:
        control = (job.get("snapshot") or {}).get("termination_control") or {}
        if control.get("status") != "cancel_in_flight":
            return False
        try:
            claimed_at = float(control.get("claimed_at"))
        except (TypeError, ValueError):
            return False
        return time.time() - claimed_at < _TERMINATION_OWNER_GRACE_SECONDS

    def _reconcile_termination(self, job: dict[str, Any]) -> dict[str, Any]:
        """Recover a stale terminate request without blindly replaying cancel."""
        if self._owner_claim_is_fresh(job):
            return job
        adapter = self._adapter(job["provider"])
        try:
            probe = adapter.status(job["external_id"])
        except Exception as exc:
            control = (job.get("snapshot") or {}).get("termination_control") or {}
            failures = int(control.get("probe_failures") or 0) + 1
            if failures >= _TERMINATION_PROBE_FAILURE_LIMIT:
                try:
                    return self.store.transition_job(
                        job["job_id"],
                        "lost",
                        snapshot={
                            **(job.get("snapshot") or {}),
                            "provider_status": "unreachable",
                            "termination_control": {
                                "status": "manual_recovery_required",
                                "probe_failures": failures,
                                "cancel_replayed": False,
                            },
                        },
                        error=(
                            f"{job['provider']} termination state is unreachable; "
                            "manual provider reconciliation is required"
                        ),
                        expected_revision=job["state_revision"],
                        audit_event_type="termination_manual_recovery_required",
                        audit_payload={"cancel_replayed": False},
                    )
                except RuntimeError:
                    return self.store.get_job(job["job_id"]) or job
            try:
                return self.store.record_observation(
                    job["job_id"],
                    snapshot={
                        **(job.get("snapshot") or {}),
                        "provider_status": "unreachable",
                        "termination_control": {
                            "status": "recovery_wait",
                            "probe_failures": failures,
                            "cancel_replayed": False,
                        },
                    },
                    error=f"{job['provider']} termination probe failed: {exc}",
                    expected_revision=job["state_revision"],
                )
            except RuntimeError:
                return self.store.get_job(job["job_id"]) or job

        if probe.normalized_status in TERMINAL_REMOTE_JOB_STATUSES:
            try:
                return self.store.transition_job(
                    job["job_id"],
                    "terminated",
                    snapshot={
                        **(job.get("snapshot") or {}),
                        **probe.snapshot,
                        "termination_control": {
                            "status": "confirmed_by_probe",
                            "cancel_replayed": False,
                        },
                    },
                    error=probe.error,
                    expected_revision=job["state_revision"],
                    audit_event_type="termination_reconciled",
                    audit_payload={"cancel_replayed": False},
                )
            except RuntimeError:
                return self.store.get_job(job["job_id"]) or job

        try:
            claimed = self.store.record_observation(
                job["job_id"],
                snapshot={
                    **(job.get("snapshot") or {}),
                    **probe.snapshot,
                    "termination_control": {
                        "status": "cancel_in_flight",
                        "claimed_at": time.time(),
                        "cancel_replayed": False,
                    },
                },
                error=probe.error,
                expected_revision=job["state_revision"],
            )
        except RuntimeError:
            return self.store.get_job(job["job_id"]) or job
        return self._dispatch_termination(claimed, adapter)

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
        if job["status"] == "terminate_requested":
            return self._reconcile_termination(job)
        if job["status"] == "submitting" and not job["external_id"]:
            if time.time() - float(job["updated_at"]) < _SUBMISSION_RECOVERY_GRACE_SECONDS:
                return job
            return self._mark_submission_recovery_required(
                job,
                cause="stale_submitting_without_external_id",
                expected_revision=job["state_revision"],
            )
        if not job["external_id"]:
            raise ValueError(f"Remote job '{job_id}' has no provider-side ID")
        if job["status"] not in {"queued", "running", "submitting", "resuming"}:
            return job
        adapter = self._adapter(job["provider"])
        try:
            probe = adapter.status(job["external_id"])
        except Exception as exc:
            return self.store.merge_observation(
                job_id,
                snapshot={"provider_status": "unreachable"},
                error=f"{job['provider']} reconciliation failed: {exc}",
            )
        if probe.normalized_status and probe.normalized_status != job["status"]:
            try:
                updated = self.store.transition_job(
                    job_id,
                    probe.normalized_status,
                    snapshot={**(job.get("snapshot") or {}), **probe.snapshot},
                    error=probe.error,
                    expected_revision=job["state_revision"],
                )
                if updated["status"] not in {"queued", "running", "resuming"}:
                    return updated
                updated = self._ensure_task_monitor(updated, adapter)
                return self._sync_task_monitor(updated, adapter)
            except ValueError:
                # Provider reported a status this job's current state cannot
                # legally move to (e.g. a stale/out-of-order observation).
                # Recording it as telemetry is always safe; only a lifecycle
                # transition needs the strict check.
                pass
        updated = self.store.merge_observation(
            job_id,
            snapshot=probe.snapshot,
            error=probe.error,
        )
        updated = self._ensure_task_monitor(updated, adapter)
        return self._sync_task_monitor(updated, adapter)

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
        existing_handle = (job.get("snapshot") or {}).get("background_command")
        if isinstance(existing_handle, dict) and existing_handle.get("exit_path"):
            return {
                "job_id": job_id,
                "launch": {"stdout": "ALREADY_TRACKED\n", "stderr": "", "exit_code": 0},
                "handle": existing_handle,
                "reused": True,
            }

        job = self._ensure_task_monitor(job, adapter)
        paths = remote_task_monitor_paths(job_id)
        launch = build_monitored_launch_command(job_id, command)
        launch_result = adapter.run_command(job["external_id"], launch, user=user)
        if launch_result.get("exit_code") not in (None, 0):
            raise RuntimeError(
                f"remote launch wrapper exited with code {launch_result.get('exit_code')}"
            )
        output = str(launch_result.get("stdout") or "")
        if "LAUNCH_CLAIM_UNRESOLVED" in output:
            raise RuntimeError("remote launch claim exists but has no recoverable process identity")
        launch_state = None
        task_pid = None
        recovered_exit_code = None
        for line in output.splitlines():
            if line.startswith(("LAUNCHED:", "ALREADY_LAUNCHED:", "ALREADY_COMPLETED:")):
                launch_state, value = line.split(":", 1)
                value = value.strip()
                if launch_state == "ALREADY_COMPLETED":
                    try:
                        recovered_exit_code = int(value)
                    except ValueError:
                        raise RuntimeError("remote completed launch has an invalid exit marker")
                elif value.isdigit():
                    task_pid = int(value)
                break
        if launch_state is None:
            raise RuntimeError("remote launch response did not include a durable launch identity")
        if launch_state != "ALREADY_COMPLETED" and task_pid is None:
            raise RuntimeError("remote launch identity did not include a process ID")
        execution_status = (
            "completed"
            if launch_state == "ALREADY_COMPLETED" and recovered_exit_code == 0
            else "failed"
            if launch_state == "ALREADY_COMPLETED"
            else "executing"
        )
        handle = {
            "log_path": paths.command_log,
            "exit_path": paths.command_exit,
            "progress_path": paths.progress,
            "monitor_events_path": paths.events,
            "started_at": time.time(),
            "task_pid": task_pid,
            "launch_state": launch_state.lower(),
        }
        self.store.merge_observation(
            job_id,
            snapshot={
                "provider_status": "reachable",
                "background_command": handle,
                "execution": {
                    "status": execution_status,
                    "process_active": launch_state != "ALREADY_COMPLETED",
                    "exit_code": recovered_exit_code,
                },
            },
            error=None,
        )
        return {
            "job_id": job_id,
            "launch": launch_result,
            "handle": handle,
            "reused": launch_state != "LAUNCHED",
        }

    def poll_job_command(self, job_id: str, *, tail_bytes: int = 8000) -> dict[str, Any]:
        """Check on the job's most recently started background command.

        Reads only the durable marker-file paths from the job's snapshot, so
        this works identically whether it's the same process that started
        the command or a freshly re-attached one after a restart. Returns
        ``{"running": True, ...}`` while the exit marker hasn't appeared yet,
        or ``{"running": False, "exit_code": ..., "output_tail": ...}`` once
        it has — ``output_tail`` is the last ``tail_bytes`` of combined
        stdout/stderr; use ``download_job_file`` on ``log_path`` for the
        full output of a long-running command.
        """
        job = self._get_job(job_id)
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.INTERACTIVE_EXEC not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.INTERACTIVE_EXEC)
        handle = (job.get("snapshot") or {}).get("background_command")
        if not isinstance(handle, dict) or not handle.get("exit_path"):
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
        except (IndexError, ValueError):
            exit_code = None
        tail = adapter.run_command(
            job["external_id"], f"tail -c {int(tail_bytes)} {log_path} 2>/dev/null || true", user="root"
        )
        execution_status = "completed" if exit_code == 0 else "failed"
        self.store.merge_observation(
            job_id,
            snapshot={
                "provider_status": "reachable",
                "background_command": None,
                "last_command_exit_code": exit_code,
                "execution": {
                    "status": execution_status,
                    "process_active": False,
                    "exit_code": exit_code,
                },
            },
            error=None,
        )
        return {
            "running": False,
            "exit_code": exit_code,
            "output_tail": tail.get("stdout", ""),
            "log_path": log_path,
        }

    def publish_job_progress(
        self,
        job_id: str,
        *,
        kind: str,
        current: int,
        total: int,
        unit: str,
    ) -> dict[str, Any]:
        """Publish explicit workload progress to the Sandbox observer.

        This is deliberately opt-in: provider liveness is never converted to
        a guessed percentage. Workload launchers or parsers call this only
        when they have a real numerator and denominator (for example MD step
        and target step). The observer persists the value remotely so a local
        disconnect does not erase the latest evidence.
        """
        job = self._get_job(job_id)
        if job["status"] not in {"queued", "running", "resuming"}:
            raise ValueError(f"Job '{job_id}' cannot publish progress while {job['status']}")
        adapter = self._adapter(job["provider"])
        if RemoteJobCapability.INTERACTIVE_EXEC not in adapter.capabilities:
            raise CapabilityError(job["provider"], RemoteJobCapability.INTERACTIVE_EXEC)
        current_value = int(current)
        total_value = int(total)
        command = build_monitor_progress_command(
            job_id,
            kind=kind,
            current=current_value,
            total=total_value,
            unit=unit,
        )
        result = adapter.run_command(job["external_id"], command, user="root")
        if result.get("exit_code") not in (None, 0):
            raise RuntimeError(
                f"remote progress update exited with code {result.get('exit_code')}"
            )
        percent = min(100.0, max(0.0, current_value * 100.0 / total_value))
        return self.store.merge_observation(
            job_id,
            snapshot={
                "provider_status": "reachable",
                "current_phase": "execution",
                "execution": {"status": "executing", "process_active": True},
                "progress": {
                    "kind": kind,
                    "current": current_value,
                    "total": total_value,
                    "percent": percent,
                    "unit": unit,
                    "updated_at": time.time(),
                },
            },
            error=None,
        )

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
        monitor_state_update = "updated"
        try:
            adapter.run_command(
                job["external_id"],
                build_monitor_state_command(
                    job_id,
                    task_status="staging",
                    phase="prepare",
                ),
                user="root",
            )
        except Exception:
            # The upload itself succeeded. A presentation-stage update is
            # observability metadata and must never turn that into a retry.
            monitor_state_update = "unavailable"
        self.store.merge_observation(
            job_id,
            snapshot={
                "provider_status": "reachable",
                "last_upload": source_path.name,
                "monitor_state_update": monitor_state_update,
            },
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
        dest_path = Path(destination_dir).expanduser().resolve()
        try:
            artifacts = adapter.collect_outputs(job["external_id"], dest_path)
        except Exception as exc:
            return self.store.transition_job(
                job_id,
                "failed",
                error=f"{job['provider']} output collection failed: {exc}",
                expected_revision=collecting["state_revision"],
            )
        return self.store.transition_job(
            job_id,
            "collected",
            artifacts=artifacts,
            expected_revision=collecting["state_revision"],
        )
