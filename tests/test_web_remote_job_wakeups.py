from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest
import httpx
from fastapi import HTTPException

from matcreator.control_plane.remote_job_monitor import RemoteJobMonitor
from matcreator.control_plane.remote_job_service import RemoteJobService
from matcreator.control_plane.remote_jobs import RemoteJobStore
from matcreator.control_plane.runs import ManagedRunRegistry
from test_web_session_access import _load_web_main


@pytest.fixture
def harness(monkeypatch, tmp_path):
    web = _load_web_main(monkeypatch, tmp_path / "home")
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    monkeypatch.setattr(web, "_remote_job_store", store)
    monkeypatch.setattr(web, "_remote_job_service", RemoteJobService(store))
    monkeypatch.setattr(web, "_run_registry", ManagedRunRegistry())
    monkeypatch.setattr(web, "_cancellation_workspace_root", lambda *args: tmp_path)
    monkeypatch.setattr(web, "is_cancellation_requested", lambda *args, **kwargs: False)
    session_db = tmp_path / "session.db"
    with sqlite3.connect(session_db) as connection:
        connection.executescript(
            "CREATE TABLE sessions (app_name TEXT, user_id TEXT, id TEXT);"
            "CREATE TABLE events (app_name TEXT, user_id TEXT, session_id TEXT, event_data TEXT);"
        )
        connection.execute("INSERT INTO sessions VALUES (?, ?, ?)", (web.APP_NAME, "alice", "session-1"))
    monkeypatch.setattr(web, "_iter_session_db_paths", lambda *args: iter([("alice", session_db)]))
    job = store.create_job(
        owner_id="alice", session_id="session-1", node_id="relax",
        provider="bohr_batchjob", idempotency_key="tracked-job", specification={},
    )
    store.transition_job(job["job_id"], "submitting")
    store.transition_job(job["job_id"], "succeeded", external_id="batch-123")
    notification = store.list_pending_notifications()[0]
    return SimpleNamespace(web=web, store=store, session_db=session_db, job=job, notification=notification)


def test_idle_session_receives_one_managed_agent_turn(harness, monkeypatch):
    async def exercise():
        web = harness.web
        captured = []
        monkeypatch.setattr(web, "_target_url_for_user", _target)

        async def produce(run, payload, target_url, *, started):
            captured.append(payload)
            assert target_url == "http://worker.example"
            await web._run_registry.publish(run, 'data: {"author":"agent"}\n\n')
            started.set()

        monkeypatch.setattr(web, "_produce_managed_run", produce)
        monitor = RemoteJobMonitor(
            harness.store, web._remote_job_service, on_job_finished=web._resume_remote_job_session,
        )
        await monitor.reconcile_once()
        await monitor.reconcile_once()

        assert len(captured) == 1
        payload = captured[0]
        assert payload["user_id"] == "alice"
        assert payload["session_id"] == "session-1"
        text = payload["new_message"]["parts"][0]["text"]
        assert harness.notification["notification_id"] in text
        assert harness.job["job_id"] in text
        assert "Do not submit a replacement" in text
        record = harness.store.list_notifications()[0]
        assert record["delivery_status"] == "delivered"
        assert web._run_registry.get(record["run_id"]) is not None
        await web._run_registry.shutdown()

    asyncio.run(exercise())


async def _target(owner_id):
    assert owner_id == "alice"
    return "http://worker.example"


def test_busy_session_is_deferred_without_losing_notification(harness, monkeypatch):
    async def exercise():
        release = asyncio.Event()

        async def producer(run):
            await release.wait()

        run = await harness.web._run_registry.start(
            owner_id="alice", session_id="session-1", producer=producer,
        )
        assert await harness.web._resume_remote_job_session(harness.notification) is None
        assert harness.store.list_pending_notifications()
        release.set()
        await run.task

    asyncio.run(exercise())


def test_wakeup_defers_through_the_post_run_handoff_grace_window(harness, monkeypatch):
    async def exercise():
        web = harness.web

        async def producer(run):
            return None

        run = await web._run_registry.start(
            owner_id="alice", session_id="session-1", producer=producer,
        )
        await run.task
        # The client is still handing the just-finished turn's live DOM off to
        # durable history; a wakeup starting inside that window races it, so
        # the notification is deferred (monitor retries a None result).
        assert await web._resume_remote_job_session(harness.notification) is None
        assert harness.store.list_pending_notifications()

        run.updated_at -= 60
        captured = []
        monkeypatch.setattr(web, "_target_url_for_user", _target)

        async def produce(managed_run, payload, target_url, *, started):
            captured.append(payload)
            await web._run_registry.publish(managed_run, 'data: {"author":"agent"}\n\n')
            started.set()

        monkeypatch.setattr(web, "_produce_managed_run", produce)
        assert await web._resume_remote_job_session(harness.notification) is not None
        assert len(captured) == 1
        await web._run_registry.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("reason", ["session-stop", "job-stop", "cancel-file", "deleted-session"])
