from __future__ import annotations

import pytest

from matcreator.control_plane.remote_jobs import RemoteJobStore


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