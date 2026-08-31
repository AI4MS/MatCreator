"""Sandbox-side task monitor protocol helpers.

The monitor is deliberately small and provider-neutral.  It runs inside an
interactive Sandbox, records append-only public telemetry, and never owns
collection, validation, cancellation, or scientific acceptance.  The local
control plane pulls the journal after its durable cursor when connectivity is
available again.
"""
from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .providers.base import provider_query_timeout_seconds

TASK_EVENT_SCHEMA = "mc.task-event.v1"
MONITOR_PROTOCOL = "mc.task-events.v1"

_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class RemoteTaskMonitorPaths:
    """Stable remote paths derived exclusively from one MC ``job_id``."""

    root: str
    script: str
    state: str
    progress: str
    events: str
    source: str
    sequence: str
    pid: str
    launch_lock: str
    stop: str
    log: str
    command_log: str
    command_exit: str


def remote_task_monitor_paths(job_id: str) -> RemoteTaskMonitorPaths:
    normalized = str(job_id or "")
    if not _SAFE_ID.fullmatch(normalized):
        raise ValueError("job_id contains unsupported characters")
    root = f"/tmp/matcreator-monitor-{normalized}"
    command_root = f"/tmp/matcreator-cmd-{normalized}"
    return RemoteTaskMonitorPaths(
        root=root,
        script=f"{root}.sh",
        state=f"{root}.state",
        progress=f"{root}.progress",
        events=f"{root}.events.jsonl",
        source=f"{root}.source",
        sequence=f"{root}.seq",
        pid=f"{root}.pid",
        launch_lock=f"{root}.launch.lock",
        stop=f"{root}.stop",
        log=f"{root}.log",
        command_log=f"{command_root}.log",
        command_exit=f"{command_root}.exit",
    )


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _safe_token(value: str, *, field: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _SAFE_TOKEN.fullmatch(normalized):
        raise ValueError(f"{field} contains unsupported characters")
    return normalized


def build_monitor_state_command(
    job_id: str,
    *,
    task_status: str,
    phase: str,
    process_pid: str = "",
    exit_path: str | None = None,
    log_path: str | None = None,
) -> str:
    """Atomically update the small state file observed by the sidecar."""

    paths = remote_task_monitor_paths(job_id)
    status = _safe_token(task_status, field="task_status")
    phase_key = _safe_token(phase, field="phase")
    # POSIX ``read`` treats tab as IFS whitespace and collapses adjacent tab
    # fields.  An empty PID would therefore shift every following path one
    # column to the left.  Use a non-numeric sentinel so all six columns stay
    # aligned while the worker's existing numeric check still treats it as
    # inactive.
    pid = str(process_pid or "-")
    if pid != "-" and not pid.isdigit():
        raise ValueError("process_pid must be numeric")
    resolved_exit = str(exit_path or paths.command_exit)
    resolved_log = str(log_path or paths.command_log)
    if any(
        "\t" in value or "\n" in value
        for value in (resolved_exit, resolved_log, paths.progress)
    ):
        raise ValueError("monitor paths cannot contain tabs or newlines")
    content = (
        f"{status}\t{phase_key}\t{pid}\t{resolved_exit}\t{resolved_log}"
        f"\t{paths.progress}\n"
    )
    payload = _encoded(content)
    return (
        f"umask 077; printf '%s' '{payload}' | base64 -d > {paths.state}.tmp; "
        f"mv {paths.state}.tmp {paths.state}"
    )


def build_monitor_bootstrap_command(job_id: str, *, interval_seconds: int = 15) -> str:
    """Return one idempotent command that starts the detached monitor.

    The worker has no network dependency.  It writes one bounded public event
    per interval and exits after the observed command emits a durable exit
    marker or after an explicit stop marker appears.
    """

    paths = remote_task_monitor_paths(job_id)
    interval = int(interval_seconds)
    if not 5 <= interval <= 300:
        raise ValueError("interval_seconds must be between 5 and 300")

    worker = f"""#!/bin/sh
set -u
umask 077
state='{paths.state}'
events='{paths.events}'
source_file='{paths.source}'
seq_file='{paths.sequence}'
stop_file='{paths.stop}'
interval={interval}

touch "$events"
if [ -s "$source_file" ]; then
  source_id=$(cat "$source_file")
else
  source_id="$(date +%s)-$$"
  printf '%s' "$source_id" > "$source_file.tmp"
  mv "$source_file.tmp" "$source_file"
fi

while :; do
  task_status=ready
  phase=prepare
  process_pid=
  exit_path='{paths.command_exit}'
  log_path='{paths.command_log}'
  progress_path='{paths.progress}'
  if [ -s "$state" ]; then
    IFS="$(printf '\\t')" read -r task_status phase process_pid exit_path log_path progress_path < "$state" || true
  fi
  case "$task_status" in ''|*[!a-z0-9_-]*) task_status=unknown ;; esac
  case "$phase" in ''|*[!a-z0-9_-]*) phase=unknown ;; esac

  process_active=false
  case "$process_pid" in
    ''|*[!0-9]*) ;;
    *) kill -0 "$process_pid" >/dev/null 2>&1 && process_active=true ;;
  esac

  exit_code=null
  if [ -n "$exit_path" ] && [ -f "$exit_path" ]; then
    candidate=$(cat "$exit_path" 2>/dev/null || true)
    case "$candidate" in ''|*[!0-9-]*) ;; *) exit_code=$candidate ;; esac
  fi
  log_bytes=0
  if [ -n "$log_path" ] && [ -f "$log_path" ]; then
    log_bytes=$(wc -c < "$log_path" 2>/dev/null | tr -d ' ' || printf 0)
    case "$log_bytes" in ''|*[!0-9]*) log_bytes=0 ;; esac
  fi

  progress_json=null
  if [ "$phase" = execute ] && [ -n "$progress_path" ] && [ -s "$progress_path" ]; then
    progress_kind=
    progress_current=
    progress_total=
    progress_unit=
    IFS="$(printf '\\t')" read -r progress_kind progress_current progress_total progress_unit < "$progress_path" || true
    case "$progress_kind" in ''|*[!a-z0-9_-]*) progress_kind= ;; esac
    case "$progress_unit" in ''|*[!a-z0-9_-]*) progress_unit= ;; esac
    case "$progress_current" in ''|*[!0-9]*) progress_current= ;; esac
    case "$progress_total" in ''|*[!0-9]*) progress_total= ;; esac
    if [ -n "$progress_kind" ] && [ -n "$progress_current" ] && [ -n "$progress_total" ] && [ "$progress_total" -gt 0 ]; then
      progress_percent=$(awk -v current="$progress_current" -v total="$progress_total" 'BEGIN {{ value=current*100/total; if(value<0)value=0; if(value>100)value=100; printf "%.2f", value }}')
      progress_json=$(printf '{{"kind":"%s","current":%s,"total":%s,"percent":%s,"unit":"%s"}}' "$progress_kind" "$progress_current" "$progress_total" "$progress_percent" "$progress_unit")
    fi
  fi

  event_type=heartbeat
  should_stop=false
  if [ -f "$stop_file" ]; then
    event_type=monitor_stopped
    task_status=stopped
    should_stop=true
  elif [ "$exit_code" != null ]; then
    event_type=execution_exited
    if [ "$exit_code" = 0 ]; then task_status=completed; else task_status=failed; fi
    should_stop=true
  fi

  seq=0
  if [ -s "$seq_file" ]; then seq=$(cat "$seq_file" 2>/dev/null || printf 0); fi
  case "$seq" in ''|*[!0-9]*) seq=0 ;; esac
  seq=$((seq + 1))
  printf '%s' "$seq" > "$seq_file.tmp"
  mv "$seq_file.tmp" "$seq_file"
  now=$(date +%s)
  printf '{{"schema_version":"{TASK_EVENT_SCHEMA}","source_instance_id":"%s","source_seq":%s,"observed_at":%s,"event_type":"%s","task_status":"%s","phase":"%s","process_active":%s,"exit_code":%s,"log_bytes":%s,"progress":%s}}\\n' \
    "$source_id" "$seq" "$now" "$event_type" "$task_status" "$phase" "$process_active" "$exit_code" "$log_bytes" "$progress_json" >> "$events"

  [ "$should_stop" = true ] && break
  sleep "$interval"
done
"""
    script_payload = _encoded(worker)
    initial_state = build_monitor_state_command(
        job_id,
        task_status="ready",
        phase="prepare",
    )
    return (
        "umask 077; "
        f"if [ -s {paths.pid} ] && kill -0 $(cat {paths.pid}) >/dev/null 2>&1; then "
        f"echo MONITOR_ACTIVE:$(cat {paths.pid}); exit 0; fi; "
        f"rm -f {paths.stop}; "
        f"printf '%s' '{script_payload}' | base64 -d > {paths.script}; "
        f"chmod 700 {paths.script}; "
        f"if [ ! -s {paths.state} ]; then {initial_state}; fi; "
        f"nohup {paths.script} > {paths.log} 2>&1 < /dev/null & monitor_pid=$!; "
        f"printf '%s' \"$monitor_pid\" > {paths.pid}; "
        'printf "MONITOR_STARTED:%s\\n" "$monitor_pid"'
    )


def build_monitored_launch_command(job_id: str, command: str) -> str:
    """Launch a workload once and atomically expose its PID/markers.

    ``mkdir`` is the remote exactly-once claim.  An ambiguous retry reports
    the existing launch instead of deleting the exit marker and starting a
    second non-idempotent computation.  A broken claim intentionally requires
    explicit reconciliation; silently guessing is less safe than stopping.
    """

    paths = remote_task_monitor_paths(job_id)
    payload = _encoded(str(command))
    return (
        f"if ! mkdir {paths.launch_lock} 2>/dev/null; then "
        f"if [ -f {paths.command_exit} ]; then "
        f"printf 'ALREADY_COMPLETED:%s\\n' \"$(cat {paths.command_exit})\"; "
        f"elif [ -s {paths.state} ]; then "
        f"existing_pid=$(awk -F '\\t' '{{print $3}}' {paths.state}); "
        "printf 'ALREADY_LAUNCHED:%s\\n' \"$existing_pid\"; "
        "else echo LAUNCH_CLAIM_UNRESOLVED; fi; exit 0; fi; "
        f"rm -f {paths.command_exit}; "
        f"rm -f {paths.progress}; "
        "nohup sh -c '"
        f"export MC_PROGRESS_PATH={paths.progress} MC_PROGRESS_MIN_INTERVAL_SECONDS=15; "
        f"echo {payload} | base64 -d | sh; code=$?; "
        f"printf \"%s\\n\" \"$code\" > {paths.command_exit}.tmp; "
        f"mv {paths.command_exit}.tmp {paths.command_exit}; exit \"$code\"' "
        f"> {paths.command_log} 2>&1 < /dev/null & task_pid=$!; "
        f"printf 'executing\\texecute\\t%s\\t{paths.command_exit}\\t{paths.command_log}"
        f"\\t{paths.progress}\\n' \"$task_pid\" > {paths.state}.tmp; "
        f"mv {paths.state}.tmp {paths.state}; "
        'printf "LAUNCHED:%s\\n" "$task_pid"'
    )


def build_monitor_progress_command(
    job_id: str,
    *,
    kind: str,
    current: int,
    total: int,
    unit: str,
) -> str:
    """Atomically publish normalized execution progress for the observer."""

    paths = remote_task_monitor_paths(job_id)
    kind_key = _safe_token(kind, field="kind")
    unit_key = _safe_token(unit, field="unit")
    current_value = int(current)
    total_value = int(total)
    if current_value < 0 or total_value <= 0:
        raise ValueError("progress requires current >= 0 and total > 0")
    content = f"{kind_key}\t{current_value}\t{total_value}\t{unit_key}\n"
    payload = _encoded(content)
    return (
        f"umask 077; printf '%s' '{payload}' | base64 -d > {paths.progress}.tmp; "
        f"mv {paths.progress}.tmp {paths.progress}"
    )


def build_monitor_sync_command(
    job_id: str, *, after_sequence: int = 0, limit: int = 500
) -> str:
    """Read a bounded journal page after the durable local sequence cursor."""

    paths = remote_task_monitor_paths(job_id)
    after = int(after_sequence)
    page_size = int(limit)
    if after < 0:
        raise ValueError("after_sequence must be non-negative")
    if not 1 <= page_size <= 2000:
        raise ValueError("limit must be between 1 and 2000")
    first = after + 1
    last = after + page_size
    return f"sed -n '{first},{last}p' {paths.events} 2>/dev/null || true"


def build_monitor_stop_command(job_id: str) -> str:
    paths = remote_task_monitor_paths(job_id)
    return f"umask 077; : > {paths.stop}"


def parse_monitor_events(text: str) -> list[dict[str, Any]]:
    """Parse and public-field-filter a Sandbox monitor journal page."""

    events: list[dict[str, Any]] = []
    for raw_line in str(text or "").splitlines():
        try:
            payload = json.loads(raw_line)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != TASK_EVENT_SCHEMA:
            continue
        source_id = str(payload.get("source_instance_id") or "")
        if not _SAFE_ID.fullmatch(source_id):
            continue
        try:
            source_seq = int(payload["source_seq"])
            observed_at = float(payload["observed_at"])
            log_bytes = max(0, int(payload.get("log_bytes") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if source_seq <= 0:
            continue
        event_type = str(payload.get("event_type") or "heartbeat").lower()
        task_status = str(payload.get("task_status") or "unknown").lower()
        phase = str(payload.get("phase") or "unknown").lower()
        if not all(_SAFE_TOKEN.fullmatch(value) for value in (event_type, task_status, phase)):
            continue
        exit_code = payload.get("exit_code")
        if exit_code is not None:
            try:
                exit_code = int(exit_code)
            except (TypeError, ValueError):
                continue
        event: dict[str, Any] = {
            "schema_version": TASK_EVENT_SCHEMA,
            "source_instance_id": source_id,
            "source_seq": source_seq,
            "observed_at": observed_at,
            "occurred_at": observed_at,
            "event_type": event_type,
            "task_status": task_status,
            "phase": phase,
            "process_active": bool(payload.get("process_active")),
            "exit_code": exit_code,
            "log_bytes": log_bytes,
            "execution": {
                "status": task_status,
                "process_active": bool(payload.get("process_active")),
                "exit_code": exit_code,
            },
        }
        progress = payload.get("progress")
        if isinstance(progress, dict):
            public_progress = {
                key: progress[key]
                for key in ("kind", "current", "total", "percent", "unit", "label")
                if key in progress
            }
            if public_progress:
                event["progress"] = public_progress
        events.append(event)
    return sorted(events, key=lambda item: (item["source_instance_id"], item["source_seq"]))


def group_monitor_events(
    events: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        source_id = str(event.get("source_instance_id") or "")
        if not _SAFE_ID.fullmatch(source_id):
            continue
        grouped.setdefault(source_id, []).append(dict(event))
    for values in grouped.values():
        values.sort(key=lambda item: int(item["source_seq"]))
    return grouped


def task_monitor_cursor(job: dict[str, Any]) -> int:
    """Return the durable global sequence cursor for the active journal."""

    snapshot = job.get("snapshot") if isinstance(job.get("snapshot"), dict) else {}
    monitor_sync = (
        snapshot.get("monitor_sync")
        if isinstance(snapshot.get("monitor_sync"), dict)
        else {}
    )
    try:
        return max(0, int(monitor_sync.get("last_source_seq") or 0))
    except (TypeError, ValueError):
        return 0


def bootstrap_remote_task_monitor(
    *,
    store: Any,
    job: dict[str, Any],
    adapter: Any,
    interval_seconds: int = 15,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Start the observer without transferring lifecycle ownership to it."""

    job_id = str(job["job_id"])
    paths = remote_task_monitor_paths(job_id)
    timeout = provider_query_timeout_seconds(timeout_seconds)
    try:
        result = adapter.run_command(
            job["external_id"],
            build_monitor_bootstrap_command(job_id, interval_seconds=interval_seconds),
            user="root",
            timeout_seconds=timeout,
        )
        exit_code = result.get("exit_code")
        if exit_code not in (None, 0):
            raise RuntimeError(f"monitor bootstrap exited with code {exit_code}")
        output = str(result.get("stdout") or "").strip()
        monitor_status = "active" if "MONITOR_" in output or exit_code in (None, 0) else "unknown"
        monitor = {
            "protocol": MONITOR_PROTOCOL,
            "status": monitor_status,
            "events_path": paths.events,
            "state_path": paths.state,
            "progress_path": paths.progress,
            "bootstrapped_at": time.time(),
        }
    except Exception as exc:
        monitor = {
            "protocol": MONITOR_PROTOCOL,
            "status": "bootstrap_failed",
            "reason": str(exc),
            "events_path": paths.events,
            "state_path": paths.state,
            "progress_path": paths.progress,
            "bootstrapped_at": time.time(),
        }
    return store.merge_observation(
        job_id,
        snapshot={"remote_monitor": monitor},
        error=job.get("error"),
    )


def sync_remote_task_monitor(
    *,
    store: Any,
    job: dict[str, Any],
    adapter: Any,
    limit: int = 500,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Pull and ingest one bounded, replay-safe Sandbox journal page."""

    job_id = str(job["job_id"])
    cursor = task_monitor_cursor(job)
    timeout = provider_query_timeout_seconds(timeout_seconds)
    try:
        result = adapter.run_command(
            job["external_id"],
            build_monitor_sync_command(job_id, after_sequence=cursor, limit=limit),
            user="root",
            timeout_seconds=timeout,
        )
        exit_code = result.get("exit_code")
        if exit_code not in (None, 0):
            raise RuntimeError(f"monitor sync exited with code {exit_code}")
        events = parse_monitor_events(str(result.get("stdout") or ""))
    except Exception as exc:
        snapshot = job.get("snapshot") if isinstance(job.get("snapshot"), dict) else {}
        monitor = dict(snapshot.get("remote_monitor") or {})
        monitor.update({"status": "sync_failed", "sync_error": str(exc), "last_sync_at": time.time()})
        return store.merge_observation(
            job_id,
            snapshot={"remote_monitor": monitor},
            error=job.get("error"),
        )

    updated = job
    for source_id, source_events in group_monitor_events(events).items():
        updated = store.ingest_task_monitor_events(
            job_id,
            source_instance_id=source_id,
            events=source_events,
        )
    return updated
