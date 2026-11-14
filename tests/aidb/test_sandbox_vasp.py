"""Exercise the tracked sandbox route with a local provider and historical VASP outputs."""
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest

from matcreator.agents.execution_agent import remote_job_tools as tools
from matcreator.aidb.service import SessionAIDB
from matcreator.control_plane.providers.base import RemoteJobCapability


@pytest.fixture
def sandbox_replay(tmp_path, monkeypatch):
    import json

    from ase.db import connect
    from pymatgen.io.vasp import Poscar, Vasprun

    if not (Path.home() / ".matcreator/skills/ai-ready-db/scripts/aidb_ops.py").is_file():
        pytest.skip("Install ai-ready-db for the isolated archive test")
    fixture = Path(__file__).resolve().parent / "fixtures/vasp/si-pbe-relax"
    workspace = tmp_path / "workspace"
    inputs = workspace / "input"
    inputs.mkdir(parents=True)
    for name in ("INCAR", "KPOINTS"):
        shutil.copyfile(fixture / name, inputs / name)
    run = Vasprun(fixture / "vasprun.xml", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    Poscar(run.initial_structure).write_file(inputs / "POSCAR")
    (inputs / "POTCAR").write_text("Synthetic fixture only\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    database, sidecar, config = (tmp_path / name for name in ("archive.db", "sidecar.json", "aidb.json"))
    with connect(database) as db:
        db.count()
    sidecar.write_text('{"records": {}}')
    config.write_text(json.dumps({"database": {"backend": "ase_sqlite"}, "ase_sqlite": {
        "path": str(database), "json_sidecar_path": str(sidecar), "max_query_keys_per_row": 64,
    }}))
    session = SessionAIDB(workspace, "test-chat")
    binding_id = session.bind(config)["binding_id"]
    adk = tmp_path / "adk"
    monkeypatch.setattr(tools, "ADK_DIR", adk)
    monkeypatch.setattr("matcreator.aidb.managed_vasp.ADK_DIR", adk)
    monkeypatch.setattr(tools, "record_remote_job_reference", lambda **kwargs: None)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    replay = fake_bin / "vasp_std"
    replay.write_text(
        '#!/bin/bash\nset -e\ncp "$MATCREATOR_TEST_FIXTURE/vasprun.xml" "$MATCREATOR_TEST_FIXTURE/OUTCAR" .\n'
    )
    replay.chmod(0o700)

    class SandboxProvider:
        capabilities = {RemoteJobCapability.INTERACTIVE_EXEC, RemoteJobCapability.FILE_TRANSFER}
        creates = 0
        launches = 0
        downloads = []
        fail_download = None
        corrupt_download = None

        def create(self, spec):
            self.creates += 1
            return f"historical-sandbox-replay-{self.creates}"

        def status(self, external_id):
            return None

        def run_command(self, external_id, command, *, user):
            self.launches += "nohup" in command
            # The wrapper encodes the command including its original /tmp path.
            # Its launch and marker paths are scoped by a generated job ID.
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10, env={
                **os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                "MATCREATOR_TEST_FIXTURE": str(fixture),
            })
            return {"exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}

        def upload_file(self, external_id, source, destination):
            shutil.copyfile(source, destination)

        def download_file(self, external_id, source, destination):
            name = Path(source).name
            self.downloads.append(name)
            if name == self.fail_download:
                raise OSError("injected interrupted transfer")
            shutil.copyfile(source, destination)
            if name == self.corrupt_download:
                destination.write_bytes(b"truncated transport")

    provider = SandboxProvider()
    monkeypatch.setattr(tools.RemoteJobService, "_adapter", lambda *args: provider)
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    return SimpleNamespace(**locals())


def test_sandbox_uses_shared_precheck_and_verified_collection(sandbox_replay, caplog):
    from matcreator.aidb.managed_vasp import get_managed_vasp_completion

    replay = sandbox_replay
    created = tools.submit_bohr_sandbox(replay.context, project_id=123, template="test-vasp")
    job_id = created["job_id"]
    assert created["status"] == "running", created
    assert tools.collect_remote_job_outputs(job_id, "download", replay.context)["status"] == "error"
    assert not replay.provider.downloads
    assert get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id) is None

    caplog.set_level("INFO", logger="matcreator.aidb.relaxation")
    started = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context, job_id=job_id)
    assert started["status"] == "running", started
    assert "outcome=no_match" in caplog.text
    assert not replay.provider.downloads
    polled = _poll_until_finished(job_id, replay.context)
    assert polled.get("exit_code") == 0, polled
    assert get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id) is None
    assert replay.session.query({"formula": "Si"})["records"] == []

    collected = tools.collect_remote_job_outputs(job_id, "download", replay.context)
    assert collected["archive_status"] == "verified", collected
    assert collected["compute_status"] == "succeeded"
    assert set(collected["artifact_paths"]) == {str(replay.workspace / "download" / name)
                                                for name in ("OUTCAR", "vasprun.xml")}
    result = get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id)
    assert result["binding_id"] == replay.binding_id
    assert result["summary"]["energy_ev_per_cell"] == -43.38227814
    [record] = replay.session.query({"formula": "Si"})["records"]
    assert record["record_id"] == result["record_id"]
    assert tools.collect_remote_job_outputs(job_id, "ignored", replay.context)["aidb_result"] == collected["aidb_result"]
    assert replay.provider.launches == 1
    assert len(replay.provider.downloads) == len(set(replay.provider.downloads))


