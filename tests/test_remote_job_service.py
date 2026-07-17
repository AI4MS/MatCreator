from __future__ import annotations

from matcreator.control_plane.remote_job_service import E2BConnectionConfig, RemoteJobService
from matcreator.control_plane.remote_jobs import RemoteJobStore


class _FakeE2BAdapter:
    def __init__(self) -> None:
        self.created_specs = []
        self.paused = []
        self.terminated = []
        self.on_run = None

    def create(self, spec):
        self.created_specs.append(spec)
        return "sandbox-123"

    def pause(self, sandbox_id: str) -> None:
        self.paused.append(sandbox_id)

    def terminate(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)

    def run_command(self, sandbox_id: str, command: str, *, user: str) -> dict:
        if self.on_run:
            self.on_run()
        return {"stdout": "", "stderr": "", "exit_code": 0}


def _connection() -> E2BConnectionConfig:
    return E2BConnectionConfig(
        api_key="super-secret",
        api_url="https://e2b.example",
        project_id="project-42",
        template="doc-compiler",
    )


def test_submit_e2b_persists_sandbox_without_api_key_and_is_idempotent(tmp_path) -> None:
    adapter = _FakeE2BAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), e2b_adapter=adapter)

    job = service.submit_e2b(
        owner_id="alice",
        session_id="session-1",
        idempotency_key="session-1:node-1:1",
        connection=_connection(),
    )
    replay = service.submit_e2b(
        owner_id="alice",
        session_id="session-1",
        idempotency_key="session-1:node-1:1",
        connection=_connection(),
    )

    assert job["status"] == "running"
    assert job["external_id"] == "sandbox-123"
    assert "api_key" not in job["specification"]
    assert replay["job_id"] == job["job_id"]
    assert len(adapter.created_specs) == 1


def test_e2b_job_controls_update_durable_state(tmp_path) -> None:
    adapter = _FakeE2BAdapter()
    service = RemoteJobService(RemoteJobStore(tmp_path / "remote-jobs.db"), e2b_adapter=adapter)
    job = service.submit_e2b(
        owner_id="alice",
        session_id="session-1",
        idempotency_key="session-1:node-1:1",
        connection=_connection(),
    )

    paused = service.pause_e2b(job["job_id"])
    assert paused["status"] == "paused"
    assert adapter.paused == ["sandbox-123"]

    terminated = service.terminate_e2b(paused["job_id"])
    assert terminated["status"] == "terminated"
    assert adapter.terminated == ["sandbox-123"]


def test_e2b_command_merges_telemetry_after_monitor_observation(tmp_path) -> None:
    adapter = _FakeE2BAdapter()
    store = RemoteJobStore(tmp_path / "remote-jobs.db")
    service = RemoteJobService(store, e2b_adapter=adapter)
    job = service.submit_e2b(
        owner_id="alice",
        session_id="session-1",
        idempotency_key="session-1:node-1:1",
        connection=_connection(),
    )

    adapter.on_run = lambda: store.record_observation(
        job["job_id"],
        snapshot={"provider_status": "reachable", "monitor_probe": "fresh"},
        expected_revision=store.get_job(job["job_id"])["state_revision"],
    )

    assert service.run_e2b_command(job["job_id"], "echo done") == {
        "stdout": "", "stderr": "", "exit_code": 0
    }
    assert store.get_job(job["job_id"])["snapshot"] == {
        "provider_status": "reachable",
        "monitor_probe": "fresh",
        "last_command_exit_code": 0,
    }

