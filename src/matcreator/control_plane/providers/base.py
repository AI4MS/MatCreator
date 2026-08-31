"""Provider-neutral adapter protocol for the remote-job control plane.

``RemoteJobStore`` and ``RemoteJobService`` are already provider-neutral (see
``remote_jobs.py``): the persisted schema, lifecycle state machine, and
recovery bookkeeping all key off a generic ``provider`` string. This module
defines the boundary a *new* provider must implement so that
``RemoteJobService`` never needs provider-specific branches — adding a
provider means adding one adapter module plus one registration call (see
``registry.py``), nothing else in the control plane changes.
"""
from __future__ import annotations

import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


DEFAULT_PROVIDER_QUERY_TIMEOUT_SECONDS = 30.0
PROVIDER_QUERY_TIMEOUT_ENV = "MATCREATOR_REMOTE_PROVIDER_QUERY_TIMEOUT_SECONDS"


def provider_query_timeout_seconds(value: float | None = None) -> float:
    """Return the finite timeout used for provider observations.

    Long-running user commands deliberately retain their adapter-specific
    unbounded behavior.  Status probes and the small task-monitor
    bootstrap/sync commands use this separate bound so a provider transport
    that never returns cannot freeze the durable monitor loop.
    """

    raw_value: object = (
        value
        if value is not None
        else os.environ.get(
            PROVIDER_QUERY_TIMEOUT_ENV,
            str(DEFAULT_PROVIDER_QUERY_TIMEOUT_SECONDS),
        )
    )
    try:
        seconds = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{PROVIDER_QUERY_TIMEOUT_ENV} must be a positive finite number"
        ) from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"{PROVIDER_QUERY_TIMEOUT_ENV} must be a positive finite number"
        )
    return seconds


class RemoteJobCapability(Enum):
    """Optional operations a provider adapter may support.

    ``create``/``status``/``cancel`` are mandatory for every adapter (they are
    abstract methods on :class:`RemoteJobAdapter`). Everything else is gated
    by a capability flag so :class:`RemoteJobService` can reject an
    unsupported operation with a clear, provider-attributed error instead of
    an ``AttributeError`` surfacing from deep inside an adapter.
    """

    PAUSE = "pause"
    RESUME = "resume"
    INTERACTIVE_EXEC = "interactive_exec"
    FILE_TRANSFER = "file_transfer"
    BATCH_COLLECT = "batch_collect"


class CapabilityError(NotImplementedError):
    """Raised when a requested operation is not supported by a provider."""

    def __init__(self, provider: str, capability: RemoteJobCapability) -> None:
        super().__init__(f"Provider '{provider}' does not support '{capability.value}'")
        self.provider = provider
        self.capability = capability


class RemoteJobPreflightError(ValueError):
    """A local validation/setup failure known to precede provider creation.

    Only this exception class is safe for the service to reset and retry with
    the same idempotency key. Generic provider errors are ambiguous: the
    remote resource may already exist even when its response was lost.
    """


@dataclass(frozen=True)
class RemoteJobStatus:
    """Result of probing one external job.

    ``normalized_status`` must be one of the canonical statuses defined in
    ``remote_jobs.py`` (for example ``running``, ``succeeded``, ``failed``,
    ``cancelled``, ``lost``) when the provider can report a lifecycle
    observation, or ``None`` when the provider can only confirm liveness
    without knowing whether that changes the normalized lifecycle (e.g. an
    interactive sandbox that stays "running" until an agent explicitly ends
    it). ``RemoteJobService`` only transitions the durable record's status
    when ``normalized_status`` is not ``None`` and differs from the job's
    current status; otherwise it merges ``snapshot`` as a non-lifecycle
    observation.
    """

    normalized_status: str | None
    snapshot: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class RemoteJobAdapter(ABC):
    """Boundary between the control plane and one external job provider.

    Subclasses implement only what their provider can actually do. Declaring
    ``capabilities`` tells :class:`RemoteJobService` which of the optional
    methods below are safe to call; the default implementations below raise
    :class:`CapabilityError` as a safety net for a capability that was
    declared but never overridden.
    """

    provider: str
    capabilities: frozenset[RemoteJobCapability] = frozenset()
    # Interval RemoteJobMonitor should wait between reconciliations of jobs
    # owned by this provider. Batch/HPC-style providers whose status only
    # changes on the order of minutes can declare a much longer interval than
    # an interactively addressable sandbox.
    poll_interval_seconds: float = 15.0

    @abstractmethod
    def create(self, spec: dict[str, Any]) -> str:
        """Create one external job/sandbox and return its provider-side ID."""

    @abstractmethod
    def status(self, external_id: str) -> RemoteJobStatus:
        """Probe one external job for liveness and/or lifecycle status."""

    @abstractmethod
    def cancel(self, external_id: str) -> None:
        """Irreversibly stop/delete one external job."""

    def pause(self, external_id: str) -> None:
        raise CapabilityError(self.provider, RemoteJobCapability.PAUSE)

    def resume(self, external_id: str) -> None:
        raise CapabilityError(self.provider, RemoteJobCapability.RESUME)

    def run_command(
        self,
        external_id: str,
        command: str,
        *,
        user: str = "root",
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Run one interactive command.

        ``None`` preserves the unbounded contract required by explicit
        long-running user commands.  Control-plane probes pass a finite
        timeout so their failure is isolated from other jobs.
        """
        raise CapabilityError(self.provider, RemoteJobCapability.INTERACTIVE_EXEC)

    def upload_file(self, external_id: str, source: str | Path, destination: str) -> None:
        raise CapabilityError(self.provider, RemoteJobCapability.FILE_TRANSFER)

    def download_file(
        self,
        external_id: str,
        source: str,
        destination: str | Path,
        *,
        user: str | None = None,
    ) -> Path:
        raise CapabilityError(self.provider, RemoteJobCapability.FILE_TRANSFER)

    def collect_outputs(self, external_id: str, destination_dir: str | Path) -> list[dict[str, Any]]:
        """Pull a finished batch job's declared output files to ``destination_dir``.

        Returns a list of ``{"source": ..., "destination": ...}`` records
        describing what was collected, so the caller can persist them as job
        artifacts.
        """
        raise CapabilityError(self.provider, RemoteJobCapability.BATCH_COLLECT)
