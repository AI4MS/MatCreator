from __future__ import annotations

import hashlib
from types import SimpleNamespace

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.control_plane.providers.e2b import E2BConnectionConfig


class _FakeService:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
        self.merged_specifications: list[tuple[str, dict]] = []
        self.store = self

    def submit_job(self, **kwargs):
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
            "provider": "e2b",
            "status": "running",
            "external_id": "sandbox-123",
            "specification": {},
            "snapshot": {},
            "error": None,
            "updated_at": 1,
        }

    def list_events(self, job_id: str):
        return [{"event_type": "user_control", "payload": {"action": "terminate", "source": "ui"}}]

    def merge_specification(self, job_id: str, values: dict):
        self.merged_specifications.append((job_id, values))
        return self.get_job(job_id)

    def pause_job(self, job_id: str):
        return {"job_id": job_id, "status": "paused", "external_id": "sandbox-123"}

    def terminate_job(self, job_id: str):
        return {"job_id": job_id, "status": "terminated", "external_id": "sandbox-123"}

    def run_job_command(self, job_id: str, command: str, *, user: str):
        return {"stdout": f"ran {command}", "stderr": "", "exit_code": 0}

    def upload_job_file(self, job_id: str, source, destination: str):
        return {"source": str(source), "destination": destination}

    def download_job_file(self, job_id: str, source: str, destination: str):
        return {"source": source, "destination": str(destination)}

    def collect_job_outputs(self, job_id: str, destination_dir):
        return {"job_id": job_id, "status": "collected", "artifacts": [{"source": "x", "destination": str(destination_dir)}]}

    def start_job_command(self, job_id: str, command: str, *, user: str):
        return {"job_id": job_id, "launch": {"stdout": "LAUNCHED\n"}, "handle": {"log_path": "/tmp/x.log", "exit_path": "/tmp/x.exit"}}

    def poll_job_command(self, job_id: str):
        return {"running": False, "exit_code": 0, "output_tail": "done\n", "log_path": "/tmp/x.log"}

    def publish_job_progress(self, job_id: str, *, kind: str, current: int, total: int, unit: str):
        return {
            **self.get_job(job_id),
            "snapshot": {
                "progress": {
                    "kind": kind,
                    "current": current,
                    "total": total,
                    "percent": current * 100 / total,
                    "unit": unit,
                }
            },
        }


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
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    result = remote_job_tools.submit_e2b_sandbox(_context(), timeout=120, template="doc-compiler")

    assert result == {
        "status": "running",
        "job_id": "job-123",
        "sandbox_id": "sandbox-123",
        "message": "Tracked E2B sandbox is ready. Use its job_id for status or controls.",
    }
    submission = service.submissions[0]
    assert submission["owner_id"] == "alice"
    assert submission["session_id"] == "session-1"
    assert submission["provider"] == "e2b"
    assert submission["node_id"] == "relax"
    assert submission["step_number"] == 2
    assert submission["idempotency_key"] == remote_job_tools._idempotency_key(
        "session-1", "relax", "doc-compiler"
    )
    assert submission["spec"]["template"] == "doc-compiler"
    assert submission["spec"]["api_key"] == "secret"
    assert "api_key" not in submission["persisted_specification"]
    assert submission["persisted_specification"]["stable_name"] == "relax"
    assert submission["persisted_specification"]["workload_kind"] == "generic"
    assert submission["persisted_specification"]["task_contract"]["schema_version"] == "mc.remote-task.v1"
    assert submission["persisted_specification"]["task_contract"]["inputs"] == []
    assert submission["persisted_specification"]["presentation"] == {
        "version": "mc.remote-task-presentation.v1",
        "workload_kind": "generic",
        "task_type": "Generic remote task",
        "current_phase": "prepare",
        "phase_plan": [
            {"key": "prepare", "label": "Prepare inputs", "group": "preparation"},
            {"key": "submit", "label": "Submit Sandbox", "group": "preparation"},
            {"key": "queue", "label": "Stage runtime", "group": "preparation"},
            {"key": "execute", "label": "Execute", "group": "execution"},
            {"key": "collect", "label": "Collect outputs", "group": "completion"},
            {"key": "validate", "label": "Verify task", "group": "completion"},
        ],
    }


