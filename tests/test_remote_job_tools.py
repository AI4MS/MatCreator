from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.tools.function_tool import FunctionTool

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.control_plane.providers.e2b import E2BConnectionConfig


class _FakeService:
    def __init__(self) -> None:
        self.submissions: list[dict] = []
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
            "snapshot": {},
            "error": None,
            "updated_at": 1,
        }

    def list_events(self, job_id: str):
        return [{"event_type": "user_control", "payload": {"action": "terminate", "source": "ui"}}]

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
    assert submission["spec"]["template"] == "doc-compiler"
    assert submission["spec"]["api_key"] == "secret"
    assert "api_key" not in submission["persisted_specification"]


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


def test_submit_bohr_batchjob_tool_requires_project(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)

    result = remote_job_tools.submit_bohr_batchjob(_context(), name="n", image="img", command="cmd")

    assert result["status"] == "error"
    assert "project_id" in result["message"]
    assert service.submissions == []


def test_submit_bohr_batchjob_exposes_the_expected_adk_schema() -> None:
    declaration = FunctionTool(remote_job_tools.submit_bohr_batchjob)._get_declaration()
    assert declaration is not None
    payload = declaration.model_dump(exclude_none=True)
    parameters = payload.get("parameters_json_schema") or payload.get("parameters")
    assert payload["name"] == "submit_bohr_batchjob"
    assert set(parameters["required"]) == {"name", "image", "command"}
    assert set(parameters["properties"]) == {
        "name", "image", "command", "project_id", "machine_type", "sku_id",
        "input_path", "out_files", "max_run_time", "max_wait_time",
    }


def test_attach_bohr_batchjob_exposes_required_string_id() -> None:
    declaration = FunctionTool(remote_job_tools.attach_bohr_batchjob)._get_declaration()
    assert declaration is not None
    payload = declaration.model_dump(exclude_none=True)
    parameters = payload.get("parameters_json_schema") or payload.get("parameters")
    assert payload["name"] == "attach_bohr_batchjob"
    assert parameters["required"] == ["batchjob_id"]
    assert set(parameters["properties"]) == {"batchjob_id"}
    assert parameters["properties"]["batchjob_id"]["type"].lower() == "string"


@pytest.mark.parametrize("status,error", [("queued", None), ("succeeded", None), ("failed", "execution failed")])
def test_attach_bohr_batchjob_records_context_and_preserves_status(monkeypatch, status, error):
    calls = []
    references = []
    service = _FakeService()

    def attach(**kwargs):
        calls.append(kwargs)
        return {"job_id": "job-123", "external_id": "external-123", "status": status, "error": error}

    service.attach_job = attach
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: references.append(kwargs))
    monkeypatch.delenv("BOHRIUM_PROJECT_ID", raising=False)
    result = remote_job_tools.attach_bohr_batchjob(_context(), batchjob_id=" external-123 ")
    assert result == {"job_id": "job-123", "batchjob_id": "external-123", "status": status, "error": error}
    assert calls == [{
        "owner_id": "alice", "session_id": "session-1", "provider": "bohr_batchjob",
        "external_id": "external-123", "node_id": "relax", "step_number": 2,
    }]
    assert references == [{
        "session_id": "session-1", "node_id": "relax", "job_id": "job-123",
        "provider": "bohr_batchjob", "external_id": "external-123",
    }]
    assert service.submissions == []


@pytest.mark.parametrize("batchjob_id", ["", "  ", None, 123, True])
def test_attach_bohr_batchjob_rejects_invalid_id_without_io(monkeypatch, batchjob_id):
    monkeypatch.setattr(remote_job_tools, "_service", lambda: pytest.fail("invalid ID must not access store"))
    result = remote_job_tools.attach_bohr_batchjob(_context(), batchjob_id=batchjob_id)
    assert result["status"] == "error"
    assert "string batchjob_id is required" in result["error"]
    assert result["job_id"] is None


def test_attach_bohr_batchjob_requires_session_without_io(monkeypatch):
    monkeypatch.setattr(remote_job_tools, "_service", lambda: pytest.fail("missing session must not access store"))
    context = _context()
    context.state.pop("session_id")
    result = remote_job_tools.attach_bohr_batchjob(context, batchjob_id="external-123")
    assert result["status"] == "error"
    assert "session_id" in result["error"]


def test_attach_bohr_batchjob_surfaces_read_error_without_recording_reference(monkeypatch):
    def attach(**kwargs):
        raise ValueError("Could not verify existing job: account access denied")

    monkeypatch.setattr(remote_job_tools, "_service", lambda: SimpleNamespace(attach_job=attach))
    monkeypatch.setattr(
        remote_job_tools, "record_remote_job_reference",
        lambda **kwargs: pytest.fail("failed attachment must not record a reference"),
    )
    result = remote_job_tools.attach_bohr_batchjob(_context(), batchjob_id="external-123")
    assert result["status"] == "error"
    assert result["job_id"] is None
    assert result["batchjob_id"] == "external-123"
    assert "account access denied" in result["error"]


