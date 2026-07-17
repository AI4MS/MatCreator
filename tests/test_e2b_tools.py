from __future__ import annotations

from types import SimpleNamespace

from matcreator.agents.execution_agent import e2b_tools
from matcreator.control_plane.remote_job_service import E2BConnectionConfig


class _FakeService:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
        self.store = self

    def submit_e2b(self, **kwargs):
        self.submissions.append(kwargs)
        return {
            "job_id": "job-123",
            "status": "running",
            "external_id": "sandbox-123",
        }

    def get_job(self, job_id: str):
        if job_id != "job-123":
            return None
        return {
            "job_id": job_id,
            "owner_id": "alice",
            "session_id": "session-1",
            "status": "running",
            "external_id": "sandbox-123",
            "snapshot": {},
            "error": None,
            "updated_at": 1,
        }

    def list_events(self, job_id: str):
        return [{"event_type": "user_control", "payload": {"action": "terminate", "source": "ui"}}]

    def pause_e2b(self, job_id: str):
        return {"job_id": job_id, "status": "paused", "external_id": "sandbox-123"}

    def terminate_e2b(self, job_id: str):
        return {"job_id": job_id, "status": "terminated", "external_id": "sandbox-123"}

    def run_e2b_command(self, job_id: str, command: str, *, user: str):
        return {"stdout": f"ran {command}", "stderr": "", "exit_code": 0}

    def upload_e2b_file(self, job_id: str, source, destination: str):
        return {"source": str(source), "destination": destination}


def _context():
    return SimpleNamespace(
        state={
            "session_id": "session-1",
            "_graph_exec_node_id": "execution_0__node_relax",
            "step_number": 2,
        },
        _invocation_context=SimpleNamespace(user_id="alice"),
    )


def test_submit_e2b_tool_uses_current_session_and_node(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(e2b_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    result = e2b_tools.submit_e2b_sandbox(_context(), timeout=120, template="doc-compiler")

    assert result == {
        "status": "running",
        "job_id": "job-123",
        "sandbox_id": "sandbox-123",
        "message": "Tracked E2B sandbox is ready. Use its job_id for status or controls.",
    }
    submission = service.submissions[0]
    assert submission["owner_id"] == "alice"
    assert submission["session_id"] == "session-1"
    assert submission["node_id"] == "relax"
    assert submission["step_number"] == 2
    assert submission["connection"] == E2BConnectionConfig(
        api_key="secret",
        api_url="https://e2b.example",
        project_id="project-42",
        template="doc-compiler",
    )


def test_submit_e2b_tool_requires_explicit_template(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(e2b_tools, "_service", lambda: service)

    result = e2b_tools.submit_e2b_sandbox(_context())

    assert result["status"] == "error"
    assert "template is required" in result["message"]
    assert service.submissions == []


def test_e2b_connection_uses_configured_environment_names(monkeypatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "access-key")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-7")

    connection = e2b_tools._connection()

    assert connection == E2BConnectionConfig(
        api_key="access-key",
        api_url="https://e2b.example",
        project_id="project-7",
        template="",
    )


def test_e2b_tools_reject_jobs_from_another_session(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(e2b_tools, "_service", lambda: service)
    context = _context()
    context._invocation_context.user_id = "bob"

    assert e2b_tools.get_e2b_job_status("job-123", context) == {
        "status": "error",
        "message": "E2B job was not found in this session.",
    }


def test_e2b_status_exposes_user_sandbox_control(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(e2b_tools, "_service", lambda: service)

    status = e2b_tools.get_e2b_job_status("job-123", _context())

    assert status["user_control"] == {"action": "terminate", "source": "ui"}


def test_e2b_command_and_workspace_upload_are_scoped_to_owned_job(tmp_path, monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(e2b_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    source = tmp_path / "input.txt"
    source.write_text("input", encoding="utf-8")

    assert e2b_tools.run_e2b_command("job-123", "echo hello", context) == {
        "stdout": "ran echo hello", "stderr": "", "exit_code": 0
    }
    assert e2b_tools.upload_e2b_input("job-123", "input.txt", "/home/user/input.txt", context) == {
        "source": str(source), "destination": "/home/user/input.txt"
    }
    assert e2b_tools.upload_e2b_input("job-123", "/tmp/outside.txt", "/tmp/outside.txt", context)["status"] == "error"