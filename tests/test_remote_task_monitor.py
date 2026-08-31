from __future__ import annotations

import base64
import json

import pytest

from matcreator.control_plane.remote_jobs import RemoteJobStore
from matcreator.control_plane.remote_task_monitor import (
    TASK_EVENT_SCHEMA,
    bootstrap_remote_task_monitor,
    build_monitor_bootstrap_command,
    build_monitor_progress_command,
    build_monitor_state_command,
    build_monitor_sync_command,
    build_monitored_launch_command,
    group_monitor_events,
    parse_monitor_events,
    remote_task_monitor_paths,
    sync_remote_task_monitor,
    task_monitor_cursor,
)


def test_monitor_paths_are_stable_and_reject_shell_syntax() -> None:
    paths = remote_task_monitor_paths("abc_123")
    assert paths.events == "/tmp/matcreator-monitor-abc_123.events.jsonl"
    assert paths.command_exit == "/tmp/matcreator-cmd-abc_123.exit"
    with pytest.raises(ValueError):
        remote_task_monitor_paths("abc; rm -rf /")


def test_bootstrap_is_detached_idempotent_and_contains_public_event_protocol() -> None:
    command = build_monitor_bootstrap_command("job-1", interval_seconds=10)
    assert "MONITOR_ACTIVE" in command
    assert "nohup /tmp/matcreator-monitor-job-1.sh" in command
    assert "mc.task-event.v1" not in command  # worker body is base64 encoded
    encoded = command.split("printf '%s' '", 1)[1].split("' | base64 -d", 1)[0]
    worker = base64.b64decode(encoded).decode()
    assert TASK_EVENT_SCHEMA in worker
    assert "source_seq" in worker
    assert "execution_exited" in worker
    assert "sleep \"$interval\"" in worker


def test_monitored_launch_hides_command_and_writes_pid_state() -> None:
    raw = "python -c \"print('secret-ish payload')\""
    command = build_monitored_launch_command("job-1", raw)
    assert raw not in command
    assert base64.b64encode(raw.encode()).decode() in command
    assert "mkdir /tmp/matcreator-monitor-job-1.launch.lock" in command
    assert "ALREADY_LAUNCHED" in command
    assert "/tmp/matcreator-cmd-job-1.exit" in command
    assert "export MC_PROGRESS_PATH=/tmp/matcreator-monitor-job-1.progress" in command
    assert "MC_PROGRESS_MIN_INTERVAL_SECONDS=15" in command
    assert "rm -f /tmp/matcreator-monitor-job-1.progress" in command
    assert "executing\\texecute" in command
    assert "\\t/tmp/matcreator-monitor-job-1.progress\\n" in command
    assert "| sed" not in command
    assert "LAUNCHED:%s" in command


def test_state_and_sync_commands_are_bounded() -> None:
    state = build_monitor_state_command("job-1", task_status="staging", phase="prepare")
    assert "base64 -d" in state
    encoded = state.split("printf '%s' '", 1)[1].split("' | base64 -d", 1)[0]
    decoded = base64.b64decode(encoded).decode()
    assert decoded.startswith("staging\tprepare\t-\t")
    assert decoded.count("\t") == 5
    assert build_monitor_sync_command("job-1", after_sequence=7, limit=20).startswith("sed -n '8,27p'")
    with pytest.raises(ValueError):
        build_monitor_sync_command("job-1", limit=0)
    with pytest.raises(ValueError):
        build_monitor_state_command("job-1", task_status="bad status", phase="prepare")

    progress = build_monitor_progress_command(
        "job-1", kind="md_step", current=25, total=100, unit="step"
    )
    assert "/tmp/matcreator-monitor-job-1.progress.tmp" in progress
    with pytest.raises(ValueError):
        build_monitor_progress_command(
            "job-1", kind="md_step", current=1, total=0, unit="step"
        )


