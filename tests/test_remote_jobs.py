from __future__ import annotations

import pytest

from matcreator.control_plane.remote_jobs import (
    RemoteJobStore,
    build_remote_job_view,
)
from matcreator.control_plane.remote_task_contract import build_remote_task_envelope


def test_remote_job_is_idempotent_and_emits_events(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
        node_id="node-1",
        step_number=1,
        specification={"template": "doc-compiler"},
    )
    replay = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
    )

    assert replay["job_id"] == job["job_id"]
    assert job["status"] == "created"
    assert job["specification"] == {"template": "doc-compiler"}
    assert store.list_events(job["job_id"]) == [
        {
            "event_id": 1,
            "event_type": "created",
            "payload": {"status": "created"},
            "created_at": pytest.approx(job["created_at"]),
        }
    ]


def test_remote_job_tracks_provider_state_with_revision_check(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
    )
    submitting = store.transition_job(job["job_id"], "submitting")
    running = store.transition_job(
        job["job_id"],
        "running",
        external_id="sandbox-123",
        snapshot={"provider_status": "running"},
        expected_revision=submitting["state_revision"],
    )

    assert running["external_id"] == "sandbox-123"
    assert running["snapshot"] == {"provider_status": "running"}
    assert running["state_revision"] == 2
    with pytest.raises(RuntimeError, match="revision changed"):
        store.transition_job(job["job_id"], "succeeded", expected_revision=0)


def test_remote_job_rejects_invalid_transition(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
    )

    with pytest.raises(ValueError, match="Illegal remote job transition"):
        store.transition_job(job["job_id"], "collected")


def test_remote_job_records_observations_without_status_change(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
    )
    running = store.transition_job(job["job_id"], "submitting")
    running = store.transition_job(job["job_id"], "running")

    observed = store.record_observation(
        job["job_id"],
        snapshot={"provider_status": "reachable"},
        expected_revision=running["state_revision"],
    )

    assert observed["status"] == "running"
    assert observed["snapshot"] == {"provider_status": "reachable"}
    assert observed["state_revision"] == 3
    assert store.list_events(job["job_id"])[-1]["event_type"] == "observed"


def test_remote_job_records_user_control_without_changing_provider_status(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="e2b",
        idempotency_key="session-1:node-1:attempt-1",
    )
    submitting = store.transition_job(job["job_id"], "submitting")
    running = store.transition_job(
        job["job_id"], "running", expected_revision=submitting["state_revision"]
    )

    store.record_user_control(running["job_id"], "pause")

    assert store.get_job(running["job_id"])["status"] == "running"
    assert store.list_events(running["job_id"])[-1]["payload"] == {
        "action": "pause",
        "source": "ui",
    }


def test_merge_specification_adds_presentation_without_changing_status(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_sandbox",
        idempotency_key="session-1:node-1:attempt-1",
        specification={"template": "md-runtime"},
    )

    updated = store.merge_specification(
        job["job_id"],
        {
            "presentation": {
                "version": "mc.remote-task-presentation.v1",
                "workload_kind": "md",
                "task_type": "Molecular dynamics",
                "phase_plan": ["prepare", "execute", "validate"],
            }
        },
    )

    assert updated["status"] == "created"
    assert updated["specification"]["template"] == "md-runtime"
    assert updated["specification"]["presentation"]["workload_kind"] == "md"
    assert store.list_events(job["job_id"])[-1] == {
        "event_id": 2,
        "event_type": "metadata_updated",
        "payload": {"keys": ["presentation"]},
        "created_at": pytest.approx(updated["updated_at"]),
    }


def test_remote_job_view_legacy_record_degrades_to_generic_state() -> None:
    view = build_remote_job_view(
        {
            "job_id": "job-1",
            "node_id": "step-1",
            "provider": "bohr_sandbox",
            "status": "running",
            "specification": {},
            "snapshot": {"provider_status": "reachable"},
        },
        capabilities={"pause"},
    )

    assert view["version"] == "mc.remote-job-view.v1"
    assert view["presentation_version"] is None
    assert view["task_type"] == "Remote task"
    assert view["workload_kind"] == "remote_task"
    assert view["current_phase"] == "ready"
    assert view["phase_plan"] == []
    assert view["show_progress"] is False
    assert view["lifecycle_status"] == "running"
    assert view["actions"] == {"refresh": True, "pause": True, "terminate": True}


def test_remote_job_view_uses_versioned_workload_phase_without_mutating_lifecycle() -> None:
    view = build_remote_job_view(
        {
            "job_id": "job-md",
            "provider": "bohr_sandbox",
            "status": "running",
            "specification": {
                "presentation": {
                    "version": "mc.remote-task-presentation.v1",
                    "workload_kind": "md",
                    "task_type": "Molecular dynamics",
                    "phase_plan": [
                        {"key": "prepare", "label": "Preparing", "group": "preparation"},
                        {"key": "execute", "label": "Running MD", "group": "execution"},
                        {"key": "validate", "label": "Validating", "group": "completion"},
                    ],
                }
            },
            "snapshot": {
                "monitor_sync": {
                    "latest_event": {
                        "current_phase": "execute",
                        "task_status": "running",
                        "execution": {"status": "running", "process_active": True},
                        "progress": {"current": 40, "total": 100, "unit": "steps"},
                        "validation": {"status": "pending"},
                    }
                }
            },
        }
    )

    assert view["lifecycle_status"] == "running"
    assert view["presentation_version"] == "mc.remote-task-presentation.v1"
    assert view["workload_kind"] == "md"
    assert view["task_type"] == "Molecular dynamics"
    assert view["phase"] == "execute"
    assert view["current_phase"] == "execute"
    assert view["phase_label"] == "Running MD"
    assert view["show_progress"] is True
    assert view["progress"] == {"current": 40, "total": 100, "unit": "steps"}
    assert view["validation"] == {"status": "pending"}