def test_submit_bohr_batchjob_tool_submits_batch_spec(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.submit_bohr_batchjob(
        _context(),
        project_id=42,
        name="relax-job",
        machine_type="c8_m32_cpu",
        image="registry.dp.tech/dptech/vasp:5.4.4",
        command="vasp_std",
    )

    assert result["status"] == "running"
    assert result["batchjob_id"] == "sandbox-123"
    submission = service.submissions[0]
    assert submission["provider"] == "bohr_batchjob"
    assert submission["spec"]["command"] == "vasp_std"
    assert submission["spec"]["max_run_time"] == "24h"
    assert submission["spec"]["max_wait_time"] == "30m"


@pytest.mark.parametrize("selectors", [{}, {"machine_type": "cpu", "sku_id": 123}])
def test_submit_bohr_batchjob_requires_exactly_one_machine(monkeypatch, selectors) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    result = remote_job_tools.submit_bohr_batchjob(
        _context(), project_id=42, name="n", image="img", command="cmd", **selectors,
    )
    assert result["status"] == "error"
    assert "exactly one" in result["message"]
    assert not service.submissions


@pytest.mark.parametrize("given,expected_rel", [
    ("input", "input"),
    ("./input", "input"),
    ("sub/input", "sub/input"),
    ("__abs_inside_workspace__", "input"),
])
def test_submit_bohr_batchjob_resolves_workspace_input_and_env_project(
    monkeypatch, tmp_path, given, expected_rel,
) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setenv("BOHRIUM_PROJECT_ID", "42")
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    for directory in (tmp_path / "input", tmp_path / "sub" / "input"):
        directory.mkdir(parents=True)
        (directory / "INCAR").write_text("ENCUT=500")
    input_path = str(tmp_path / "input") if given == "__abs_inside_workspace__" else given

    result = remote_job_tools.submit_bohr_batchjob(
        context, name="n", image="img", command="cmd", sku_id=123,
        input_path=input_path, out_files=["OUTCAR", "vasprun.xml"],
        max_run_time="2h", max_wait_time="10m",
    )

    assert result["status"] == "running"
    assert service.submissions[0]["spec"] == {
        "project_id": "42", "name": "n", "machine_type": None, "sku_id": 123,
        "image": "img", "command": "cmd", "input_path": expected_rel,
        "input_root": str(tmp_path),
        "out_files": ["OUTCAR", "vasprun.xml"], "max_run_time": "2h", "max_wait_time": "10m",
    }


@pytest.mark.parametrize("given,expected", [
    (["OUTCAR", "log"], ["OUTCAR", "log"]),
    ('["OUTCAR", "log"]', ["OUTCAR", "log"]),
    ("['OUTCAR', 'log']", ["OUTCAR", "log"]),
    ("OUTCAR", ["OUTCAR"]),
    ("stdout.log, stderr.log", ["stdout.log", "stderr.log"]),
    (None, None),
    ([], None),
    ("[]", None),
])
def test_submit_bohr_batchjob_coerces_stringified_out_files(
    monkeypatch, tmp_path, given, expected,
) -> None:
    # LLM function calls sometimes serialize the out_files array as one
    # string; the tool must coerce it instead of durably failing the job.
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)

    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=42, name="n", image="img", command="cmd",
        machine_type="cpu", out_files=given,
    )

    assert result["status"] == "running"
    assert service.submissions[0]["spec"]["out_files"] == expected


@pytest.mark.parametrize("given", [
    "[1, 2]", '{"vasprun.xml": true}', "", "  ", ["OUTCAR", ""], [3], "OUTCAR,,log",
])
def test_submit_bohr_batchjob_rejects_malformed_out_files_before_submission(
    monkeypatch, tmp_path, given,
) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)

    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=42, name="n", image="img", command="cmd",
        machine_type="cpu", out_files=given,
    )

    assert result["status"] == "error"
    assert "JSON array" in result["message"]
    # No durable record may be created for an argument-shape mistake.
    assert not service.submissions