def _poll_until_finished(job_id, context):
    for _ in range(100):
        polled = tools.poll_remote_job_command(job_id, context)
        if polled.get("running") is False:
            return polled
        time.sleep(0.02)
    pytest.fail(f"Sandbox command did not finish: {polled}")


@pytest.mark.parametrize("fault, expected", [
    ("command", "command_failed"),
    ("missing", "output_incomplete"),
    ("transfer", "download_failed"),
    ("archive", "archive_failed"),
    ("readback", "archive_failed"),
])
def test_sandbox_reports_distinct_failures_and_recovers_original_output(sandbox_replay, fault, expected, monkeypatch):
    import sqlite3

    replay = sandbox_replay
    if fault == "command":
        with replay.replay.open("a") as script:
            script.write("exit 7\n")
    elif fault == "missing":
        with replay.replay.open("a") as script:
            script.write("rm OUTCAR\n")
    elif fault == "transfer":
        replay.provider.fail_download = "OUTCAR"
    elif fault == "readback":
        from matcreator.aidb.service import AIDBError, SessionAIDB

        original_detail = SessionAIDB.detail

        def unavailable_detail(self, *args, **kwargs):
            raise AIDBError("injected detail lookup failure")

        monkeypatch.setattr(SessionAIDB, "detail", unavailable_detail)
    elif fault == "archive":
        with sqlite3.connect(replay.database) as db:
            db.execute("CREATE TRIGGER block_archive BEFORE INSERT ON systems BEGIN "
                       "SELECT RAISE(ABORT, 'injected write failure'); END")
    launched = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context,
                                               project_id=123, template="test-vasp")
    assert launched["status"] == "running", launched
    job_id = launched["job_id"]
    _poll_until_finished(job_id, replay.context)
    failed = tools.collect_remote_job_outputs(job_id, "download", replay.context)
    assert failed["archive_status"] == "failed", failed
    assert failed["failure_kind"] == expected, failed
    assert failed["compute_status"] == ("succeeded" if fault in {"archive", "readback"} else "unverified")
    assert str(replay.workspace / "download/vasprun.xml") in failed["artifact_paths"]
    if fault == "readback":
        from matcreator.aidb.managed_vasp import get_managed_vasp_completion

        failed_completion = get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id)
        [record] = replay.session.query({"formula": "Si"})["records"]
        assert failed_completion["unverified_record_id"] == record["record_id"]
        monkeypatch.setattr(SessionAIDB, "detail", original_detail)
    else:
        assert replay.session.query({"formula": "Si"})["records"] == []
    if fault in {"transfer", "archive", "readback"}:
        replay.context._invocation_context.invocation_id = "after-restart"
        replay.provider.fail_download = None
        with sqlite3.connect(replay.database) as db:
            db.execute("DROP TRIGGER IF EXISTS block_archive")
        recovered = tools.collect_remote_job_outputs(job_id, "ignored", replay.context)
        assert recovered["aidb_result"] == failed["aidb_result"]
        assert recovered["archive_status"] == "verified", recovered
        assert replay.provider.downloads.count("vasprun.xml") == 1
        assert replay.provider.launches == 1
        assert tools.collect_remote_job_outputs(job_id, "ignored", replay.context)["aidb_result"] == recovered["aidb_result"]
        assert len(replay.session.query({"formula": "Si"})["records"]) == 1


def test_sandbox_archive_can_pause_a_fresh_sandbox_before_allocation(sandbox_replay):
    replay = sandbox_replay
    first = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context,
                                            project_id=123, template="test-vasp")
    assert first["status"] == "running", first
    _poll_until_finished(first["job_id"], replay.context)
    archived = tools.collect_remote_job_outputs(first["job_id"], "download", replay.context)
    assert archived["archive_status"] == "verified", archived

    replay.context.state["_graph_exec_node_id"] = "step-two"
    replay.context._invocation_context.invocation_id = "turn-2"
    choice = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context,
                                             project_id=123, template="test-vasp")
    assert choice["status"] == "waiting_choice", choice
    assert len(choice["candidates"]["records"]) == 1
    assert replay.provider.creates == 1
    assert replay.provider.launches == 1


def test_generic_sandbox_tools_route_vasp_to_the_managed_entry(sandbox_replay):
    replay = sandbox_replay
    job = tools.submit_bohr_sandbox(replay.context, project_id=123, template="test-vasp")
    for execute in (tools.run_remote_job_command, tools.start_remote_job_command):
        blocked = execute(job["job_id"], "mpirun -np 4 vasp_std", replay.context)
        assert blocked["status"] == "error"
        assert "start_managed_vasp_sandbox" in blocked["message"]
    assert replay.provider.launches == 0


