"""Durable, provider-neutral remote-job records for the control plane."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

TERMINAL_REMOTE_JOB_STATUSES = frozenset(
    {"collected", "failed", "cancelled", "terminated", "lost"}
)
ACTIVE_REMOTE_JOB_STATUSES = frozenset(
    {
        "created",
        "submitting",
        "queued",
        "running",
        "pause_requested",
        "paused",
        "resume_requested",
        "resuming",
        "succeeded",
        "collecting",
        "terminate_requested",
    }
)

_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"submitting", "cancelled", "terminated"}),
    "submitting": frozenset({"queued", "running", "succeeded", "failed", "cancelled", "lost"}),
    "queued": frozenset({"running", "succeeded", "failed", "cancelled", "pause_requested", "terminate_requested", "lost"}),
    "running": frozenset({"succeeded", "failed", "cancelled", "pause_requested", "terminate_requested", "lost"}),
    "pause_requested": frozenset({"paused", "running", "failed", "terminate_requested", "lost"}),
    "paused": frozenset({"resume_requested", "terminate_requested", "failed", "lost"}),
    "resume_requested": frozenset({"resuming", "running", "failed", "terminate_requested", "lost"}),
    "resuming": frozenset({"running", "failed", "terminate_requested", "lost"}),
    "succeeded": frozenset({"collecting", "failed"}),
    "collecting": frozenset({"collected", "failed", "lost"}),
    "terminate_requested": frozenset({"terminated", "failed", "lost"}),
    "collected": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "terminated": frozenset(),
    "lost": frozenset(),
}

_UNSET = object()

_REMOTE_JOB_PHASE_LABELS = {
    "created": "Created",
    "submitting": "Creating remote job",
    "queued": "Queued",
    "running": "Running",
    "pause_requested": "Pausing",
    "paused": "Paused",
    "resume_requested": "Resuming",
    "resuming": "Resuming",
    "succeeded": "Task completed",
    "collecting": "Collecting results",
    "collected": "Completed",
    "terminate_requested": "Closing remote job",
    "terminated": "Terminated",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "lost": "Lost",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _progress_metric(value: Any) -> tuple[float, float, str] | None:
    """Return a progress position only when two samples are safe to compare.

    Remote observer events and explicit progress publications are persisted on
    independent paths.  Their timestamps therefore do not define a reliable
    total order after reconnect.  Numerators are monotonic only within the same
    denominator and unit; anything else may describe a new stage or metric and
    must retain normal source precedence.
    """
    source = _mapping(value)
    current = source.get("current")
    total = source.get("total")
    unit = source.get("unit")
    if (
        isinstance(current, bool)
        or not isinstance(current, (int, float))
        or isinstance(total, bool)
        or not isinstance(total, (int, float))
        or not isinstance(unit, str)
        or not unit
    ):
        return None
    current_value = float(current)
    total_value = float(total)
    if not math.isfinite(current_value) or not math.isfinite(total_value) or total_value <= 0:
        return None
    return current_value, total_value, unit


def _select_monotonic_progress(*values: Any) -> dict[str, Any] | None:
    """Select progress without allowing an older comparable sample to regress.

    The first mapping keeps precedence for incompatible metrics.  Later samples
    may replace it only when ``unit`` and ``total`` match exactly and their
    numerator is greater.  Raw monitor events remain unchanged for audit.
    """
    selected: dict[str, Any] | None = None
    selected_metric: tuple[float, float, str] | None = None
    for value in values:
        candidate = _mapping(value)
        if not candidate:
            continue
        metric = _progress_metric(candidate)
        if selected is None:
            selected = candidate
            selected_metric = metric
            continue
        if metric is None or selected_metric is None:
            continue
        current, total, unit = metric
        selected_current, selected_total, selected_unit = selected_metric
        if total == selected_total and unit == selected_unit and current > selected_current:
            selected = candidate
            selected_metric = metric
    return selected


def _infer_task_type(job: dict[str, Any]) -> str:
    specification = _mapping(job.get("specification"))
    configured = str(specification.get("task_type") or "").strip()
    if configured:
        return configured
    text = " ".join(
        str(value or "")
        for value in (specification.get("stable_name"), job.get("node_id"))
    ).lower()
    if re.search(r"t\d+k[_-]p(?:\d+bar|[-+]?\d+)", text) or re.search(
        r"\b(md|lammps|molecular[_ -]?dynamics)\b", text
    ):
        return "MD"
    if re.search(r"\b(vasp|dft|relax|static)\b", text):
        return "DFT"
    if re.search(r"\b(train|finetune|distill|deepmd)\b", text):
        return "Training"
    if re.search(r"\b(embedding|descriptor|umap)\b", text):
        return "Analysis"
    return "Remote task"


def _workload_kind(task_type: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", task_type.strip().lower()).strip("_")
    return normalized or "remote_task"


def _phase_plan(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            key = item.strip()
            label = key.replace("_", " ").strip().title()
            group = None
            progress_applicable = key in {"execute", "execution"}
        elif isinstance(item, dict):
            key = str(item.get("key") or item.get("id") or item.get("phase") or "").strip()
            label = str(item.get("label") or key.replace("_", " ").title()).strip()
            group = str(item.get("group") or "").strip() or None
            configured_progress = item.get("progress_applicable")
            progress_applicable = (
                configured_progress
                if isinstance(configured_progress, bool)
                else key in {"execute", "execution"}
            )
        else:
            continue
        if not key:
            continue
        phase = {
            "key": key,
            "phase": key,
            "label": label or key,
            "progress_applicable": progress_applicable,
            "show_progress": progress_applicable,
        }
        if group:
            phase["group"] = group
        result.append(phase)
    return result


def build_remote_job_view(
    job: dict[str, Any], *, capabilities: set[str] | frozenset[str] | None = None
) -> dict[str, Any]:
    """Return a canonical, backend-driven presentation projection.

    The durable ``status`` remains the provider/allocation lifecycle. Optional
    versioned presentation metadata supplies a separate workload phase model,
    so a reachable Sandbox is never mistaken for a running scientific task.
    Legacy records degrade to the existing generic labels and actions.
    """
    specification = _mapping(job.get("specification"))
    snapshot = _mapping(job.get("snapshot"))
    configured_presentation = _mapping(specification.get("presentation"))
    observed_presentation = _mapping(snapshot.get("presentation"))
    monitor_sync = _mapping(snapshot.get("monitor_sync"))
    latest_event = _mapping(monitor_sync.get("latest_event"))

    status = str(job.get("status") or "unknown").lower()
    provider = str(job.get("provider") or "").lower()
    kind = "sandbox" if provider == "e2b" or "sandbox" in provider else "batch"
    execution = (
        _mapping(latest_event.get("execution"))
        or _mapping(observed_presentation.get("execution"))
        or _mapping(snapshot.get("execution"))
    )
    progress = _select_monotonic_progress(
        latest_event.get("progress"),
        observed_presentation.get("progress"),
        snapshot.get("progress"),
    )
    validation = (
        _mapping(latest_event.get("validation"))
        or _mapping(observed_presentation.get("validation"))
        or _mapping(snapshot.get("validation"))
        or None
    )
    auto_finalize = _mapping(snapshot.get("auto_finalize"))
    execution_status = str(
        latest_event.get("task_status") or execution.get("status") or "unknown"
    ).lower()
    finalize_status = str(auto_finalize.get("status") or "not_configured").lower()

    phase = status
    phase_label = _REMOTE_JOB_PHASE_LABELS.get(status, "Unknown")
    if kind == "sandbox" and status == "running":
        if finalize_status in {"blocked", "close_failed"}:
            phase, phase_label = "blocked", (
                "Sandbox close failed"
                if finalize_status == "close_failed"
                else "Result collection blocked"
            )
        elif finalize_status == "validated":
            phase, phase_label = "closing", "Validated · closing Sandbox"
        elif execution_status == "failed":
            phase, phase_label = "failed", "Task failed · Sandbox retained"
        elif execution_status in {"completed", "succeeded"}:
            if isinstance(specification.get("auto_finalize"), dict):
                phase, phase_label = "finalizing", "Task completed · collecting results"
            else:
                phase, phase_label = "task_completed", "Task completed · Sandbox still open"
        elif execution_status in {"running", "executing"}:
            phase, phase_label = "running", "Task running"
        elif execution_status == "not_started":
            phase, phase_label = "ready", "Sandbox ready · task not started"
        elif isinstance(progress, dict) and progress.get("current") is not None:
            phase, phase_label = "running", "Task telemetry detected"
        else:
            phase, phase_label = "ready", "Sandbox ready · awaiting task telemetry"
    elif kind == "sandbox" and status == "terminated" and finalize_status == "completed":
        phase, phase_label = "completed", "Completed · results collected · Sandbox closed"

    configured_plan = _phase_plan(
        observed_presentation.get("phase_plan")
        or configured_presentation.get("phase_plan")
        or specification.get("phase_plan")
        or _mapping(specification.get("task_contract")).get("phase_plan")
    )
    current_phase = str(
        latest_event.get("current_phase")
        or latest_event.get("phase")
        or observed_presentation.get("current_phase")
        or snapshot.get("current_phase")
        or configured_presentation.get("current_phase")
        or specification.get("current_phase")
        or phase
    ).strip()
    if current_phase:
        configured_label = next(
            (item["label"] for item in configured_plan if item["key"] == current_phase),
            None,
        )
        if configured_label:
            phase_label = configured_label
        if configured_plan or current_phase == "execute":
            phase = current_phase

    task_type = str(
        observed_presentation.get("task_type")
        or configured_presentation.get("task_type")
        or specification.get("task_type")
        or _infer_task_type(job)
    ).strip()
    workload_kind = str(
        observed_presentation.get("workload_kind")
        or configured_presentation.get("workload_kind")
        or specification.get("workload_kind")
        or _mapping(specification.get("task_contract")).get("workload_profile")
        or _workload_kind(task_type)
    ).strip()
    presentation_version = (
        observed_presentation.get("version")
        or configured_presentation.get("version")
        or specification.get("presentation_version")
    )

    current_phase_definition = next(
        (item for item in configured_plan if item["key"] == current_phase),
        None,
    )
    show_progress = (
        bool(current_phase_definition.get("progress_applicable"))
        if current_phase_definition is not None
        else current_phase in {"execute", "execution"}
    )

    detail = None
    if finalize_status in {"blocked", "close_failed"}:
        detail = (
            "Sandbox close requires attention"
            if finalize_status == "close_failed"
            else "Result collection requires attention"
        )
    elif finalize_status == "waiting":
        reasons = auto_finalize.get("reasons")
        if isinstance(reasons, list) and reasons:
            detail = f"Waiting on {len(reasons)} completion requirement(s)"

    capability_values = set(capabilities or ())
    active = status in ACTIVE_REMOTE_JOB_STATUSES
    refreshable = status in {"queued", "running", "submitting", "resuming"}
    pausable = status in {"queued", "running"}
    terminable = status in {
        "queued", "running", "pause_requested", "paused", "resume_requested", "resuming"
    }
    return {
        "version": "mc.remote-job-view.v1",
        "presentation_version": presentation_version,
        "kind": kind,
        "workload_kind": workload_kind,
        "task_id": specification.get("stable_name") or job.get("node_id") or job.get("job_id") or "—",
        "task_type": task_type,
        "phase": phase,
        "current_phase": current_phase or phase,
        "phase_plan": configured_plan,
        "phase_label": phase_label,
        "show_progress": show_progress,
        "lifecycle_status": status,
        "provider_status": snapshot.get("provider_status") or snapshot.get("raw_status"),
        "execution_status": execution_status,
        "execution": _public_execution(execution),
        "progress": _public_progress(progress),
        "validation": _public_validation(validation),
        "auto_finalize_status": finalize_status,
        "detail": detail,
        "active": active,
        "actions": {
            "refresh": refreshable,
            "pause": pausable and "pause" in capability_values,
            "terminate": terminable,
        },
    }


def _public_scalar(value: Any) -> str | int | float | bool | None:
    """Return a bounded JSON scalar, never a caller-controlled object."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:1024]
    return None