@pytest.mark.parametrize("path,expected_message", [
    ("../outside", "must resolve inside the current workspace"),
    ("/etc/hosts", "must resolve inside the current workspace"),
    ("link", "symbolic link"),
    ("", "nonempty"),
    ("  ", "nonempty"),
    ("missing-input", "does not exist in the workspace"),
    (".", "not the workspace root"),
    ("__workspace_root_abs__", "not the workspace root"),
])
def test_submit_bohr_batchjob_rejects_unsafe_input(
    monkeypatch, tmp_path, path, expected_message,
) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    (tmp_path / "input").write_text("data")
    (tmp_path / "link").symlink_to(tmp_path / "input")
    if path == "__workspace_root_abs__":
        path = str(tmp_path)
    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=42, name="n", image="img", command="cmd",
        machine_type="cpu", input_path=path,
    )
    assert result["status"] == "error"
    assert expected_message in result["message"]
    # Escape errors must be self-correcting: name the real workspace and
    # steer the caller toward a relative path.
    if expected_message == "must resolve inside the current workspace":
        assert str(tmp_path) in result["message"]
        assert "relative to the workspace" in result["message"]
    assert not service.submissions


def test_submit_bohr_batchjob_idempotency_is_per_step_and_name(monkeypatch) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    for machine in ("cpu1", "cpu2"):
        remote_job_tools.submit_bohr_batchjob(
            _context(), project_id=42, name="n", image="img", command="cmd", machine_type=machine,
        )
    assert service.submissions[0]["idempotency_key"] == service.submissions[1]["idempotency_key"]


def test_legacy_remote_job_status_is_inspectable_but_controls_are_rejected(monkeypatch) -> None:
    service = _FakeService()
    original_get_job = service.get_job
    monkeypatch.setattr(
        service, "get_job", lambda job_id: {**original_get_job(job_id), "provider": "bohr_job"},
    )
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    result = remote_job_tools.get_remote_job_status("job-123", _context())
    assert result["status"] == "error"
    assert result["tracked_status"] == "running"
    assert result["external_id"] == "sandbox-123"
    assert result["provider_supported"] is False
    assert "no longer supported" in result["message"]
    assert remote_job_tools.terminate_remote_job("job-123", _context()) == result
    assert not hasattr(remote_job_tools, "submit_bohr_job")


def test_batchjob_control_and_collection_failures_include_the_error(monkeypatch, tmp_path) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    monkeypatch.setattr(
        service, "terminate_job",
        lambda job_id: {
            "job_id": job_id, "status": "lost", "external_id": "batch-123",
            "error": "Stop was accepted but not confirmed",
        },
    )
    monkeypatch.setattr(
        service, "collect_job_outputs",
        lambda job_id, dest: {"job_id": job_id, "status": "succeeded", "error": "Destination must not exist"},
    )
    terminated = remote_job_tools.terminate_remote_job("job-123", context)
    collected = remote_job_tools.collect_remote_job_outputs("job-123", "out", context)
    assert terminated["status"] == "lost"
    assert "not confirmed" in terminated["error"]
    # A failed collection keeps the succeeded provider outcome and tells the
    # agent how to retry instead of reporting the computation as failed.
    assert collected["status"] == "succeeded"
    assert "must not exist" in collected["error"]
    assert "new, nonexistent destination" in collected["message"]


def test_collect_remote_job_outputs_rejects_existing_destination_before_service(
    monkeypatch, tmp_path,
) -> None:
    service = _FakeService()
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    monkeypatch.setattr(
        service, "collect_job_outputs",
        lambda job_id, dest: pytest.fail("service must not be reached for an occupied destination"),
    )
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    (tmp_path / "outputs").mkdir()

    result = remote_job_tools.collect_remote_job_outputs("job-123", "outputs", context)

    assert result["status"] == "error"
    assert "already exists" in result["message"]


def test_collect_remote_job_outputs_replay_of_collected_job_skips_destination_check(
    monkeypatch, tmp_path,
) -> None:
    service = _FakeService()
    original_get_job = service.get_job
    monkeypatch.setattr(
        service, "get_job", lambda job_id: {**original_get_job(job_id), "status": "collected"},
    )
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)
    context = _context()
    context.state["workspace_dir"] = str(tmp_path)
    (tmp_path / "outputs").mkdir()

    result = remote_job_tools.collect_remote_job_outputs("job-123", "outputs", context)

    assert result["status"] == "collected"
    assert result["artifacts"]


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
    assert remote_job_tools.upload_remote_job_input("job-123", "input.txt", "/home/user/input.txt", context) == {
        "source": str(source), "destination": "/home/user/input.txt"
    }
    assert remote_job_tools.upload_remote_job_input(
        "job-123", "/tmp/outside.txt", "/tmp/outside.txt", context
    )["status"] == "error"


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


def test_poll_remote_job_command_surfaces_service_errors(monkeypatch) -> None:
    service = _FakeService()

    def _boom(job_id):
        raise ValueError("no in-flight background command")

    service.poll_job_command = _boom
    monkeypatch.setattr(remote_job_tools, "_service", lambda: service)

    result = remote_job_tools.poll_remote_job_command("job-123", _context())

    assert result["status"] == "error"
    assert "no in-flight background command" in result["message"]