def test_cancelled_sandbox_does_not_archive_cached_outputs(sandbox_replay):
    import sqlite3

    replay = sandbox_replay
    launched = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context,
                                               project_id=123, template="test-vasp")
    job_id = launched["job_id"]
    _poll_until_finished(job_id, replay.context)
    with sqlite3.connect(replay.database) as db:
        db.execute("CREATE TRIGGER block_archive BEFORE INSERT ON systems BEGIN "
                   "SELECT RAISE(ABORT, 'injected write failure'); END")
    first = tools.collect_remote_job_outputs(job_id, "download", replay.context)
    assert first["archive_status"] == "failed" and first["compute_status"] == "succeeded"
    store = tools._service().store
    store.transition_job(job_id, "terminate_requested")
    store.transition_job(job_id, "terminated")
    with sqlite3.connect(replay.database) as db:
        db.execute("DROP TRIGGER block_archive")

    retried = tools.collect_remote_job_outputs(job_id, "download", replay.context)

    assert retried["status"] == "error", retried
    assert replay.session.query({"formula": "Si"})["records"] == []
    assert replay.provider.launches == 1


def test_optional_sandbox_transfer_can_recover_without_rearchiving(sandbox_replay):
    replay = sandbox_replay
    with replay.replay.open("a") as script:
        script.write("cp OUTCAR OSZICAR\n")
    replay.provider.fail_download = "OSZICAR"
    started = tools.start_managed_vasp_sandbox(
        "input", "vasp_std", replay.context, out_files=["OSZICAR"], project_id=123, template="test-vasp",
    )
    job_id = started["job_id"]
    _poll_until_finished(job_id, replay.context)

    first = tools.collect_remote_job_outputs(job_id, "download", replay.context)
    assert first["archive_status"] == "verified", first
    assert "OSZICAR" in first["artifact_delivery_issue"]
    assert str(replay.workspace / "download/OSZICAR") not in first["artifact_paths"]

    replay.provider.fail_download = None
    retry = tools.collect_remote_job_outputs(job_id, "ignored", replay.context)
    assert retry["aidb_result"] == first["aidb_result"]
    assert retry["archive_status"] == "verified"
    assert "artifact_delivery_issue" not in retry
    assert str(replay.workspace / "download/OSZICAR") in retry["artifact_paths"]
    assert len(replay.session.query({"formula": "Si"})["records"]) == 1
    assert replay.provider.downloads.count("vasprun.xml") == 1
    assert replay.provider.launches == 1


@pytest.mark.parametrize("outcome", ["rejected", "uncertain"])
def test_managed_launch_distinguishes_rejection_from_lost_response(sandbox_replay, monkeypatch, outcome):
    replay = sandbox_replay
    created = tools.submit_bohr_sandbox(replay.context, project_id=123, template="test-vasp")
    job_id = created["job_id"]
    run = replay.provider.run_command
    attempts = []

    def first_launch(external_id, command, *, user):
        if "nohup" in command:
            attempts.append(command)
            if len(attempts) == 1:
                if outcome == "rejected":
                    return {"exit_code": 126, "stdout": "", "stderr": "rejected"}
                run(external_id, command, user=user)
                raise RuntimeError("launch response lost")
        return run(external_id, command, user=user)

    monkeypatch.setattr(replay.provider, "run_command", first_launch)
    started = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context, job_id=job_id)
    if outcome == "rejected":
        assert started["status"] == "error", started
        polled = tools.poll_remote_job_command(job_id, replay.context)
        assert polled.get("running") is False and polled.get("exit_code") == 126, polled
    else:
        assert started["status"] == "launch_uncertain", started

    retried = tools.start_managed_vasp_sandbox("input", "vasp_std", replay.context, job_id=job_id)
    assert retried["status"] == "running", retried
    assert _poll_until_finished(job_id, replay.context)["exit_code"] == 0
    assert len(attempts) == (2 if outcome == "rejected" else 1)
    assert replay.provider.launches == 1



@pytest.mark.parametrize("filename", ["OUTCAR", "matcreator-vasp-receipt.sha256"])
def test_sandbox_retries_corrupt_transfer_and_keeps_healthy_files(sandbox_replay, filename):
    replay = sandbox_replay
    replay.provider.corrupt_download = filename
    started = tools.start_managed_vasp_sandbox(
        "input", "vasp_std", replay.context, project_id=123, template="test-vasp",
    )
    job_id = started["job_id"]
    _poll_until_finished(job_id, replay.context)

    failed = tools.collect_remote_job_outputs(job_id, "download", replay.context)
    assert failed["archive_status"] == "failed", failed
    assert failed["failure_kind"] == "download_failed"
    assert not (replay.workspace / "download/OUTCAR").exists()
    assert replay.session.query({"formula": "Si"})["records"] == []

    replay.provider.corrupt_download = None
    recovered = tools.collect_remote_job_outputs(job_id, "ignored", replay.context)
    assert recovered["archive_status"] == "verified", recovered
    assert (replay.workspace / "download/OUTCAR").read_bytes() == (replay.fixture / "OUTCAR").read_bytes()
    assert replay.provider.downloads.count("vasprun.xml") == 1
    assert replay.provider.downloads.count(filename) == 2
    assert replay.provider.launches == 1
