from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from matcreator.agents.execution_agent.recovery import (
    finish_node_attempt,
    heartbeat_node_attempt,
    record_remote_job_reference,
    reconcile_recovery_state,
    start_node_attempt,
)
from matcreator.agents.execution_graph_state import get_execution_graph, set_execution_graph
from matcreator.control_plane.remote_jobs import RemoteJobStore
from matcreator.agents.thinking_agent.planning import validate_graph
from matcreator.agents.thinking_agent.agent import (
    confirm_plan_and_start_execution,
    resume_execution,
)


def test_validate_graph_assigns_a_new_recovery_identity_each_time():
    context = SimpleNamespace(state={})
    graph = {
        "nodes": {
            "node-a": {
                "node_id": "node-a",
                "label": "Do work",
                "action": "do work",
                "suggested_skills": ["utility"],
            },
        },
        "edges": [],
    }

    first = validate_graph(graph, context)
    second = validate_graph(graph, context)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert first["execution_graph"]["graph_id"]
    assert second["execution_graph"]["graph_id"] != first["execution_graph"]["graph_id"]


def test_confirmation_is_idempotent_and_does_not_reset_started_graph():
    state = {
        "session_id": "confirmation-idempotency-test",
        "execution_graph": [{
            "nodes": {"node-a": {"status": "failed", "result": "first attempt"}},
            "edges": [],
        }],
    }
    context = SimpleNamespace(state=state)

    first = confirm_plan_and_start_execution(context)
    state["execution_graph"][0]["nodes"]["node-a"].update(
        status="success", result="completed after approval"
    )
    second = confirm_plan_and_start_execution(context)

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert state["execution_graph"][0]["nodes"]["node-a"] == {
        "status": "success",
        "result": "completed after approval",
    }


def test_approved_execution_rejects_revalidation_and_resume_is_idempotent():
    graph = {
        "nodes": {
            "node-a": {
                "node_id": "node-a",
                "label": "Do work",
                "action": "do work",
                "suggested_skills": [],
                "status": "pending",
            },
        },
        "edges": [],
    }
    state = {
        "session_id": "approved-plan-test",
        "execution_graph": [graph],
        "execution_approved": True,
    }
    context = SimpleNamespace(state=state)

    validation = validate_graph(graph, context)
    resumed = resume_execution(context)

    assert validation["status"] == "error"
    assert "already approved" in validation["message"]
    assert resumed["status"] == "ok"
    assert "already approved" in resumed["message"]
    assert get_execution_graph(state) == graph


def test_execution_graph_snapshot_replaces_nodes_atomically_with_sqlite_json_patch():
    plan1 = {
        "nodes": {
            "step_fail": {"status": "failed"},
            "step_goodbye": {"status": "blocked"},
        },
        "edges": [["step_fail", "step_goodbye"]],
    }
    plan2 = {
        "nodes": {
            "step_plan2_a": {"status": "pending"},
            "step_plan2_b": {"status": "pending"},
        },
        "edges": [["step_plan2_a", "step_plan2_b"]],
    }
    stored_state: dict = {}
    state_delta: dict = {}
    set_execution_graph(stored_state, plan1)
    set_execution_graph(state_delta, plan2)

    with sqlite3.connect(":memory:") as connection:
        merged_json = connection.execute(
            "SELECT json_patch(?, ?)",
            (json.dumps(stored_state), json.dumps(state_delta)),
        ).fetchone()[0]
    merged_state = json.loads(merged_json)

    assert get_execution_graph(merged_state) == plan2
    assert set(get_execution_graph(merged_state)["nodes"]) == {"step_plan2_a", "step_plan2_b"}


def test_execution_graph_snapshot_preserves_status_and_progress_metadata():
    graph = {
        "graph_id": "plan-2",
        "nodes": {
            "step-a": {
                "status": "failed",
                "result": "command exited with status 1",
                "recovery": {"attempt": 2, "status": "needs_replanning"},
                "progress": {"completed": 3, "total": 5},
                "children": [{"id": "substep-a", "status": "success"}],
            },
        },
        "edges": [],
    }
    state: dict = {}

    set_execution_graph(state, graph)

    assert get_execution_graph(state) == graph
    assert state["execution_graph"] == [graph]


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
    node = get_execution_graph(state)["nodes"]["node-a"]
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
    node = get_execution_graph(state)["nodes"]["node-a"]
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
    assert get_execution_graph(state)["nodes"]["node-a"]["status"] == "success"


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
    node = get_execution_graph(state)["nodes"]["node-a"]
    assert node["status"] == "running"
    assert "result" not in node


def test_reconcile_ignores_attempt_from_earlier_graph_with_same_node_id(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    old_attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        graph_id="plan-1",
        node_id="node-a",
        step_id="execution_0__node_node-a",
        step_number=1,
        action="old work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    finish_node_attempt(
        old_attempt,
        status="needs_replanning",
        result={"replan_reason": "old plan failed"},
    )

    state = {
        "session_id": "session-1",
        "execution_graph": {
            "graph_id": "plan-2",
            "nodes": {
                "node-a": {"status": "pending", "action": "revised work"},
            },
            "edges": [],
        },
    }

    recovered = reconcile_recovery_state(state, tmp_path, recovery_base_dir=recovery_dir)

    assert recovered == []
    node = get_execution_graph(state)["nodes"]["node-a"]
    assert node["status"] == "pending"
    assert "result" not in node


def test_reconcile_applies_attempt_from_current_graph(tmp_path):
    recovery_dir = tmp_path / "adk-recovery"
    attempt = start_node_attempt(
        workspace_dir=tmp_path,
        session_id="session-1",
        graph_id="plan-2",
        node_id="node-a",
        step_id="execution_1__node_node-a",
        step_number=1,
        action="revised work",
        suggested_skills=[],
        prior_context=None,
        recovery_base_dir=recovery_dir,
    )
    finish_node_attempt(
        attempt,
        status="success",
        result={"concise_summary": "new plan completed"},
    )
    state = {
        "session_id": "session-1",
        "execution_graph": {
            "graph_id": "plan-2",
            "nodes": {"node-a": {"status": "running", "action": "revised work"}},
            "edges": [],
        },
    }

    recovered = reconcile_recovery_state(state, tmp_path, recovery_base_dir=recovery_dir)

    assert recovered == [{"node_id": "node-a", "action": "recover_completed", "status": "success"}]
    assert get_execution_graph(state)["nodes"]["node-a"]["status"] == "success"


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
    node = get_execution_graph(state)["nodes"]["node-a"]
    assert node["status"] == "waiting"
    assert node["remote_job"] == {
        "job_id": running["job_id"],
        "provider": "e2b",
        "external_id": "sandbox-123",
        "status": "running",
    }