def test_remote_job_view_does_not_regress_comparable_published_progress() -> None:
    view = build_remote_job_view(
        {
            "job_id": "job-probe",
            "provider": "bohr_sandbox",
            "status": "running",
            "snapshot": {
                "progress": {
                    "kind": "heartbeat",
                    "current": 3,
                    "total": 3,
                    "percent": 100.0,
                    "unit": "heartbeat",
                    "updated_at": 30.0,
                },
                "monitor_sync": {
                    "latest_event": {
                        "event_type": "execution_exited",
                        "task_status": "completed",
                        "execution": {
                            "status": "completed",
                            "process_active": False,
                            "exit_code": 0,
                        },
                        "progress": {
                            "current": 1,
                            "total": 3,
                            "percent": 33.33,
                            "unit": "heartbeat",
                        },
                    }
                },
            },
        }
    )

    assert view["execution_status"] == "completed"
    assert view["progress"] == {
        "current": 3,
        "total": 3,
        "percent": 100.0,
        "unit": "heartbeat",
        "updated_at": 30.0,
    }


def test_remote_job_view_keeps_monitor_precedence_for_incompatible_progress() -> None:
    view = build_remote_job_view(
        {
            "job_id": "job-stages",
            "provider": "bohr_sandbox",
            "status": "running",
            "snapshot": {
                "progress": {"current": 100, "total": 100, "unit": "prepare_files"},
                "monitor_sync": {
                    "latest_event": {
                        "progress": {"current": 1, "total": 3, "unit": "heartbeat"}
                    }
                },
            },
        }
    )

    assert view["progress"] == {"current": 1, "total": 3, "unit": "heartbeat"}


def test_remote_job_view_preserves_canonical_contract_phases_and_progress_semantics() -> None:
    contract = build_remote_task_envelope(
        task_run_id="md-probe-1",
        attempt=1,
        workload_profile="md",
    )
    view = build_remote_job_view(
        {
            "job_id": "job-md",
            "provider": "bohr_sandbox",
            "status": "running",
            "specification": {"task_contract": contract},
            "snapshot": {
                "monitor_sync": {
                    "latest_event": {
                        "current_phase": "execution",
                        "task_status": "running",
                        "progress": {"current": 8, "total": 20, "unit": "steps"},
                    }
                }
            },
        }
    )

    execution_phase = next(
        phase for phase in view["phase_plan"] if phase["phase"] == "execution"
    )
    assert view["workload_kind"] == "md"
    assert view["current_phase"] == "execution"
    assert view["show_progress"] is True
    assert execution_phase == {
        "key": "execution",
        "phase": "execution",
        "label": "Run molecular dynamics",
        "progress_applicable": True,
        "show_progress": True,
    }


def test_task_monitor_event_ingest_is_atomic_deduplicated_and_public(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_sandbox",
        idempotency_key="session-1:node-1:attempt-1",
    )
    event = {
        "schema_version": "mc.task-event.v1",
        "source_instance_id": "sandbox-monitor-1",
        "source_seq": 1,
        "event_type": "phase_changed",
        "occurred_at": "2026-08-28T12:00:00Z",
        "current_phase": "prepare",
        "task_status": "running",
        "progress": {"current": 0, "total": 100, "unit": "steps", "log": "hidden"},
        "log": "arbitrary remote log body must not enter the public snapshot",
    }

    ingested = store.ingest_task_monitor_events(
        job["job_id"], source_instance_id="sandbox-monitor-1", events=[event]
    )
    revision = ingested["state_revision"]
    replay = store.ingest_task_monitor_events(
        job["job_id"], source_instance_id="sandbox-monitor-1", events=[event]
    )

    assert replay["state_revision"] == revision
    sync = replay["snapshot"]["monitor_sync"]
    assert sync["last_source_seq"] == 1
    assert sync["sources"]["sandbox-monitor-1"]["last_source_seq"] == 1
    assert "log" not in sync["latest_event"]
    assert "log" not in sync["latest_event"]["progress"]
    monitor_events = [
        item for item in store.list_events(job["job_id"])
        if item["event_type"] == "task_monitor_event"
    ]
    assert len(monitor_events) == 1
    assert monitor_events[0]["payload"]["source_instance_id"] == "sandbox-monitor-1"
    assert monitor_events[0]["payload"]["source_seq"] == 1


def test_task_monitor_event_ingest_rejects_sequence_gaps_without_partial_write(tmp_path) -> None:
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    job = store.create_job(
        owner_id="alice",
        session_id="session-1",
        provider="bohr_sandbox",
        idempotency_key="session-1:node-1:attempt-1",
    )

    with pytest.raises(ValueError, match="sequence gap"):
        store.ingest_task_monitor_events(
            job["job_id"],
            source_instance_id="sandbox-monitor-1",
            events=[
                {
                    "schema_version": "mc.task-event.v1",
                    "source_instance_id": "sandbox-monitor-1",
                    "source_seq": 2,
                    "event_type": "heartbeat",
                }
            ],
        )

    unchanged = store.get_job(job["job_id"])
    assert unchanged["state_revision"] == 0
    assert store.list_events(job["job_id"])[-1]["event_type"] == "created"