def test_parse_monitor_events_filters_malformed_and_private_fields() -> None:
    valid = {
        "schema_version": TASK_EVENT_SCHEMA,
        "source_instance_id": "source-1",
        "source_seq": 2,
        "observed_at": 12.5,
        "event_type": "heartbeat",
        "task_status": "executing",
        "phase": "execute",
        "process_active": True,
        "exit_code": None,
        "log_bytes": 8,
        "private_log_tail": "must not escape",
        "progress": {"current": 2, "total": 10, "percent": 20, "secret": "drop"},
    }
    text = "\n".join(["not-json", json.dumps({**valid, "source_seq": 0}), json.dumps(valid)])
    events = parse_monitor_events(text)
    assert events == [
        {
            "schema_version": TASK_EVENT_SCHEMA,
            "source_instance_id": "source-1",
            "source_seq": 2,
            "observed_at": 12.5,
            "occurred_at": 12.5,
            "event_type": "heartbeat",
            "task_status": "executing",
            "phase": "execute",
            "process_active": True,
            "exit_code": None,
            "log_bytes": 8,
            "execution": {
                "status": "executing",
                "process_active": True,
                "exit_code": None,
            },
            "progress": {"current": 2, "total": 10, "percent": 20},
        }
    ]
    assert group_monitor_events(events) == {"source-1": events}


def test_bootstrap_and_sync_persist_replayable_events_without_status_transition(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    created = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_sandbox",
        idempotency_key="probe-1",
    )
    submitting = store.transition_job(created["job_id"], "submitting")
    running = store.transition_job(
        created["job_id"],
        "running",
        external_id="sandbox-1",
        expected_revision=submitting["state_revision"],
    )

    class Adapter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def run_command(
            self,
            external_id,
            command,
            *,
            user="root",
            timeout_seconds=None,
        ):
            self.calls.append((command, timeout_seconds))
            if "base64 -d" in command:
                return {"stdout": "MONITOR_STARTED:17\n", "stderr": "", "exit_code": 0}
            event = {
                "schema_version": TASK_EVENT_SCHEMA,
                "source_instance_id": "source-1",
                "source_seq": 1,
                "observed_at": 20,
                "event_type": "heartbeat",
                "task_status": "executing",
                "phase": "execute",
                "process_active": True,
                "exit_code": None,
                "log_bytes": 4,
                "progress": {"kind": "probe_step", "current": 1, "total": 3, "percent": 33.33, "unit": "step"},
            }
            return {"stdout": json.dumps(event) + "\n", "stderr": "", "exit_code": 0}

    adapter = Adapter()
    bootstrapped = bootstrap_remote_task_monitor(store=store, job=running, adapter=adapter)
    assert bootstrapped["status"] == "running"
    assert bootstrapped["snapshot"]["remote_monitor"]["status"] == "active"

    synced = sync_remote_task_monitor(store=store, job=bootstrapped, adapter=adapter)
    assert synced["status"] == "running"
    assert task_monitor_cursor(synced) == 1
    monitor_events = [
        event for event in store.list_events(synced["job_id"])
        if event["event_type"] == "task_monitor_event"
    ]
    assert len(monitor_events) == 1
    assert monitor_events[0]["payload"]["phase"] == "execute"
    assert [timeout for _, timeout in adapter.calls] == [30.0, 30.0]


def test_bootstrap_and_sync_timeout_are_audited_without_terminal_transition(
    tmp_path,
) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    created = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_sandbox",
        idempotency_key="probe-timeout",
    )
    submitting = store.transition_job(created["job_id"], "submitting")
    running = store.transition_job(
        created["job_id"],
        "running",
        external_id="sandbox-1",
        expected_revision=submitting["state_revision"],
    )

    class TimeoutAdapter:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        def run_command(
            self,
            external_id,
            command,
            *,
            user="root",
            timeout_seconds=None,
        ):
            self.timeouts.append(timeout_seconds)
            raise TimeoutError("provider command deadline exceeded")

    adapter = TimeoutAdapter()
    bootstrapped = bootstrap_remote_task_monitor(
        store=store,
        job=running,
        adapter=adapter,
        timeout_seconds=0.25,
    )

    assert bootstrapped["status"] == "running"
    assert bootstrapped["snapshot"]["remote_monitor"]["status"] == "bootstrap_failed"
    assert "deadline exceeded" in bootstrapped["snapshot"]["remote_monitor"]["reason"]

    synced = sync_remote_task_monitor(
        store=store,
        job=bootstrapped,
        adapter=adapter,
        timeout_seconds=0.5,
    )
    assert synced["status"] == "running"
    assert synced["snapshot"]["remote_monitor"]["status"] == "sync_failed"
    assert "deadline exceeded" in synced["snapshot"]["remote_monitor"]["sync_error"]
    assert adapter.timeouts == [0.25, 0.5]
    assert all(
        event["payload"].get("to") not in {"failed", "lost", "terminated"}
        for event in store.list_events(running["job_id"])
    )