def test_user_stops_and_deleted_sessions_never_wake_agent(harness, monkeypatch, reason):
    if reason == "session-stop":
        harness.store.suppress_session_notifications("alice", "session-1")
    elif reason == "job-stop":
        harness.store.record_user_control(harness.job["job_id"], "terminate")
    elif reason == "cancel-file":
        monkeypatch.setattr(harness.web, "is_cancellation_requested", lambda *args, **kwargs: True)
    else:
        with sqlite3.connect(harness.session_db) as connection:
            connection.execute("DELETE FROM sessions")

    async def fail_start(**kwargs):
        pytest.fail("An explicitly stopped or deleted session must not restart")

    monkeypatch.setattr(harness.web, "_start_managed_run", fail_start)
    assert asyncio.run(harness.web._resume_remote_job_session(harness.notification)) is None
    assert not harness.store.list_pending_notifications()


def test_session_lookup_and_receipt_are_owner_scoped(harness):
    assert harness.web._remote_job_session_exists("alice", "session-1")
    assert not harness.web._remote_job_session_exists("bob", "session-1")
    _write_receipt(harness)
    assert harness.web._remote_job_notification_receipt(
        "alice", "session-1", harness.notification["notification_id"],
    ) == "accepted-invocation"
    assert harness.web._remote_job_notification_receipt(
        "bob", "session-1", harness.notification["notification_id"],
    ) is None


def _write_receipt(harness):
    text = (
        "REMOTE JOB STATUS UPDATE from the harness.\n"
        f"Notification ID: {harness.notification['notification_id']}\n"
    )
    event = {
        "author": "user", "invocationId": "accepted-invocation",
        "content": {"role": "user", "parts": [{"text": text}]},
    }
    with sqlite3.connect(harness.session_db) as connection:
        connection.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?)",
            (harness.web.APP_NAME, "alice", "session-1", json.dumps(event)),
        )


def test_persisted_receipt_prevents_duplicate_after_monitor_restart(harness, monkeypatch):
    _write_receipt(harness)

    async def fail_start(**kwargs):
        pytest.fail("A notification already accepted by ADK must not be resent")

    monkeypatch.setattr(harness.web, "_start_managed_run", fail_start)
    monitor = RemoteJobMonitor(
        harness.store, harness.web._remote_job_service,
        on_job_finished=harness.web._resume_remote_job_session,
    )
    asyncio.run(monitor.reconcile_once())
    assert harness.store.list_notifications()[0]["run_id"] == "accepted-invocation"
    assert not harness.store.list_pending_notifications()


def test_upstream_start_failure_stays_retryable(harness, monkeypatch):
    async def exercise():
        web = harness.web
        monkeypatch.setattr(web, "_target_url_for_user", _target)

        async def produce(*args, **kwargs):
            raise RuntimeError("worker is unavailable")

        monkeypatch.setattr(web, "_produce_managed_run", produce)
        monitor = RemoteJobMonitor(
            harness.store, web._remote_job_service, on_job_finished=web._resume_remote_job_session,
        )
        await monitor.reconcile_once()
        notification = harness.store.list_notifications()[0]
        assert notification["delivery_status"] == "pending"
        assert "worker is unavailable" in notification["last_error"]
        assert notification["run_id"] is None
        await web._run_registry.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("stream", ["", "data: [DONE]\n\n", ": keepalive\n\n", "data: \n\n"])
def test_empty_or_control_only_stream_does_not_acknowledge_notification(harness, monkeypatch, stream):
    async def exercise():
        web = harness.web
        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=stream))
        monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: client_type(transport=transport, **kwargs))
        monkeypatch.setattr(web, "_target_url_for_user", _target)
        monitor = RemoteJobMonitor(
            harness.store, web._remote_job_service, on_job_finished=web._resume_remote_job_session,
        )
        await monitor.reconcile_once()
        notification = harness.store.list_notifications()[0]
        assert notification["delivery_status"] == "pending"
        assert notification["failures"] == 1
        assert "before accepting" in notification["last_error"]
        await web._run_registry.shutdown()

    asyncio.run(exercise())


def test_stop_during_worker_startup_prevents_agent_invocation(harness, monkeypatch):
    async def exercise():
        web = harness.web

        async def target(owner_id):
            harness.store.suppress_session_notifications(owner_id, "session-1")
            return "http://worker.example"

        async def forbidden_producer(*args, **kwargs):
            pytest.fail("Cancellation during worker startup must stop delivery")

        monkeypatch.setattr(web, "_target_url_for_user", target)
        monkeypatch.setattr(web, "_produce_managed_run", forbidden_producer)
        monitor = RemoteJobMonitor(
            harness.store, web._remote_job_service, on_job_finished=web._resume_remote_job_session,
        )
        await monitor.reconcile_once()
        assert harness.store.list_notifications()[0]["delivery_status"] == "suppressed"
        assert not web._run_registry.active_runs()

    asyncio.run(exercise())


def test_notification_delivery_is_inspectable_only_for_owning_job(harness):
    response = asyncio.run(harness.web.list_session_remote_job_events(
        "session-1", harness.job["job_id"], user_id="alice", after=0,
    ))
    payload = json.loads(response.body)
    assert payload["notifications"][0]["notification_id"] == harness.notification["notification_id"]
    with pytest.raises(HTTPException):
        asyncio.run(harness.web.list_session_remote_job_events(
            "session-1", harness.job["job_id"], user_id="bob", after=0,
        ))