def _public_progress(value: Any) -> dict[str, Any] | None:
    source = _mapping(value)
    allowed = {
        "current",
        "completed",
        "total",
        "percent",
        "fraction",
        "unit",
        "frames",
        "updated_at",
    }
    result = {
        key: scalar
        for key in allowed
        if (scalar := _public_scalar(source.get(key))) is not None
    }
    return result or None


def _public_execution(value: Any) -> dict[str, Any] | None:
    source = _mapping(value)
    allowed = {
        "status",
        "exit_code",
        "process_active",
        "started_at",
        "completed_at",
    }
    result = {
        key: scalar
        for key in allowed
        if (scalar := _public_scalar(source.get(key))) is not None
    }
    return result or None


def _public_validation(value: Any) -> dict[str, Any] | None:
    source = _mapping(value)
    # Validation reasons may embed commands, local paths, or raw provider
    # errors. Public cards need the outcome and timestamp, not that text.
    allowed = {"status", "passed", "checked_at"}
    result = {
        key: scalar
        for key in allowed
        if (scalar := _public_scalar(source.get(key))) is not None
    }
    return result or None


def _public_task_monitor_event(
    event: dict[str, Any], *, source_instance_id: str
) -> dict[str, Any]:
    """Project one monitor event onto the durable, UI-safe telemetry contract."""
    payload: dict[str, Any] = {
        "schema_version": "mc.task-event.v1",
        "source_instance_id": source_instance_id,
        "source_seq": event["source_seq"],
    }
    for key in ("event_type", "occurred_at", "current_phase", "phase", "task_status"):
        value = event.get(key)
        if value is not None:
            payload[key] = value
    field_allowlists = {
        "execution": {
            "status", "exit_code", "process_active", "started_at", "completed_at"
        },
        "progress": {
            "current", "total", "percent", "unit", "frames", "updated_at"
        },
        "validation": {
            "status", "passed", "checked_at"
        },
    }
    for field, allowed in field_allowlists.items():
        value = event.get(field)
        if isinstance(value, dict):
            payload[field] = {key: value[key] for key in allowed if key in value}
    return payload


