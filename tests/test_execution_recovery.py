from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from matcreator.agents.execution_agent.recovery import (
    finish_node_attempt,
    heartbeat_node_attempt,
    record_remote_job_reference,
    reconcile_recovery_state,
    start_node_attempt,
)
from matcreator.control_plane.remote_jobs import RemoteJobStore


def test_reconcile_resets_stale_running_attempt_to_pending(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    attempt["heartbeat_at"] = old_time
    attempt["pid"] = -1
    heartbeat_node_attempt(attempt)

    state = {
        "session_id": "session-1",
        "execution_graph": {
            "nodes": {
                "node-a": {"status": "running", "action": "do work"},
            },
            "edges": [],
        }
    }

    recovered = reconcile_recovery_state(state, tmp_path, stale_after_seconds=1, recovery_base_dir=recovery_dir)

    assert recovered == [{"node_id": "node-a", "action": "reset_stale_running", "status": "pending"}]
    node = state["execution_graph"]["nodes"]["node-a"]
    assert node["status"] == "pending"
    assert node["recovery"]["status"] == "stale"


def test_reconcile_completed_success_repairs_graph_status(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    finish_node_attempt(
        attempt,
        status="success",
        result={"concise_summary": "work completed"},
        artifacts=[str(tmp_path / "result.txt")],
    )

    state = {
        "session_id": "session-1",
        "execution_graph": {
            "nodes": {
                "node-a": {"status": "running", "action": "do work"},
            },
            "edges": [],
        }
    }

    recovered = reconcile_recovery_state(state, tmp_path, recovery_base_dir=recovery_dir)

    assert recovered == [{"node_id": "node-a", "action": "recover_completed", "status": "success"}]
    node = state["execution_graph"]["nodes"]["node-a"]
    assert node["status"] == "success"
    assert node["result"] == "work completed"
    assert node["recovery"]["status"] == "success"


def test_reconcile_does_not_reopen_terminal_graph_node(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    attempt["pid"] = -1
    heartbeat_node_attempt(attempt)

    state = {
        "session_id": "session-1",
        "execution_graph": {
            "nodes": {
                "node-a": {"status": "success", "action": "do work"},
            },
            "edges": [],
        }
    }

    recovered = reconcile_recovery_state(state, tmp_path, stale_after_seconds=1, recovery_base_dir=recovery_dir)

    assert recovered == []
    assert state["execution_graph"]["nodes"]["node-a"]["status"] == "success"


def test_reconcile_ignores_other_sessions_with_same_node_id(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    other_attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-2",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do work elsewhere",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    finish_node_attempt(
        other_attempt,
        status="success",
        result={"concise_summary": "other session completed"},
    )

    state = {
        "session_id": "session-1",
        "execution_graph": {
            "nodes": {
                "node-a": {"status": "running", "action": "do work"},
            },
            "edges": [],
        },
    }

    recovered = reconcile_recovery_state(state, tmp_path, recovery_base_dir=recovery_dir)

    assert recovered == []
    node = state["execution_graph"]["nodes"]["node-a"]
    assert node["status"] == "running"
    assert "result" not in node


def test_attempt_record_lives_in_recovery_state_not_workspace(tmp_path):
    workspace_dir = tmp_path / "workspace"
    recovery_dir = tmp_path / "adk-recovery"
    attempt = start_node_attempt(
        workspace_dir=workspace_dir,
        session_id="session-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )

    latest_path = Path(attempt["_latest_path"])

    assert latest_path == recovery_dir.resolve() / "session-1" / "node-a" / "latest.json"
    assert not latest_path.is_relative_to(workspace_dir.resolve())
    assert attempt["workspace_dir"] == str(workspace_dir.resolve())


def test_reconcile_waits_for_active_remote_job_instead_of_resubmitting(tmp_path, monkeypatch):
    recovery_dir = tmp_path / "adk-recovery"
    adk_dir = tmp_path / "adk"
    monkeypatch.setattr("matcreator.agents.execution_agent.recovery.ADK_DIR", adk_dir)
    store = RemoteJobStore(adk_dir / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        node_id="node-a",
        provider="e2b",
        idempotency_key="session-1:node-a:1",
    )
    submitting = store.transition_job(job["job_id"], "submitting")
    running = store.transition_job(
        job["job_id"], "running", external_id="sandbox-123", expected_revision=submitting["state_revision"]
    )
    attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="do remote work",
        suggested_skills=["e2b"],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    record_remote_job_reference(
        session_id="session-1",
        node_id="node-a",
        job_id=running["job_id"],
        provider="e2b",
        external_id="sandbox-123",
        recovery_base_dir=recovery_dir,
    )
    attempt["pid"] = -1
    heartbeat_node_attempt(attempt)
    state = {
        "session_id": "session-1",
        "execution_graph": {"nodes": {"node-a": {"status": "running"}}, "edges": []},
    }

    recovered = reconcile_recovery_state(
        state, tmp_path, stale_after_seconds=1, recovery_base_dir=recovery_dir
    )

    assert recovered == [{"node_id": "node-a", "action": "wait_for_remote_job", "status": "waiting"}]
    node = state["execution_graph"]["nodes"]["node-a"]
    assert node["status"] == "waiting"
    assert node["remote_job"] == {
        "job_id": running["job_id"],
        "provider": "e2b",
        "external_id": "sandbox-123",
        "status": "running",
    }