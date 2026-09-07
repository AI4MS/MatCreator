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


def _running(store, key="one"):
    job = store.create_job(owner_id="alice", session_id="s", provider="e2b", idempotency_key=key)
    store.transition_job(job["job_id"], "submitting")
    return store.transition_job(job["job_id"], "running", external_id="remote")


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "lost"])
def test_terminal_outbox_is_durable_atomic_and_not_replayed(tmp_path, status):
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = _running(store)
    with pytest.raises(RuntimeError):
        store.transition_job(job["job_id"], status, expected_revision=0)
    assert store.list_notifications() == []
    updated = store.transition_job(job["job_id"], status)
    store.transition_job(job["job_id"], status)
    reopened = RemoteJobStore(store.path)
    notification, = reopened.list_pending_notifications()
    assert notification["state_revision"] == updated["state_revision"]
    assert notification["status"] == status
    assert notification["external_id"] == "remote"
    claim = reopened.claim_notification(notification["notification_id"])
    assert store.claim_notification(notification["notification_id"]) is None
    assert reopened.finish_notification(notification["notification_id"], claim["claim_token"], "run-1")
    assert RemoteJobStore(store.path).list_pending_notifications() == []
    assert store.list_notifications()[0]["run_id"] == "run-1"


def test_outbox_lease_retry_and_stale_ack(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("matcreator.control_plane.remote_jobs.time.time", lambda: clock[0])
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = _running(store)
    store.transition_job(job["job_id"], "succeeded")
    notification, = store.list_pending_notifications()
    nid = notification["notification_id"]
    first = store.claim_notification(nid, lease_seconds=2)
    clock[0] += 3
    second = RemoteJobStore(store.path).claim_notification(nid)
    assert second["attempts"] == 2
    assert not store.finish_notification(nid, first["claim_token"], "stale")
    store.defer_notification(nid, second["claim_token"], delay_seconds=2)
    assert store.list_pending_notifications() == []
    clock[0] += 3
    third = store.claim_notification(nid)
    store.fail_notification(nid, third["claim_token"], "boom", max_attempts=1)
    persisted, = store.list_notifications()
    assert persisted["delivery_status"] == "failed"
    assert persisted["last_error"] == "boom"
    assert persisted["failures"] == 1
    assert store.list_pending_notifications() == []


@pytest.mark.parametrize("control", ["session", "pause", "terminate"])
def test_user_stop_suppresses_pending_and_future_outcomes(tmp_path, control):
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = _running(store)
    store.transition_job(job["job_id"], "succeeded")
    notification, = store.list_pending_notifications()
    claim = store.claim_notification(notification["notification_id"])
    if control == "session":
        store.suppress_session_notifications("alice", "s")
        assert RemoteJobStore(store.path).notifications_suppressed("alice", "s")
    else:
        store.record_user_control(job["job_id"], control)
    assert store.notifications_suppressed("alice", "s", job_id=job["job_id"])
    assert not store.finish_notification(notification["notification_id"], claim["claim_token"], "run")
    store.transition_job(job["job_id"], "failed")
    assert store.list_pending_notifications() == []
    assert len(store.list_notifications()) == 1


def test_local_creation_failure_never_notifies(tmp_path):
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = store.create_job(owner_id="alice", session_id="s", provider="e2b", idempotency_key="one")
    store.transition_job(job["job_id"], "submitting")
    store.transition_job(job["job_id"], "failed")
    assert store.list_notifications() == []


def test_observations_preserve_command_handle_and_completion_is_once(tmp_path):
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = _running(store)
    handle = {"exit_path": "marker.exit", "log_path": "marker.log", "started_at": 1}
    store.merge_observation(job["job_id"], snapshot={"background_command": handle})
    store.record_observation(job["job_id"], snapshot={"provider_status": "reachable"})
    store.transition_job(job["job_id"], "running", snapshot={"provider_status": "running"})
    assert store.get_job(job["job_id"])["snapshot"]["background_command"] == handle
    result = {"exit_code": 0, "running": False, "output_tail": "done"}
    store.record_observation(
        job["job_id"], snapshot={"provider_status": "unreachable"}, error="previous probe failed",
    )
    store.record_command_completion(job["job_id"], handle=handle, result=result)
    store.record_command_completion(job["job_id"], handle=handle, result=result)
    notification, = store.list_pending_notifications()
    assert notification["kind"] == "command"
    assert notification["status"] == "succeeded"
    assert store.get_job(job["job_id"])["status"] == "running"
    assert store.get_job(job["job_id"])["snapshot"]["background_command"] is None
    assert store.get_job(job["job_id"])["snapshot"]["provider_status"] == "reachable"
    assert store.get_job(job["job_id"])["error"] is None
    assert notification["error"] is None


def test_outbox_insert_failure_rolls_back_terminal_transition(tmp_path, monkeypatch):
    store = RemoteJobStore(tmp_path / "jobs.db")
    job = _running(store)

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(store, "_enqueue_notification", fail)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        store.transition_job(job["job_id"], "succeeded")
    assert store.get_job(job["job_id"]) == job
    assert store.list_events(job["job_id"])[-1]["payload"]["to"] == "running"


def test_session_stop_cutoff_allows_newly_approved_jobs_after_restart(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("matcreator.control_plane.remote_jobs.time.time", lambda: clock[0])
    store = RemoteJobStore(tmp_path / "jobs.db")
    old = _running(store, "old")
    clock[0] += 1
    store.suppress_session_notifications("alice", "s")
    clock[0] += 1
    reopened = RemoteJobStore(store.path)
    new = _running(reopened, "new")
    assert reopened.notifications_suppressed("alice", "s", job_id=old["job_id"])
    assert not reopened.notifications_suppressed("alice", "s", job_id=new["job_id"])
    reopened.transition_job(old["job_id"], "failed")
    reopened.transition_job(new["job_id"], "succeeded")
    notification, = reopened.list_pending_notifications()
    assert notification["job_id"] == new["job_id"]
    assert reopened.claim_notification(notification["notification_id"]) is not None
    clock[0] += 1
    reopened.suppress_session_notifications("alice", "s")
    assert reopened.notifications_suppressed("alice", "s", job_id=new["job_id"])
    assert reopened.list_pending_notifications() == []


def test_find_jobs_by_external_id_includes_all_owners_and_terminal_states(tmp_path):
    store = RemoteJobStore(tmp_path / "jobs.db")
    first = _running(store, "first")
    store.transition_job(first["job_id"], "failed")
    other = store.create_job(
        owner_id="bob", session_id="other", provider="e2b", idempotency_key="second",
    )
    store.transition_job(other["job_id"], "submitting")
    store.transition_job(other["job_id"], "running", external_id="remote")
    matches = store.find_jobs_by_external_id(provider="e2b", external_id="remote")
    assert {job["job_id"] for job in matches} == {first["job_id"], other["job_id"]}
    assert {job["status"] for job in matches} == {"failed", "running"}
    assert store.find_jobs_by_external_id(provider="bohr_batchjob", external_id="remote") == []
    assert store.find_jobs_by_external_id(provider="e2b", external_id="missing") == []