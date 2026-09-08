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
    "resuming": frozenset({"running", "succeeded", "failed", "cancelled", "terminate_requested", "lost"}),
    "succeeded": frozenset({"collecting", "failed"}),
    # A failed collection returns to "succeeded": the provider-side outcome
    # is unchanged and the outputs must stay collectable with a new
    # destination instead of durably failing a successful computation.
    "collecting": frozenset({"collected", "succeeded", "failed", "lost"}),
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

                CREATE INDEX IF NOT EXISTS idx_remote_jobs_external
                ON remote_jobs(provider, external_id);

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

                CREATE TABLE IF NOT EXISTS remote_job_notification_stops (
                    owner_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    stopped_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, session_id)
                );
                CREATE TABLE IF NOT EXISTS remote_job_notifications (
                    notification_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    state_revision INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    delivery_status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    failures INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL,
                    lease_until REAL,
                    claim_token TEXT,
                    last_error TEXT,
                    run_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(job_id, kind, state_revision)
                );
                CREATE INDEX IF NOT EXISTS idx_remote_job_notifications_due
                ON remote_job_notifications(delivery_status, available_at, lease_until);
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

    def find_jobs_by_external_id(self, *, provider: str, external_id: str) -> list[dict[str, Any]]:
        """Find all records for a provider-side ID, including terminal jobs."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM remote_jobs WHERE provider = ? AND external_id = ?
                   ORDER BY created_at, job_id""",
                (provider, external_id),
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
            merged_snapshot = current["snapshot"] if snapshot is _UNSET else {**current["snapshot"], **snapshot}
            resulting_external_id = current["external_id"] if external_id is _UNSET else external_id
            updated = connection.execute(
                """
                UPDATE remote_jobs
                SET status = ?, external_id = ?, snapshot = ?, artifacts = ?, error = ?,
                    state_revision = state_revision + 1, updated_at = ?
                WHERE job_id = ? AND state_revision = ?
                """,
                (
                    status,
                    resulting_external_id,
                    json.dumps(merged_snapshot, sort_keys=True),
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
            if status != current["status"] and status in {"succeeded", "failed", "cancelled", "lost"}:
                self._enqueue_notification(
                    connection,
                    {**current, "external_id": resulting_external_id, "status": status,
                     "state_revision": current["state_revision"] + 1, "snapshot": merged_snapshot,
                     "error": current["error"] if error is _UNSET else error},
                    kind="lifecycle", now=now,
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
                    json.dumps({**current["snapshot"], **snapshot}, sort_keys=True),
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
            connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'suppressed',
                   last_error = 'explicit user control', claim_token = NULL, lease_until = NULL,
                   updated_at = ? WHERE job_id = ? AND delivery_status IN ('pending', 'claimed')""",
                (time.time(), job_id),
            )

    def notifications_suppressed(
        self, owner_id: str, session_id: str, *, job_id: str | None = None,
    ) -> bool:
        """Check a stop marker, or a specific job's cutoff and explicit controls."""
        with self._connect() as connection:
            if job_id is not None:
                return connection.execute(
                    """SELECT 1 FROM remote_job_notification_stops s JOIN remote_jobs j
                       ON j.owner_id = s.owner_id AND j.session_id = s.session_id
                       WHERE s.owner_id = ? AND s.session_id = ? AND j.job_id = ?
                         AND j.created_at <= s.stopped_at
                       UNION ALL
                       SELECT 1 FROM remote_job_events e JOIN remote_jobs j ON j.job_id = e.job_id
                       WHERE j.owner_id = ? AND j.session_id = ? AND j.job_id = ?
                         AND e.event_type = 'user_control' LIMIT 1""",
                    (owner_id, session_id, job_id, owner_id, session_id, job_id),
                ).fetchone() is not None
            return connection.execute(
                "SELECT 1 FROM remote_job_notification_stops WHERE owner_id = ? AND session_id = ?",
                (owner_id, session_id),
            ).fetchone() is not None

    def suppress_session_notifications(self, owner_id: str, session_id: str) -> None:
        """Stop wakeups for existing jobs; later explicitly approved jobs remain eligible."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            connection.execute(
                "INSERT OR REPLACE INTO remote_job_notification_stops VALUES (?, ?, ?)",
                (owner_id, session_id, now),
            )
            connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'suppressed',
                   last_error = 'session explicitly stopped', claim_token = NULL,
                   lease_until = NULL, updated_at = ?
                   WHERE owner_id = ? AND session_id = ? AND delivery_status IN ('pending', 'claimed')
                     AND job_id IN (SELECT job_id FROM remote_jobs WHERE created_at <= ?)""",
                (now, owner_id, session_id, now),
            )

    def suppress_notification(self, notification_id: str, reason: str = "suppressed") -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'suppressed',
                   last_error = ?, claim_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE notification_id = ? AND delivery_status IN ('pending', 'claimed')""",
                (reason, time.time(), notification_id),
            )

    @staticmethod
    def _decode_notification(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        return {**json.loads(result.pop("payload")), **result}

    def list_notifications(self) -> list[dict[str, Any]]:
        """Include delivered/suppressed/exhausted records for inspection."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM remote_job_notifications ORDER BY created_at, notification_id"
            ).fetchall()
        return [self._decode_notification(row) for row in rows]

    def list_pending_notifications(self, *, limit: int = 100) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM remote_job_notifications
                   WHERE (delivery_status = 'pending' AND available_at <= ?)
                      OR (delivery_status = 'claimed' AND lease_until <= ?)
                   ORDER BY available_at, created_at LIMIT ?""",
                (now, now, limit),
            ).fetchall()
        return [self._decode_notification(row) for row in rows]

    def claim_notification(self, notification_id: str, *, lease_seconds: float = 60) -> dict[str, Any] | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now, token = time.time(), uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'claimed',
                   attempts = attempts + 1, claim_token = ?, lease_until = ?, updated_at = ?
                   WHERE notification_id = ?
                   AND ((delivery_status = 'pending' AND available_at <= ?)
                     OR (delivery_status = 'claimed' AND lease_until <= ?))
                   AND NOT EXISTS (
                       SELECT 1 FROM remote_job_notification_stops s JOIN remote_jobs j
                       ON j.owner_id = s.owner_id AND j.session_id = s.session_id
                       WHERE s.owner_id = remote_job_notifications.owner_id
                         AND s.session_id = remote_job_notifications.session_id
                         AND j.job_id = remote_job_notifications.job_id
                         AND j.created_at <= s.stopped_at)
                   AND NOT EXISTS (
                       SELECT 1 FROM remote_job_events e
                       WHERE e.job_id = remote_job_notifications.job_id AND e.event_type = 'user_control')""",
                (token, now + lease_seconds, now, notification_id, now, now),
            )
            if updated.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM remote_job_notifications WHERE notification_id = ?", (notification_id,)
            ).fetchone()
            return self._decode_notification(row)

    def finish_notification(self, notification_id: str, claim_token: str, run_id: str) -> bool:
        if not run_id:
            raise ValueError("run_id is required")
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'delivered', run_id = ?,
                   claim_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE notification_id = ? AND claim_token = ? AND delivery_status = 'claimed'""",
                (run_id, time.time(), notification_id, claim_token),
            )
            return result.rowcount == 1

    def defer_notification(self, notification_id: str, claim_token: str, *, delay_seconds: float = 15) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE remote_job_notifications SET delivery_status = 'pending',
                   available_at = ?, claim_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE notification_id = ? AND claim_token = ? AND delivery_status = 'claimed'""",
                (time.time() + max(0.01, delay_seconds), time.time(), notification_id, claim_token),
            )
            return result.rowcount == 1

    def fail_notification(
        self, notification_id: str, claim_token: str, error: str, *,
        delay_seconds: float = 30, max_attempts: int = 8,
    ) -> bool:
        """Bound callback failures, not busy-session deferrals."""
        with self._connect() as connection:
            result = connection.execute(
                """UPDATE remote_job_notifications SET
                   delivery_status = CASE WHEN failures + 1 >= ? THEN 'failed' ELSE 'pending' END,
                   failures = failures + 1, last_error = ?, available_at = ?,
                   claim_token = NULL, lease_until = NULL, updated_at = ?
                   WHERE notification_id = ? AND claim_token = ? AND delivery_status = 'claimed'""",
                (max_attempts, error, time.time() + max(0.01, delay_seconds), time.time(),
                 notification_id, claim_token),
            )
            return result.rowcount == 1

    def record_command_completion(
        self, job_id: str, *, handle: dict[str, Any], result: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically clear this command's handle, record its outcome, and enqueue a wakeup."""
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM remote_jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            current = self._decode(row) or {}
            if current["snapshot"].get("background_command") != handle:
                return current
            revision = current["state_revision"] + 1
            snapshot = {**current["snapshot"], "background_command": None, "provider_status": "reachable",
                        "last_command_exit_code": result.get("exit_code"), "last_command_result": result}
            connection.execute(
                "UPDATE remote_jobs SET snapshot = ?, error = NULL, state_revision = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(snapshot, sort_keys=True), revision, now, job_id),
            )
            self._append_event(connection, job_id, "command_finished", result, now)
            self._enqueue_notification(
                connection, {**current, "state_revision": revision, "snapshot": snapshot, "error": None,
                             "status": "succeeded" if result.get("exit_code") == 0 else "failed",
                             "job_status": current["status"], "command_result": result},
                kind="command", now=now,
            )
        return self.get_job(job_id) or {}

    @staticmethod
    def _enqueue_notification(
        connection: sqlite3.Connection, job: dict[str, Any], *, kind: str, now: float,
    ) -> None:
        if not job["external_id"]:
            return
        stopped = connection.execute(
            """SELECT 1 FROM remote_job_notification_stops
               WHERE owner_id = ? AND session_id = ? AND stopped_at >= ?
               UNION ALL SELECT 1 FROM remote_job_events WHERE job_id = ? AND event_type = 'user_control'
               LIMIT 1""", (job["owner_id"], job["session_id"], job["created_at"], job["job_id"]),
        ).fetchone()
        if stopped:
            return
        payload = {key: job.get(key) for key in (
            "provider", "external_id", "node_id", "step_number", "error", "snapshot",
            "job_status", "command_result",
        )}
        connection.execute(
            """INSERT OR IGNORE INTO remote_job_notifications (
               notification_id, job_id, owner_id, session_id, state_revision, kind, status,
               payload, available_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex, job["job_id"], job["owner_id"], job["session_id"], job["state_revision"],
             kind, job["status"], json.dumps(payload, sort_keys=True), now, now, now),
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