def test_submit_e2b_tool_requires_explicit_template(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_e2b_sandbox(_context())

    assert result["status"] == "error"
    assert "template is required" in result["message"]
    assert service.submissions == []


def test_submit_e2b_tool_requires_server_configuration(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

    result = remote_job_tools.submit_e2b_sandbox(_context(), template="doc-compiler")

    assert result["status"] == "error"
    assert "E2B_API_KEY" in result["message"]
    assert "BOHRIUM_PROJECT_ID" in result["message"]
    assert "E2B_API_URL" not in result["message"]
    assert service.submissions == []


def test_submit_e2b_tool_coerces_json_string_lifecycle(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    result = remote_job_tools.submit_e2b_sandbox(
        _context(), template="doc-compiler", lifecycle='{"on_timeout": "pause", "auto_resume": false}'
    )

    assert result["status"] == "running"
    assert service.submissions[0]["spec"]["lifecycle"] == {"on_timeout": "pause", "auto_resume": False}


def test_submit_e2b_tool_rejects_non_object_lifecycle(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    for bad_lifecycle in ("pause on timeout", '["pause"]'):
        result = remote_job_tools.submit_e2b_sandbox(
            _context(), template="doc-compiler", lifecycle=bad_lifecycle
        )
        assert result["status"] == "error"
        assert "lifecycle must be a JSON object" in result["message"]
    assert service.submissions == []


def test_submit_e2b_tool_surfaces_failed_job_error_instead_of_claiming_ready(monkeypatch) -> None:
    service = _FakeService()

    def _failed_submit(**kwargs):
        service.submissions.append(kwargs)
        return {
            "job_id": "job-123",
            "status": "failed",
            "external_id": None,
            "error": "e2b job creation failed: boom",
        }

    service.submit_job = _failed_submit
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    result = remote_job_tools.submit_e2b_sandbox(_context(), template="doc-compiler")

    assert result["status"] == "failed"
    assert result["sandbox_id"] is None
    assert "e2b job creation failed: boom" in result["message"]
    assert "ready" not in result["message"]


def test_e2b_connection_uses_configured_environment_names(monkeypatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "access-key")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-7")

    connection = remote_job_tools._connection()

    assert connection == E2BConnectionConfig(
        api_key="access-key",
        api_url="https://e2b.example",
        project_id="project-7",
        template="",
    )


def test_submit_bohr_sandbox_tool_requires_project_id(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

    result = remote_job_tools.submit_bohr_sandbox(_context())

    assert result["status"] == "error"
    assert "project_id" in result["message"]
    assert service.submissions == []


def test_submit_bohr_sandbox_tool_requires_explicit_template(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_bohr_sandbox(_context(), project_id=42)

    assert result["status"] == "error"
    assert "template is required" in result["message"]
    assert service.submissions == []


def test_submit_bohr_sandbox_tool_submits_with_provider(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_bohr_sandbox(_context(), project_id=42, template="sdbxagent")

    assert result["status"] == "running"
    assert result["sandbox_id"] == "sandbox-123"
    assert service.submissions[0]["provider"] == "bohr_sandbox"
    assert service.submissions[0]["spec"]["project_id"] == 42
    assert "session_id" not in service.submissions[0]["spec"]


def test_submit_bohr_sandbox_persists_public_typed_contract_without_env_secrets(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_bohr_sandbox(
        _context(),
        project_id=42,
        template="sdbxagent",
        stable_name="md-run-001",
        workload_kind="md",
        env={"ACCESS_TOKEN": "do-not-persist", "NORMAL_SETTING": "also-private"},
    )

    assert result["status"] == "running"
    submission = service.submissions[0]
    assert submission["spec"]["env"] == {
        "ACCESS_TOKEN": "do-not-persist",
        "NORMAL_SETTING": "also-private",
    }
    provider_session_id = remote_job_tools._bohr_provider_session_id(
        _context(), template="sdbxagent", stable_name="md-run-001"
    )
    assert submission["spec"]["session_id"] == provider_session_id
    assert provider_session_id.startswith("mc-")
    assert len(provider_session_id) == 35
    assert "md-run-001" not in provider_session_id
    persisted = submission["persisted_specification"]
    assert "env" not in persisted
    assert "session_id" not in persisted
    assert persisted["provider_session_id"] == provider_session_id
    assert persisted["env_variable_count"] == 2
    assert "do-not-persist" not in str(persisted)
    assert "also-private" not in str(persisted)
    assert persisted["stable_name"] == "md-run-001"
    assert persisted["workload_kind"] == "md"
    assert persisted["task_type"] == "Molecular dynamics"
    assert persisted["task_contract"]["task_run_id"] == "md-run-001"
    assert persisted["task_contract"]["workload_profile"] == "md"
    assert persisted["presentation"]["current_phase"] == "prepare"
    assert persisted["presentation"]["phase_plan"][3] == {
        "key": "execute",
        "label": "Simulate",
        "group": "execution",
    }


def test_submit_e2b_sandbox_accepts_explicit_vasp_identity(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("E2B_API_KEY", "secret")
    monkeypatch.setenv("E2B_API_URL", "https://e2b.example")
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "project-42")

    result = remote_job_tools.submit_e2b_sandbox(
        _context(),
        template="doc-compiler",
        stable_name="vasp-relax-001",
        workload_kind="vasp",
    )

    assert result["status"] == "running"
    submission = service.submissions[0]
    persisted = submission["persisted_specification"]
    assert persisted["stable_name"] == "vasp-relax-001"
    assert persisted["presentation"]["task_type"] == "VASP"
    assert persisted["presentation"]["phase_plan"][3]["label"] == "Solve"
    assert persisted["task_contract"]["workload_profile"] == "vasp"
    default_key = remote_job_tools._idempotency_key("session-1", "relax", "doc-compiler")
    assert submission["idempotency_key"] != default_key


def test_sandbox_submit_rejects_invalid_public_task_contract_before_provider_call(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    invalid_kind = remote_job_tools.submit_bohr_sandbox(
        _context(),
        project_id=42,
        template="sdbxagent",
        workload_kind="unknown-science-task",
    )
    invalid_name = remote_job_tools.submit_bohr_sandbox(
        _context(),
        project_id=42,
        template="sdbxagent",
        stable_name="../../escape",
    )

    assert invalid_kind["status"] == "error"
    assert "workload_profile" in invalid_kind["message"]
    assert invalid_name["status"] == "error"
    assert "task_run_id" in invalid_name["message"]
    assert service.submissions == []


def test_submit_bohr_job_tool_requires_all_fields(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

    result = remote_job_tools.submit_bohr_job(_context(), job_name="n")

    assert result["status"] == "error"
    assert "project_id" in result["message"]
    assert "machine_type" in result["message"]
    assert service.submissions == []


def test_submit_bohr_job_tool_submits_batch_spec(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_bohr_job(
        _context(),
        project_id=42,
        job_name="relax-job",
        machine_type="c8_m32_cpu",
        image_address="registry.dp.tech/dptech/vasp:5.4.4",
        command="vasp_std",
    )

    assert result["status"] == "running"
    assert result["bohr_job_id"] == "sandbox-123"
    submission = service.submissions[0]
    assert submission["provider"] == "bohr_job"
    assert submission["spec"]["command"] == "vasp_std"


def test_remote_job_tools_reject_jobs_from_another_session(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context._invocation_context.user_id = "bob"

    assert remote_job_tools.get_remote_job_status("job-123", context) == {
        "status": "error",
        "message": "Remote job was not found in this session.",
    }


def test_remote_job_status_exposes_user_control(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    status = remote_job_tools.get_remote_job_status("job-123", _context())

    assert status["user_control"] == {"action": "terminate", "source": "ui"}


def test_remote_job_command_and_workspace_upload_are_scoped_to_owned_job(tmp_path, monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    source = tmp_path / "input.txt"
    source.write_text("input", encoding="utf-8")

    assert remote_job_tools.run_remote_job_command("job-123", "echo hello", context) == {
        "stdout": "ran echo hello", "stderr": "", "exit_code": 0
    }
    uploaded = remote_job_tools.upload_remote_job_input(
        "job-123", "input.txt", "/home/user/input.txt", context, role="control"
    )
    assert uploaded["source"] == str(source)
    assert uploaded["destination"] == "/home/user/input.txt"
    assert uploaded["input"] == {
        "role": "control",
        "remote_path": "/home/user/input.txt",
        "size": 5,
        "sha256": hashlib.sha256(b"input").hexdigest(),
        "required": True,
    }
    assert uploaded["contract_digest"].startswith("sha256:")
    merged_contract = service.merged_specifications[-1][1]["task_contract"]
    assert merged_contract["schema_version"] == "mc.remote-task.v1"
    assert merged_contract["inputs"] == [uploaded["input"]]
    assert remote_job_tools.upload_remote_job_input(
        "job-123", "/tmp/outside.txt", "/tmp/outside.txt", context
    )["status"] == "error"


def test_upload_remote_job_input_rejects_noncanonical_remote_path_before_transfer(
    tmp_path, monkeypatch
) -> None:
    service = _FakeService()
    upload_calls: list[tuple] = []

    def _upload(*args):
        upload_calls.append(args)
        return {"source": args[1], "destination": args[2]}

    service.upload_job_file = _upload
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    (tmp_path / "input.txt").write_text("input", encoding="utf-8")

    result = remote_job_tools.upload_remote_job_input(
        "job-123", "input.txt", "relative/input.txt", context
    )

    assert result["status"] == "error"
    assert "canonical absolute POSIX" in result["message"]
    assert upload_calls == []


def test_download_remote_job_output_is_scoped_to_workspace(tmp_path, monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    destination = tmp_path / "outputs" / "CHGCAR"

    result = remote_job_tools.download_remote_job_output(
        "job-123", "/home/user/CHGCAR", str(destination), context
    )
    assert result == {"source": "/home/user/CHGCAR", "destination": str(destination.resolve())}

    assert remote_job_tools.download_remote_job_output(
        "job-123", "/home/user/CHGCAR", "/tmp/outside.txt", context
    )["status"] == "error"


def test_download_remote_job_output_rejects_missing_workspace_dir(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()  # no workspace_dir set

    result = remote_job_tools.download_remote_job_output(
        "job-123", "/home/user/CHGCAR", "outputs/CHGCAR", context
    )
    assert result["status"] == "error"
    assert "workspace_dir" in result["message"]


def test_collect_remote_job_outputs_is_scoped_to_workspace(tmp_path, monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)

    result = remote_job_tools.collect_remote_job_outputs("job-123", "outputs", context)

    assert result["status"] == "collected"
    assert result["artifacts"][0]["destination"] == str((tmp_path / "outputs").resolve())

    assert remote_job_tools.collect_remote_job_outputs(
        "job-123", "/tmp/outside", context
    )["status"] == "error"


def test_start_remote_job_command_delegates_to_service(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.start_remote_job_command("job-123", "sleep 300", _context())

    assert result["job_id"] == "job-123"
    assert result["handle"]["log_path"] == "/tmp/x.log"


def test_start_remote_job_command_rejects_jobs_from_another_session(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context._invocation_context.user_id = "bob"

    result = remote_job_tools.start_remote_job_command("job-123", "sleep 300", context)

    assert result == {
        "status": "error",
        "message": "Remote job was not found in this session.",
    }


def test_poll_remote_job_command_delegates_to_service(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.poll_remote_job_command("job-123", _context())

    assert result == {
        "running": False,
        "exit_code": 0,
        "output_tail": "done\n",
        "log_path": "/tmp/x.log",
    }


def test_publish_remote_job_progress_returns_explicit_metric(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.publish_remote_job_progress(
        "job-123", "md_steps", 250, 1000, "steps", _context()
    )

    assert result == {
        "job_id": "job-123",
        "status": "running",
        "current_phase": "execution",
        "progress": {
            "kind": "md_steps",
            "current": 250,
            "total": 1000,
            "percent": 25.0,
            "unit": "steps",
        },
    }


def test_poll_remote_job_command_surfaces_service_errors(monkeypatch) -> None:
    service = _FakeService()

    def _boom(job_id):
        raise ValueError("no in-flight background command")

    service.poll_job_command = _boom
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.poll_remote_job_command("job-123", _context())

    assert result["status"] == "error"
    assert "no in-flight background command" in result["message"]