def test_job_poll_exposes_root_activity_and_completion_revision(harness):
    async def exercise():
        web = harness.web
        release = asyncio.Event()

        async def producer(run):
            await release.wait()

        async def poll(owner="alice"):
            response = await web.list_session_remote_jobs("session-1", user_id=owner)
            return json.loads(response.body)

        before = await poll()
        assert before["active_run"] is None
        run = await web._run_registry.start(owner_id="alice", session_id="session-1", producer=producer)
        active = await poll()
        assert active["active_run"]["run_id"] == run.run_id
        assert (await poll("bob"))["active_run"] is None
        assert (await poll("bob"))["activity_revision"] is None
        release.set()
        await run.task
        completed = await poll()
        assert completed["active_run"] is None
        assert completed["activity_revision"] != active["activity_revision"]
        assert (await poll())["activity_revision"] == completed["activity_revision"]
        web._run_registry = ManagedRunRegistry()
        assert (await poll())["activity_revision"] == before["activity_revision"]

    asyncio.run(exercise())


def test_local_harness_wires_callback_and_stops_cleanly(harness):
    async def exercise():
        web = harness.web
        task = asyncio.create_task(web._run_remote_job_monitor())
        await asyncio.sleep(0)
        assert web._remote_job_monitor.on_job_finished is web._resume_remote_job_session
        web._remote_job_monitor.stop()
        await asyncio.wait_for(task, timeout=2)

    # Avoid dispatching the fixture notification while verifying lifecycle wiring.
    harness.store.suppress_session_notifications("alice", "session-1")
    asyncio.run(exercise())


def test_job_control_suppresses_wakeup_before_provider_call(harness, monkeypatch):
    def terminate(job_id):
        assert harness.store.list_events(job_id)[-1]["event_type"] == "user_control"
        assert not harness.store.list_pending_notifications()
        raise ValueError("provider unavailable")

    monkeypatch.setattr(
        harness.web, "_remote_job_service_for_owner",
        lambda owner_id: SimpleNamespace(terminate_job=terminate),
    )
    with pytest.raises(HTTPException, match="provider unavailable"):
        asyncio.run(harness.web.terminate_session_remote_job(
            "session-1", harness.job["job_id"], user_id="alice",
        ))
    assert not harness.store.list_pending_notifications()


@pytest.mark.parametrize("user_id", ["alice", ""])
def test_session_stop_is_persisted_before_provider_operations(harness, monkeypatch, user_id):
    flags = []

    def pause(**kwargs):
        assert flags == ["cancel-requested"]
        assert harness.store.notifications_suppressed(
            "alice", "session-1", job_id=harness.job["job_id"],
        )
        return []

    monkeypatch.setattr(
        harness.web, "request_cancellation", lambda *args, **kwargs: flags.append("cancel-requested"),
    )
    monkeypatch.setattr(
        harness.web, "_remote_job_service_for_owner",
        lambda owner_id: SimpleNamespace(pause_active_session_jobs=pause),
    )
    monkeypatch.setattr(
        harness.web, "AgentGraphLogger",
        lambda session_id: SimpleNamespace(mark_running_nodes_cancelled=lambda **kwargs: None),
    )
    asyncio.run(harness.web.cancel_session_execution("session-1", user_id=user_id, reason="user_requested"))
    assert not harness.store.list_pending_notifications()


def test_server_owner_monitors_start_independently_and_join_on_stop(harness, monkeypatch, tmp_path):
    async def exercise():
        web = harness.web
        monkeypatch.setattr(web, "_MATCREATOR_MODE", "server")
        monkeypatch.setattr(web, "_USERS_DATA_ROOT", tmp_path / "users")
        for owner in ("alice", "bob"):
            db = web._USERS_DATA_ROOT / owner / ".matcreator" / ".adk" / "remote-jobs.db"
            db.parent.mkdir(parents=True)
            db.touch()
        monkeypatch.setattr(web, "_remote_job_store_for_owner", lambda owner: owner)
        monkeypatch.setattr(web, "_remote_job_service_for_owner", lambda owner: owner)
        ready = asyncio.Event()
        started = []
        stopped = []

        class Monitor:
            def __init__(self, store, service, *, on_job_finished, callback_timeout_seconds):
                assert on_job_finished is web._resume_remote_job_session
                self.owner = store
                self.stop_event = asyncio.Event()

            async def run(self):
                started.append(self.owner)
                if len(started) == 2:
                    ready.set()
                await self.stop_event.wait()
                stopped.append(self.owner)

            def stop(self):
                self.stop_event.set()

        monkeypatch.setattr(web, "RemoteJobMonitor", Monitor)
        task = asyncio.create_task(web._run_remote_job_monitor())
        await asyncio.wait_for(ready.wait(), timeout=2)
        web._remote_job_monitor_stop.set()
        await asyncio.wait_for(task, timeout=2)
        assert set(started) == set(stopped) == {"alice", "bob"}

    asyncio.run(exercise())