def validate_remote_job_transition(current: str, target: str) -> str:
    if current not in _JOB_TRANSITIONS:
        raise ValueError(f"Unsupported remote job status: {current}")
    if target not in _JOB_TRANSITIONS:
        raise ValueError(f"Unsupported remote job status: {target}")
    if target != current and target not in _JOB_TRANSITIONS[current]:
        raise ValueError(f"Illegal remote job transition: {current} -> {target}")
    return target


class RemoteJobStore:
    """SQLite-backed state for external jobs that outlive a web process."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS remote_jobs (
                    job_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    node_id TEXT,
                    step_number INTEGER,
                    provider TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    external_id TEXT,
                    status TEXT NOT NULL,
                    specification TEXT NOT NULL DEFAULT '{}',
                    snapshot TEXT NOT NULL DEFAULT '{}',
                    artifacts TEXT NOT NULL DEFAULT '[]',
                    output_dir TEXT,
                    error TEXT,
                    state_revision INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_remote_jobs_session
                ON remote_jobs(owner_id, session_id, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_remote_jobs_active
                ON remote_jobs(provider, status, updated_at);

                CREATE TABLE IF NOT EXISTS remote_job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES remote_jobs(job_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_remote_job_events_job
                ON remote_job_events(job_id, event_id);

                CREATE TABLE IF NOT EXISTS remote_job_monitor_event_dedup (
                    job_id TEXT NOT NULL,
                    source_instance_id TEXT NOT NULL,
                    source_seq INTEGER NOT NULL,
                    event_id INTEGER NOT NULL,
                    PRIMARY KEY(job_id, source_instance_id, source_seq),
                    FOREIGN KEY(job_id) REFERENCES remote_jobs(job_id) ON DELETE CASCADE,
                    FOREIGN KEY(event_id) REFERENCES remote_job_events(event_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_remote_job_monitor_event_cursor
                ON remote_job_monitor_event_dedup(job_id, source_instance_id, source_seq);
                """
            )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for key, fallback in (("specification", {}), ("snapshot", {}), ("artifacts", [])):
            try:
                result[key] = json.loads(result[key])
            except (TypeError, json.JSONDecodeError):
                result[key] = fallback
        return result

    def create_job(
        self,
        *,
        owner_id: str,
        session_id: str,
        provider: str,
        idempotency_key: str,
        node_id: str | None = None,
        step_number: int | None = None,
        specification: dict[str, Any] | None = None,
        output_dir: str | None = None,
    ) -> dict[str, Any]:
        if not owner_id or not session_id or not provider or not idempotency_key:
            raise ValueError("owner_id, session_id, provider, and idempotency_key are required")
        now = time.time()
        job_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM remote_jobs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                existing_data = self._decode(existing) or {}
                if (
                    existing_data["owner_id"] != owner_id
                    or existing_data["session_id"] != session_id
                    or existing_data["provider"] != provider
                ):
                    raise ValueError("Job idempotency key belongs to different work")
                return existing_data
            connection.execute(
                """
                INSERT INTO remote_jobs (
                    job_id, owner_id, session_id, node_id, step_number, provider,
                    idempotency_key, status, specification, output_dir, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner_id,
                    session_id,
                    node_id,
                    step_number,
                    provider,
                    idempotency_key,
                    json.dumps(specification or {}, sort_keys=True),
                    output_dir,
                    now,
                    now,
                ),
            )
            self._append_event(connection, job_id, "created", {"status": "created"}, now)
        return self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._decode(row)

    def merge_specification(self, job_id: str, values: dict[str, Any]) -> dict[str, Any]:
        """Merge presentation metadata without changing provider lifecycle."""
        if not isinstance(values, dict):
            raise TypeError("remote job specification values must be a mapping")
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            specification = {**current["specification"], **values}
            connection.execute(
                """
                UPDATE remote_jobs
                SET specification = ?, state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(specification, sort_keys=True), now, job_id),
            )
            self._append_event(
                connection,
                job_id,
                "metadata_updated",
                {"keys": sorted(values)},
                now,
            )
        return self.get_job(job_id) or {}

    def ingest_task_monitor_events(
        self,
        job_id: str,
        *,
        source_instance_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Atomically ingest ordered, replay-safe Sandbox monitor events.

        Source sequence numbers start at one and must be contiguous beyond the
        persisted cursor. Replayed events are harmless. Only a public telemetry
        projection reaches the durable event log/snapshot; arbitrary remote log
        text is deliberately excluded. This method never changes job status.
        """
        source_instance_id = str(source_instance_id or "").strip()
        if not source_instance_id:
            raise ValueError("source_instance_id is required")
        if len(source_instance_id) > 256:
            raise ValueError("source_instance_id is too long")
        if not isinstance(events, list):
            raise TypeError("events must be a list")

        projected_by_seq: dict[int, dict[str, Any]] = {}
        for event in events:
            if not isinstance(event, dict):
                raise TypeError("each task monitor event must be a mapping")
            if event.get("schema_version") != "mc.task-event.v1":
                raise ValueError("task monitor event schema_version must be 'mc.task-event.v1'")
            event_source = str(event.get("source_instance_id") or "").strip()
            if not event_source:
                raise ValueError("task monitor event source_instance_id is required")
            if event_source != source_instance_id:
                raise ValueError("task monitor event source_instance_id does not match its batch")
            seq = event.get("source_seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
                raise ValueError("task monitor event source_seq must be a positive integer")
            projected = _public_task_monitor_event(
                event, source_instance_id=source_instance_id
            )
            existing = projected_by_seq.get(seq)
            if existing is not None and existing != projected:
                raise ValueError(f"conflicting task monitor event at source_seq {seq}")
            projected_by_seq[seq] = projected

        if not projected_by_seq:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            return job

        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            cursor_row = connection.execute(
                """
                SELECT MAX(source_seq) AS last_source_seq
                FROM remote_job_monitor_event_dedup
                WHERE job_id = ? AND source_instance_id = ?
                """,
                (job_id, source_instance_id),
            ).fetchone()
            last_source_seq = int(cursor_row["last_source_seq"] or 0)
            new_sequences = sorted(seq for seq in projected_by_seq if seq > last_source_seq)
            if new_sequences:
                expected = list(range(last_source_seq + 1, new_sequences[-1] + 1))
                if new_sequences != expected:
                    raise ValueError(
                        "task monitor events contain a sequence gap after "
                        f"{last_source_seq}: received {new_sequences}"
                    )

            latest_event: dict[str, Any] | None = None
            for seq in new_sequences:
                payload = projected_by_seq[seq]
                self._append_event(
                    connection, job_id, "task_monitor_event", payload, now
                )
                event_id = int(
                    connection.execute("SELECT last_insert_rowid()").fetchone()[0]
                )
                connection.execute(
                    """
                    INSERT INTO remote_job_monitor_event_dedup (
                        job_id, source_instance_id, source_seq, event_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (job_id, source_instance_id, seq, event_id),
                )
                latest_event = payload

            if latest_event is not None:
                snapshot = dict(current["snapshot"])
                monitor_sync = dict(_mapping(snapshot.get("monitor_sync")))
                sources = dict(_mapping(monitor_sync.get("sources")))
                sources[source_instance_id] = {
                    "last_source_seq": new_sequences[-1],
                    "updated_at": now,
                }
                monitor_sync.update(
                    {
                        "version": "mc-task-monitor-sync.v1",
                        "source_instance_id": source_instance_id,
                        "last_source_seq": new_sequences[-1],
                        "latest_event": latest_event,
                        "sources": sources,
                    }
                )
                snapshot["monitor_sync"] = monitor_sync
                connection.execute(
                    """
                    UPDATE remote_jobs
                    SET snapshot = ?, state_revision = state_revision + 1, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (json.dumps(snapshot, sort_keys=True), now, job_id),
                )
        return self.get_job(job_id) or {}

    def list_jobs(self, *, owner_id: str, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM remote_jobs
                WHERE owner_id = ? AND session_id = ?
                ORDER BY updated_at DESC, created_at DESC
                """,
                (owner_id, session_id),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def list_active_jobs(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        statuses = tuple(ACTIVE_REMOTE_JOB_STATUSES)
        placeholders = ", ".join("?" for _ in statuses)
        query = f"SELECT * FROM remote_jobs WHERE status IN ({placeholders})"
        parameters: list[Any] = list(statuses)
        if provider:
            query += " AND provider = ?"
            parameters.append(provider)
        query += " ORDER BY updated_at"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode(row) or {} for row in rows]

    def transition_job(
        self,
        job_id: str,
        status: str,
        *,
        external_id: str | None | object = _UNSET,
        snapshot: dict[str, Any] | object = _UNSET,
        artifacts: list[dict[str, Any]] | object = _UNSET,
        error: str | None | object = _UNSET,
        expected_revision: int | None = None,
        audit_event_type: str | None = None,
        audit_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            if expected_revision is not None and current["state_revision"] != expected_revision:
                raise RuntimeError("Remote job revision changed")
            validate_remote_job_transition(current["status"], status)
            updated = connection.execute(
                """
                UPDATE remote_jobs
                SET status = ?, external_id = ?, snapshot = ?, artifacts = ?, error = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ? AND state_revision = ?
                """,
                (
                    status,
                    current["external_id"] if external_id is _UNSET else external_id,
                    json.dumps(current["snapshot"] if snapshot is _UNSET else snapshot, sort_keys=True),
                    json.dumps(current["artifacts"] if artifacts is _UNSET else artifacts, sort_keys=True),
                    current["error"] if error is _UNSET else error,
                    now,
                    job_id,
                    current["state_revision"],
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Remote job revision changed")
            self._append_event(
                connection,
                job_id,
                audit_event_type or "transitioned",
                {
                    "from": current["status"],
                    "to": status,
                    **(audit_payload or {}),
                },
                now,
            )
        return self.get_job(job_id) or {}

    def reset_failed_job_for_retry(self, job_id: str) -> dict[str, Any]:
        """Return a failed job that never acquired an external ID to ``created``.

        ``failed`` is terminal for the normal transition machinery, but a job
        that failed before the provider handed back an external ID has no
        provider-side effect to duplicate, so re-running its submission is
        safe. This is the one sanctioned exception, recorded as its own
        ``retry`` event. Raises ``ValueError`` for any other job state.
        """
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            if current["status"] != "failed" or current["external_id"]:
                raise ValueError(
                    f"Remote job '{job_id}' cannot be reset for retry "
                    f"(status={current['status']!r}, external_id={current['external_id']!r})"
                )
            updated = connection.execute(
                """
                UPDATE remote_jobs
                SET status = 'created', error = NULL,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ? AND state_revision = ?
                """,
                (now, job_id, current["state_revision"]),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Remote job revision changed")
            self._append_event(
                connection,
                job_id,
                "retry",
                {"from": "failed", "to": "created", "previous_error": current["error"]},
                now,
            )
        return self.get_job(job_id) or {}

    def record_observation(
        self,
        job_id: str,
        *,
        snapshot: dict[str, Any],
        error: str | None = None,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Persist a provider observation without changing normalized job status."""
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            if expected_revision is not None and current["state_revision"] != expected_revision:
                raise RuntimeError("Remote job revision changed")
            updated = connection.execute(
                """
                UPDATE remote_jobs
                SET snapshot = ?, error = ?, state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ? AND state_revision = ?
                """,
                (
                    json.dumps(snapshot, sort_keys=True),
                    error,
                    now,
                    job_id,
                    current["state_revision"],
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("Remote job revision changed")
            self._append_event(connection, job_id, "observed", {"status": current["status"]}, now)
        return self.get_job(job_id) or {}

    def merge_observation(
        self,
        job_id: str,
        *,
        snapshot: dict[str, Any],
        error: str | None = None,
    ) -> dict[str, Any]:
        """Merge non-lifecycle telemetry into the latest provider snapshot.

        This intentionally does not accept an expected revision: command and
        upload results may arrive while a monitor is recording provider state.
        """
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            current = self._decode(row) or {}
            merged_snapshot = {**current["snapshot"], **snapshot}
            connection.execute(
                """
                UPDATE remote_jobs
                SET snapshot = ?, error = ?, state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ?
                """,
                (json.dumps(merged_snapshot, sort_keys=True), error, now, job_id),
            )
            self._append_event(connection, job_id, "observed", {"status": current["status"]}, now)
        return self.get_job(job_id) or {}

    def list_events(self, job_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_type, payload, created_at
                FROM remote_job_events WHERE job_id = ? AND event_id > ?
                ORDER BY event_id
                """,
                (job_id, after),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            event = dict(row)
            event["payload"] = json.loads(event["payload"])
            events.append(event)
        return events

    def record_user_control(self, job_id: str, action: str) -> None:
        """Record a user-requested provider control without changing job state."""
        if action not in {"pause", "terminate"}:
            raise ValueError(f"Unsupported remote job user control: {action}")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT 1 FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Remote job '{job_id}' was not found")
            self._append_event(
                connection,
                job_id,
                "user_control",
                {"action": action, "source": "ui"},
                time.time(),
            )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO remote_job_events (job_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, event_type, json.dumps(payload, sort_keys=True), created_at),
        )
