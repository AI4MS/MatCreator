"""Durable, provider-neutral remote-job records for the control plane."""
from __future__ import annotations

import json
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
                "transitioned",
                {"from": current["status"], "to": status},
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