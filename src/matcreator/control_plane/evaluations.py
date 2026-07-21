"""Durable benchmark evaluation campaigns owned by the control plane."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


ACTIVE_CAMPAIGN_STATUSES = frozenset({"draft", "starting", "active", "cancelling"})
TERMINAL_CAMPAIGN_STATUSES = frozenset({"completed", "failed", "cancelled"})
ACTIVE_ATTEMPT_STATUSES = frozenset(
    {"queued", "runtime_starting", "running", "submitting", "grading", "cancelling"}
)
TERMINAL_ATTEMPT_STATUSES = frozenset(
    {"completed", "failed", "cancelled", "timed_out", "interrupted"}
)

_CAMPAIGN_TRANSITIONS = {
    "draft": frozenset({"starting", "cancelled"}),
    "starting": frozenset({"active", "failed", "cancelled"}),
    "active": frozenset({"cancelling", "completed", "failed", "cancelled"}),
    "cancelling": frozenset({"cancelled", "failed", "completed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_ATTEMPT_TRANSITIONS = {
    "queued": frozenset({"runtime_starting", "cancelled", "failed"}),
    "runtime_starting": frozenset({"running", "failed", "cancelled", "timed_out", "interrupted"}),
    "running": frozenset({"submitting", "cancelling", "failed", "cancelled", "timed_out", "interrupted"}),
    "submitting": frozenset({"grading", "failed", "cancelled", "interrupted"}),
    "grading": frozenset({"completed", "failed", "cancelled", "interrupted"}),
    "cancelling": frozenset({"cancelled", "failed", "interrupted"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
    "timed_out": frozenset(),
    "interrupted": frozenset(),
}


def _validate_transition(transitions: dict[str, frozenset[str]], current: str, target: str) -> None:
    if current not in transitions or target not in transitions:
        raise ValueError(f"Unsupported evaluation status: {current} -> {target}")
    if current != target and target not in transitions[current]:
        raise ValueError(f"Illegal evaluation transition: {current} -> {target}")


class EvaluationStore:
    """SQLite-backed evaluation campaigns and question attempts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS evaluation_campaigns (
                    campaign_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    benchmark_session_id TEXT,
                    benchmark_run_id TEXT,
                    status TEXT NOT NULL,
                    configuration TEXT NOT NULL DEFAULT '{}',
                    result_summary TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_campaigns_owner
                ON evaluation_campaigns(owner_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS evaluation_attempts (
                    attempt_id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    question_id TEXT NOT NULL,
                    runtime_session_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    workspace_path TEXT,
                    task_payload TEXT NOT NULL DEFAULT '{}',
                    managed_run_id TEXT,
                    benchmark_attempt_id TEXT,
                    grading_job_id TEXT,
                    result TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES evaluation_campaigns(campaign_id) ON DELETE CASCADE,
                    UNIQUE(campaign_id, question_id)
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_attempts_campaign
                ON evaluation_attempts(campaign_id, updated_at DESC);

                CREATE TABLE IF NOT EXISTS evaluation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    attempt_id TEXT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES evaluation_campaigns(campaign_id) ON DELETE CASCADE,
                    FOREIGN KEY(attempt_id) REFERENCES evaluation_attempts(attempt_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_events_campaign
                ON evaluation_events(campaign_id, event_id);

                CREATE TABLE IF NOT EXISTS evaluation_question_sets (
                    set_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    question_ids TEXT NOT NULL,
                    visibility TEXT NOT NULL CHECK(visibility IN ('private', 'shared')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(owner_id, name)
                );

                CREATE INDEX IF NOT EXISTS idx_evaluation_question_sets_visibility
                ON evaluation_question_sets(visibility, updated_at DESC);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(evaluation_attempts)").fetchall()
            }
            if "managed_run_id" not in columns:
                connection.execute("ALTER TABLE evaluation_attempts ADD COLUMN managed_run_id TEXT")
            if "task_payload" not in columns:
                connection.execute(
                    "ALTER TABLE evaluation_attempts ADD COLUMN task_payload TEXT NOT NULL DEFAULT '{}'"
                )

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        value = dict(row)
        for key in ("configuration", "result_summary", "result", "payload", "task_payload", "question_ids"):
            if key in value:
                try:
                    value[key] = json.loads(value[key])
                except (TypeError, json.JSONDecodeError):
                    value[key] = {}
        return value

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        campaign_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        attempt_id: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO evaluation_events (campaign_id, attempt_id, event_type, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (campaign_id, attempt_id, event_type, json.dumps(payload, sort_keys=True), time.time()),
        )

    def create_campaign(
        self,
        *,
        owner_id: str,
        model_name: str,
        configuration: dict[str, Any],
    ) -> dict[str, Any]:
        if not owner_id or not model_name:
            raise ValueError("owner_id and model_name are required")
        campaign_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_campaigns (
                    campaign_id, owner_id, model_name, status, configuration, created_at, updated_at
                ) VALUES (?, ?, ?, 'draft', ?, ?, ?)
                """,
                (campaign_id, owner_id, model_name, json.dumps(configuration, sort_keys=True), now, now),
            )
            self._event(connection, campaign_id, "campaign_created", {"status": "draft"})
        return self.get_campaign(campaign_id) or {}

    def get_campaign(self, campaign_id: str, *, owner_id: str | None = None) -> dict[str, Any] | None:
        query = "SELECT * FROM evaluation_campaigns WHERE campaign_id = ?"
        params: tuple[str, ...] = (campaign_id,)
        if owner_id is not None:
            query += " AND owner_id = ?"
            params = (campaign_id, owner_id)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return self._decode(row)

    def list_campaigns(self, *, owner_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_campaigns WHERE owner_id = ? ORDER BY updated_at DESC",
                (owner_id,),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def list_active_campaigns(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in ACTIVE_CAMPAIGN_STATUSES)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM evaluation_campaigns
                WHERE status IN ({placeholders})
                ORDER BY updated_at
                """,
                tuple(ACTIVE_CAMPAIGN_STATUSES),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def transition_campaign(
        self,
        campaign_id: str,
        status: str,
        *,
        benchmark_session_id: str | None = None,
        benchmark_run_id: str | None = None,
        result_summary: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_campaigns WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation campaign '{campaign_id}' was not found")
            _validate_transition(_CAMPAIGN_TRANSITIONS, current["status"], status)
            connection.execute(
                """
                UPDATE evaluation_campaigns
                SET status = ?, benchmark_session_id = ?, benchmark_run_id = ?, result_summary = ?, error = ?, updated_at = ?
                WHERE campaign_id = ?
                """,
                (
                    status,
                    benchmark_session_id if benchmark_session_id is not None else current["benchmark_session_id"],
                    benchmark_run_id if benchmark_run_id is not None else current["benchmark_run_id"],
                    json.dumps(result_summary if result_summary is not None else current["result_summary"], sort_keys=True),
                    error,
                    time.time(),
                    campaign_id,
                ),
            )
            self._event(connection, campaign_id, "campaign_status", {"status": status, "error": error})
        return self.get_campaign(campaign_id) or {}

    def create_attempt(
        self,
        *,
        campaign_id: str,
        question_id: str,
        runtime_session_id: str,
        idempotency_key: str,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        if not question_id or not runtime_session_id or not idempotency_key:
            raise ValueError("question_id, runtime_session_id, and idempotency_key are required")
        attempt_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_attempts WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
            )
            if existing is not None:
                if existing["campaign_id"] != campaign_id or existing["question_id"] != question_id:
                    raise ValueError("Attempt idempotency key belongs to different work")
                return existing
            connection.execute(
                """
                INSERT INTO evaluation_attempts (
                    attempt_id, campaign_id, question_id, runtime_session_id, idempotency_key,
                    status, workspace_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (attempt_id, campaign_id, question_id, runtime_session_id, idempotency_key, workspace_path, now, now),
            )
            self._event(connection, campaign_id, "attempt_created", {"status": "queued", "question_id": question_id}, attempt_id=attempt_id)
        return self.get_attempt(attempt_id) or {}

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM evaluation_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        return self._decode(row)

    def list_attempts(self, campaign_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM evaluation_attempts WHERE campaign_id = ? ORDER BY created_at, attempt_id",
                (campaign_id,),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def transition_attempt(
        self,
        attempt_id: str,
        status: str,
        *,
        benchmark_attempt_id: str | None = None,
        grading_job_id: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation attempt '{attempt_id}' was not found")
            _validate_transition(_ATTEMPT_TRANSITIONS, current["status"], status)
            connection.execute(
                """
                UPDATE evaluation_attempts
                SET status = ?, benchmark_attempt_id = ?, grading_job_id = ?, result = ?, error = ?, updated_at = ?
                WHERE attempt_id = ?
                """,
                (
                    status,
                    benchmark_attempt_id if benchmark_attempt_id is not None else current["benchmark_attempt_id"],
                    grading_job_id if grading_job_id is not None else current["grading_job_id"],
                    json.dumps(result if result is not None else current["result"], sort_keys=True),
                    error,
                    time.time(),
                    attempt_id,
                ),
            )
            self._event(
                connection,
                current["campaign_id"],
                "attempt_status",
                {"status": status, "error": error},
                attempt_id=attempt_id,
            )
        return self.get_attempt(attempt_id) or {}

    def set_managed_run_id(self, attempt_id: str, managed_run_id: str) -> dict[str, Any]:
        if not managed_run_id:
            raise ValueError("managed_run_id is required")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation attempt '{attempt_id}' was not found")
            if current["status"] not in {"runtime_starting", "running"}:
                raise ValueError("Managed runs can only be linked to an active runtime attempt")
            connection.execute(
                "UPDATE evaluation_attempts SET managed_run_id = ?, updated_at = ? WHERE attempt_id = ?",
                (managed_run_id, time.time(), attempt_id),
            )
            self._event(
                connection,
                current["campaign_id"],
                "attempt_managed_run_started",
                {"managed_run_id": managed_run_id},
                attempt_id=attempt_id,
            )
        return self.get_attempt(attempt_id) or {}

    def set_task_payload(self, attempt_id: str, task_payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation attempt '{attempt_id}' was not found")
            if current["status"] not in {"runtime_starting", "running"}:
                raise ValueError("Task content can only be recorded for an active runtime attempt")
            connection.execute(
                "UPDATE evaluation_attempts SET task_payload = ?, updated_at = ? WHERE attempt_id = ?",
                (json.dumps(task_payload, sort_keys=True), time.time(), attempt_id),
            )
            self._event(
                connection,
                current["campaign_id"],
                "attempt_task_loaded",
                {"data_file_count": len(task_payload.get("data_files", []))},
                attempt_id=attempt_id,
            )
        return self.get_attempt(attempt_id) or {}

    def recover_missing_result_attempt(self, attempt_id: str, result: dict[str, Any]) -> dict[str, Any]:
        """Repair a failure caused by the retired per-question result lookup."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_attempts WHERE attempt_id = ?", (attempt_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation attempt '{attempt_id}' was not found")
            if current["status"] != "failed" or not str(current["error"] or "").startswith(
                "Could not retrieve benchmark grading result: Benchmark grading job completed"
            ):
                raise ValueError("Attempt is not eligible for missing-result recovery")
            merged_result = {**current["result"], **result}
            connection.execute(
                """
                UPDATE evaluation_attempts
                SET status = 'completed', result = ?, error = NULL, updated_at = ?
                WHERE attempt_id = ?
                """,
                (json.dumps(merged_result, sort_keys=True), time.time(), attempt_id),
            )
            self._event(
                connection,
                current["campaign_id"],
                "attempt_result_recovered",
                {"status": "completed"},
                attempt_id=attempt_id,
            )
        return self.get_attempt(attempt_id) or {}

    def recover_completed_campaign(self, campaign_id: str) -> dict[str, Any]:
        """Mark an all-completed campaign terminal after targeted result recovery."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._decode(
                connection.execute(
                    "SELECT * FROM evaluation_campaigns WHERE campaign_id = ?", (campaign_id,)
                ).fetchone()
            )
            if current is None:
                raise KeyError(f"Evaluation campaign '{campaign_id}' was not found")
            if current["status"] != "failed":
                raise ValueError("Campaign is not eligible for missing-result recovery")
            outstanding = connection.execute(
                "SELECT COUNT(*) FROM evaluation_attempts WHERE campaign_id = ? AND status != 'completed'",
                (campaign_id,),
            ).fetchone()[0]
            if outstanding:
                raise ValueError("Campaign still has incomplete attempts")
            connection.execute(
                """
                UPDATE evaluation_campaigns
                SET status = 'completed', error = NULL, updated_at = ?
                WHERE campaign_id = ?
                """,
                (time.time(), campaign_id),
            )
            self._event(connection, campaign_id, "campaign_result_recovered", {"status": "completed"})
        return self.get_campaign(campaign_id) or {}

    def list_events(self, campaign_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_events
                WHERE campaign_id = ? AND event_id > ?
                ORDER BY event_id
                """,
                (campaign_id, after),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    @staticmethod
    def _question_set_values(name: str, question_ids: list[str], visibility: str) -> tuple[str, list[str], str]:
        normalized_name = name.strip()
        normalized_ids = list(dict.fromkeys(question_id.strip() for question_id in question_ids if question_id.strip()))
        if not normalized_name or len(normalized_name) > 120:
            raise ValueError("Question set name must be between 1 and 120 characters")
        if not normalized_ids:
            raise ValueError("Question sets require at least one question")
        if visibility not in {"private", "shared"}:
            raise ValueError("Question set visibility must be private or shared")
        return normalized_name, normalized_ids, visibility

    def create_question_set(
        self, *, owner_id: str, name: str, question_ids: list[str], visibility: str = "private"
    ) -> dict[str, Any]:
        if not owner_id:
            raise ValueError("owner_id is required")
        name, question_ids, visibility = self._question_set_values(name, question_ids, visibility)
        set_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO evaluation_question_sets (
                    set_id, owner_id, name, question_ids, visibility, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (set_id, owner_id, name, json.dumps(question_ids), visibility, now, now),
            )
        return self.get_question_set(set_id) or {}

    def get_question_set(self, set_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_question_sets WHERE set_id = ?", (set_id,)
            ).fetchone()
        return self._decode(row)

    def list_question_sets(self, *, viewer_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM evaluation_question_sets
                WHERE owner_id = ? OR visibility = 'shared'
                ORDER BY updated_at DESC, name COLLATE NOCASE
                """,
                (viewer_id,),
            ).fetchall()
        return [self._decode(row) or {} for row in rows]

    def update_question_set(
        self, *, set_id: str, owner_id: str, name: str, question_ids: list[str], visibility: str
    ) -> dict[str, Any]:
        name, question_ids, visibility = self._question_set_values(name, question_ids, visibility)
        with self._connect() as connection:
            result = connection.execute(
                """
                UPDATE evaluation_question_sets
                SET name = ?, question_ids = ?, visibility = ?, updated_at = ?
                WHERE set_id = ? AND owner_id = ?
                """,
                (name, json.dumps(question_ids), visibility, time.time(), set_id, owner_id),
            )
            if result.rowcount != 1:
                raise KeyError("Question set was not found or is not owned by this user")
        return self.get_question_set(set_id) or {}

    def delete_question_set(self, *, set_id: str, owner_id: str) -> None:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM evaluation_question_sets WHERE set_id = ? AND owner_id = ?", (set_id, owner_id)
            )
            if result.rowcount != 1:
                raise KeyError("Question set was not found or is not owned by this user")