"""Exercise managed Batch Job tools with a fake provider and a local AIDB."""
import json
import sqlite3

import pytest
from pathlib import Path
from types import SimpleNamespace

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.aidb.service import SessionAIDB
from matcreator.control_plane.providers.base import RemoteJobCapability


def test_submitted_batch_uses_a_private_snapshot_of_confirmed_inputs(tmp_path, monkeypatch):
    from pymatgen.core import Lattice, Structure
    from pymatgen.io.vasp import Incar, Kpoints, Poscar

    workspace = tmp_path / "workspace"
    inputs = workspace / "input"
    inputs.mkdir(parents=True)
    Incar({"IBRION": 2, "NSW": 5, "ENCUT": 520}).write_file(inputs / "INCAR")
    Poscar(Structure(Lattice.cubic(3), ["Si"], [[0, 0, 0]])).write_file(inputs / "POSCAR")
    Kpoints.gamma_automatic((4, 4, 4)).write_file(inputs / "KPOINTS")
    (inputs / "POTCAR").write_text("Synthetic fixture only\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    original_incar = (inputs / "INCAR").read_bytes()
    config = tmp_path / "aidb.json"
    config.write_text("{}")
    binding = SessionAIDB(workspace, "test-chat").bind(config)
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", tmp_path / "adk")
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    monkeypatch.setattr("matcreator.aidb.service.InstalledSkillQuery",
                        lambda **kw: lambda *args: ([], {"match_count": 0}))

    class BatchProvider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            staged = Path(spec["input_root"]) / spec["input_path"]
            assert not staged.is_relative_to(workspace)
            (inputs / "INCAR").write_text("changed after preparation")
            assert (staged / "INCAR").read_bytes() == original_incar
            assert "bash" in spec["command"]
            assert {"OUTCAR", "vasprun.xml"} <= set(spec["out_files"])
            return "provider-run-1"

        def status(self, external_id):
            return None

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: BatchProvider())
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp", command="mpirun -np 4 vasp_std",
        machine_type="test-cpu", input_path="input", out_files=["CONTCAR"],
    )
    assert result["status"] == "queued", result
    job = remote_job_tools._service().store.get_job(result["job_id"])
    evidence = job["specification"]["managed_vasp"]
    assert evidence["binding_id"] == binding["binding_id"]
    assert set(evidence["revision"]["sha256"]) == {"INCAR", "POSCAR", "KPOINTS", "POTCAR"}
    assert "Synthetic fixture" not in json.dumps(job)


@pytest.mark.parametrize("fault", [None, "independent", "download_interrupted", "download_partial", "download_corrupt", "legacy_corrupt", "readback", "write", "receipt", "binding", "static",
                                    "static_missing", "static_unconverged", "static_write", "md",
                                    "md_seeded", "md_missing", "md_incomplete", "md_truncated", "md_write"])
def test_batch_collection_validates_and_archives_once_with_the_original_receipt(tmp_path, monkeypatch, fault):
    from io import StringIO
    import gzip
    import os
    import shutil
    import subprocess

    import numpy as np
    from ase.db import connect
    from ase.io import read
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pymatgen.io.vasp import Poscar
    from web.aidb_api import create_aidb_router

    ops = Path.home() / ".matcreator/skills/ai-ready-db/scripts/aidb_ops.py"
    if not ops.is_file():
        pytest.skip("Install ai-ready-db for the isolated archive test")
    fixtures = Path(__file__).resolve().parent / "fixtures/vasp/si-pbe-relax"
    workspace = tmp_path / "workspace"
    inputs = workspace / "input"
    inputs.mkdir(parents=True)

    static = bool(fault and fault.startswith("static"))
    md = bool(fault and fault.startswith("md"))
    if static or md:
        raw = fixtures.parent / ("si-md-1" if md else "nacl-static")
        fixtures = tmp_path / "replayed-fixture"
        fixtures.mkdir()
        for name in ("vasprun.xml", "OUTCAR"):
            (fixtures / name).write_bytes(gzip.decompress((raw / (name + ".gz")).read_bytes()))
    else:
        for name in ("INCAR", "KPOINTS"):
            shutil.copyfile(fixtures / name, inputs / name)
    from pymatgen.io.vasp import Vasprun

    initial = Vasprun(fixtures / "vasprun.xml", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    Poscar(initial.initial_structure).write_file(inputs / "POSCAR")
    if md:
        if fault in {"md_seeded", "md_missing"}:
            # The recorded initial state comes from the real OUTCAR. Set it
            # explicitly for the replay to exercise the deterministic reuse boundary.
            initial.incar["RANDOM_SEED"] = [496816047, 0, 0]
        if fault == "md_seeded":
            # A computed per-atom threshold can differ from VASP's decimal
            # XML by a single binary float unit, even when VASP used it.
            initial.incar["EDIFF"] = np.nextafter(initial.incar["EDIFF"], np.inf).item()
        initial.incar.write_file(inputs / "INCAR")
        initial.kpoints.write_file(inputs / "KPOINTS")
        outcar = fixtures / "OUTCAR"
        if fault == "md_missing":
            outcar.write_text(outcar.read_text().replace("(temperature  297.38 K)", "(temperature unavailable)"))
        elif fault == "md_incomplete":
            header = "POSITION                                       TOTAL-FORCE"
            content = outcar.read_text()
            middle = content.index(header, content.index(header) + len(header))
            outcar.write_text(content[:middle] + content[middle:].replace(header, "MISSING MIDDLE FRAME", 1))
        elif fault == "md_truncated":
            xml = fixtures / "vasprun.xml"
            xml.write_bytes(xml.read_bytes()[:-100])
    if static:
        from pymatgen.io.vasp import Chgcar

        initial.incar.write_file(inputs / "INCAR")
        if fault != "static_missing":
            # The public historical pair did not include charge density. This
            # grid exercises the replay transport and delivery only.
            Chgcar(Poscar(initial.final_structure), {"total": np.ones((2, 2, 2))}).write_file(fixtures / "CHGCAR")
        if fault == "static_unconverged":
            outcar = fixtures / "OUTCAR"
            outcar.write_text(outcar.read_text().replace(
                "aborting loop because EDIFF is reached", "aborting loop before EDIFF is reached"))
    (inputs / "POTCAR").write_text("Synthetic fixture only\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    if static:
        (inputs / "POTCAR").write_text(
            "Synthetic fixture only\nTITEL = PAW_PBE Na_pv 19Sep2006\nLEXCH = PE\n"
            "TITEL = PAW_PBE Cl 06Sep2000\nLEXCH = PE\n"
        )
    database, sidecar, config = (tmp_path / name for name in ("archive.db", "sidecar.json", "aidb.json"))
    with connect(database) as db:
        db.count()
    sidecar.write_text('{"records": {}}')
    config.write_text(json.dumps({"database": {"backend": "ase_sqlite"}, "ase_sqlite": {
        "path": str(database), "json_sidecar_path": str(sidecar), "max_query_keys_per_row": 64,
    }}))
    session = SessionAIDB(workspace, "test-chat")
    binding = session.bind(config)
    adk = tmp_path / "adk"
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", adk)
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    replay = fake_bin / "vasp_std"
    replay.write_text('#!/bin/bash\nset -e\ncp "$MATCREATOR_TEST_FIXTURE/vasprun.xml" "$MATCREATOR_TEST_FIXTURE/OUTCAR" .\nif [[ -f "$MATCREATOR_TEST_FIXTURE/CHGCAR" ]]; then cp "$MATCREATOR_TEST_FIXTURE/CHGCAR" .; fi\n')
    replay.chmod(0o700)
    calls = []
    provider_results = tmp_path / "provider-results"
    provider_results.mkdir()
    outputs = provider_results / "run-1"

    class BatchProvider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            calls.append("submit")
            staged = Path(spec["input_root"]) / spec["input_path"]
            if calls == ["submit"] or fault == "independent":
                outputs = provider_results / f"run-{calls.count('submit')}"
                shutil.copytree(staged, outputs)
                subprocess.run(spec["command"], cwd=outputs, shell=True, check=True, env={
                    **os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                    "MATCREATOR_TEST_FIXTURE": str(fixtures),
                })
            return f"provider-run-{calls.count('submit')}"

        def status(self, external_id):
            from matcreator.control_plane.providers.base import RemoteJobStatus

            return RemoteJobStatus(normalized_status="succeeded", snapshot={})

        def collect_outputs(self, external_id, destination):
            calls.append("download")
            outputs = provider_results / external_id.removeprefix("provider-")
            destination.mkdir()
            names = ("vasprun.xml", "OUTCAR", "matcreator-vasp-receipt.sha256",
                     *(("CHGCAR",) if (outputs / "CHGCAR").is_file() else ()))
            if calls.count("download") == 1 and fault in {"download_interrupted", "download_partial"}:
                names = ("vasprun.xml",)
            artifacts = [{"source": external_id, "destination": str(shutil.copyfile(outputs / name, destination / name))}
                         for name in names]
            if calls.count("download") == 1 and fault == "download_corrupt":
                (destination / "OUTCAR").write_bytes(b"truncated transport")
            if calls.count("download") == 1 and fault == "download_interrupted":
                raise KeyboardInterrupt("interrupted collector process")
            return artifacts

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: BatchProvider())
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    if md:
        context.state["_vasp_requested_output_text"] = "用 VASP 做 300 K NVT，1 fs，3 步，给我末帧和温度曲线"
    submission = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp", command="vasp_std",
        machine_type="test-cpu", input_path="input", out_files=["CHGCAR"] if static else None,
    )
    assert submission["status"] == "succeeded", submission
    job_id = submission["job_id"]
    if static or md:
        assert remote_job_tools._service().store.get_job(job_id)["specification"]["managed_vasp"]
    if fault == "readback":
        from matcreator.aidb.service import AIDBError

        original_detail = SessionAIDB.detail

        def unavailable_detail(self, *args, **kwargs):
            raise AIDBError("injected detail lookup failure")

        monkeypatch.setattr(SessionAIDB, "detail", unavailable_detail)
    if fault in {"write", "static_write", "md_write"}:
        with sqlite3.connect(database) as db:
            db.execute("CREATE TRIGGER block_archive BEFORE INSERT ON systems BEGIN "
                       "SELECT RAISE(ABORT, 'injected write failure'); END")
    elif fault == "receipt":
        (outputs / "matcreator-vasp-receipt.sha256").write_text("receipt for another job")
    elif fault == "binding":
        session.bind(config)
    if fault == "legacy_corrupt":
        # Recreate a collection persisted by the older, filename-only validator.
        service = remote_job_tools._service()
        legacy_cache = adk / "vasp-jobs" / job_id / "legacy"
        legacy_cache.parent.mkdir(parents=True)
        service.collect_job_outputs(job_id, legacy_cache, output_dir=str(workspace / "download"))
        (legacy_cache / "OUTCAR").write_bytes(b"truncated transport")
        assert session.query({"formula": "Si"})["records"] == []
    if fault == "download_interrupted":
        with pytest.raises(KeyboardInterrupt, match="interrupted collector process"):
            remote_job_tools.collect_remote_job_outputs(job_id, "download", context)
    elif fault in {"download_partial", "download_corrupt", "receipt"}:
        partial = remote_job_tools.collect_remote_job_outputs(job_id, "download", context)
        assert partial["status"] == "succeeded", partial
        assert not (workspace / "download" / "OUTCAR").exists()
        if fault == "receipt":
            assert session.query({"formula": "Si"})["records"] == []
            return
    if fault in {"download_interrupted", "download_partial", "download_corrupt"}:
        assert session.query({"formula": "Si"})["records"] == []
        context._invocation_context.invocation_id = "after-restart"
        collected = remote_job_tools.collect_remote_job_outputs(job_id, "ignored", context)
    else:
        collected = remote_job_tools.collect_remote_job_outputs(job_id, "download", context)
    assert collected["status"] == "collected", collected
    assert collected.get("aidb_result") == {
        "kind": "aidb_completion", "result_id": job_id, "session_id": "test-chat", "user_id": "owner",
    }, collected

    from matcreator.aidb import managed_vasp

    monkeypatch.setattr(managed_vasp, "ADK_DIR", adk)
    completed = managed_vasp.get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id)
    if md:
        assert calls == ["submit", "download"]
        if fault in {"md_incomplete", "md_truncated", "md_write"}:
            assert completed["status"] == "failed", completed
            assert completed["record_id"] is None
            assert session.query({"formula": "Si"})["records"] == []
            assert completed["failure_kind"] == ("archive_failed" if fault == "md_write" else "output_unverified")
            assert ("download/temperature-curve.html" in completed["artifact_paths"]) is (fault == "md_write")
            return
        assert completed["status"] == "verified", completed
        record = session.detail(completed["record_id"])["record"]
        assert record["calculation_type"] == "molecular_dynamics"
        assert record["frame_index"] == 2
        assert record["energy"] == -10.85400315
        assert record["source_metadata"]["vasp_facts"]["labels"]["ionic_converged"] is None
        assert session.query({"formula": "Si"})["matched_count"] == 1
        from matcreator.tools.relaxation_tools import vasp_relaxation_choice
        import asyncio

        def request_reuse():
            context._invocation_context.invocation_id = "turn-2"
            return remote_job_tools.submit_bohr_batchjob(
                context, project_id=123, name="reuse-md", image="test-vasp", command="vasp_std",
                machine_type="test-cpu", input_path="input",
            )

        if fault == "md_missing":
            assert "温度" in completed["artifact_delivery_issue"]
            assert "download/temperature-curve.html" not in completed["artifact_paths"]
            assert request_reuse()["status"] == "succeeded"
            assert calls == ["submit", "download", "submit"]
            return
        assert "download/temperature-curve.html" in completed["artifact_paths"]
        app = FastAPI()
        app.include_router(create_aidb_router(lambda *args: True, lambda *args, **kwargs: workspace))
        client = TestClient(app)
        base = "/api/users/owner/sessions/test-chat/aidb"
        exported = client.post(f"{base}/completions/{job_id}/records/{record['record_id']}/export",
                               json={"binding_id": binding["binding_id"]}).json()
        assert set(exported["files"]) == {"extxyz", "poscar", "html", "temperature"}
        result = client.get(f"{base}/exports/{exported['export_id']}/temperature")
        assert result.status_code == 200
        assert "289.76" in result.text
        choice = request_reuse()
        if fault == "md":
            # The untouched historical inputs omit an explicit seed and cannot
            # prove that starting another run would use the observed random state.
            assert choice["status"] == "succeeded", choice
            assert calls == ["submit", "download", "submit"]
            return
        assert choice["status"] == "waiting_choice", choice
        context._invocation_context.invocation_id = "turn-3"
        context.user_content = SimpleNamespace(role="user", parts=[SimpleNamespace(text="选用第一条。")])
        reused = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))
        assert reused["status"] == "reused", reused
        assert set(reused["delivery"]["files"]) == {"extxyz", "poscar", "temperature"}
        assert calls == ["submit", "download"]
        assert session.query({"formula": "Si"})["matched_count"] == 1

        context._invocation_context.invocation_id = "turn-4"
        context.user_content.parts[0].text = "再做一组 300 K NVT AIMD，给我温度曲线。"
        pending = remote_job_tools.submit_bohr_batchjob(
            context, project_id=123, name="reuse-md-again", image="test-vasp", command="vasp_std",
            machine_type="test-cpu", input_path="input",
        )
        assert pending["status"] == "waiting_choice", pending
        initial.incar["RANDOM_SEED"] = [496816048, 0, 0]
        initial.incar.write_file(inputs / "INCAR")
        context._invocation_context.invocation_id = "turn-5"
        context.user_content.parts[0].text = "选用第一条。"
        stale = asyncio.run(vasp_relaxation_choice("select", pending["choice_id"], context, ordinal=1))
        assert stale["status"] == "stale_input", stale
        assert calls == ["submit", "download"]
        return
    if static and fault != "static":
        assert completed["status"] == "failed", completed
        assert completed["record_id"] is None
        assert session.query({"formula": "NaCl"})["records"] == []
        assert completed["compute_status"] == {
            "static_missing": "incomplete", "static_unconverged": "unverified", "static_write": "succeeded",
        }[fault]
        assert {"download/OUTCAR", "download/vasprun.xml"} <= set(completed["artifact_paths"])
        assert calls == ["submit", "download"]
        return
    if fault == "readback":
        assert completed["status"] == "failed", completed
        assert completed["compute_status"] == "succeeded"
        assert completed["failure_kind"] == "archive_failed"
        with connect(database) as db:
            assert db.count() == 1
        archive_receipt = json.loads(Path(completed["archive_receipt"]).read_text())
        assert archive_receipt["status"] == "readback_failed"
        assert archive_receipt["record_id"] == completed["unverified_record_id"]
        monkeypatch.setattr(SessionAIDB, "detail", original_detail)
        events = _assert_chat_history_contains_completion(tmp_path, workspace, job_id, monkeypatch)
        recovered = managed_vasp.get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id)
        assert recovered["status"] == "verified", recovered
        assert recovered["record_id"] == completed["unverified_record_id"]
        assert recovered["outcome"] == "existing"
        if evidence_dir := os.environ.get("MATCREATOR_TICKET13_EVIDENCE_DIR"):
            Path(evidence_dir, "ticket13-browser-input.json").write_text(json.dumps({
                "owner": "owner", "user_id": "owner", "session_id": "test-chat", "workspace": str(workspace),
                "job_id": job_id, "record_id": recovered["record_id"], "adk_dir": str(adk), "events": events,
                "failed_completion": completed, "recovered_completion": recovered,
            }, ensure_ascii=False, indent=2))
        assert remote_job_tools.collect_remote_job_outputs(job_id, "download", context)["aidb_result"] == collected["aidb_result"]
        assert calls == ["submit", "download"]
        with connect(database) as db:
            assert db.count() == 1
        return
    if fault in {"write", "binding"}:
        assert completed["status"] == "failed", completed
        assert completed["record_id"] is None
        assert set(completed["artifact_paths"]) == {"download/vasprun.xml", "download/OUTCAR"}
        assert session.query({"formula": "Si"})["records"] == []
        assert completed["compute_status"] == "succeeded"
        if fault != "write":
            return
        with sqlite3.connect(database) as db:
            db.execute("DROP TRIGGER block_archive")
        recovered = remote_job_tools.collect_remote_job_outputs(job_id, "another-destination", context)
        assert recovered["aidb_result"] == collected["aidb_result"]
        completed = managed_vasp.get_managed_vasp_completion(user_id="owner", session_id="test-chat", job_id=job_id)
    assert completed["status"] == "verified", completed.get("reason")
    assert completed["compute_status"] == "succeeded"
    assert completed["outcome"] == "added"
    assert completed["binding_id"] == binding["binding_id"]
    if static:
        assert completed["summary"] == {"formula": "NaCl", "energy_ev_per_cell": -27.96859470, "frame_index": 0}
        record = session.detail(completed["record_id"])["record"]
        assert record["calculation_type"] == "static"
        assert record["source_metadata"]["vasp_facts"]["labels"]["ionic_converged"] is None
        assert (workspace / "download/CHGCAR").read_bytes() == (fixtures / "CHGCAR").read_bytes()

        import asyncio
        from matcreator.tools.relaxation_tools import vasp_relaxation_choice

        context._invocation_context.invocation_id = "turn-2"
        context.user_content = SimpleNamespace(role="user", parts=[
            SimpleNamespace(text="用 VASP 做静态自洽计算，给我总能量和电荷密度。"),
        ])
        assert session.query({"formula": "NaCl", "calculation_type": "static"})["records"]
        choice = remote_job_tools.submit_bohr_batchjob(
            context, project_id=123, name="reuse-scf", image="test-vasp", command="vasp_std",
            machine_type="test-cpu", input_path="input", out_files=["CHGCAR"],
        )
        assert choice["status"] == "waiting_choice", choice
        assert [row["record_id"] for row in choice["candidates"]["records"]] == [record["record_id"]]

        context._invocation_context.invocation_id = "turn-3"
        context.user_content.parts[0].text = "选用第一条，给我电荷密度和微调数据。"
        result = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))
        assert result["status"] == "reused", result
        assert result["delivery"]["files"]["CHGCAR"]["filename"] == "CHGCAR"
        assert result["delivery"]["files"]["extxyz"]
        delivered = workspace / result["delivery"]["artifact_paths"]["CHGCAR"]
        assert delivered.read_bytes() == (fixtures / "CHGCAR").read_bytes()

        app = FastAPI()
        app.include_router(create_aidb_router(lambda owner, chat: (owner, chat) == ("owner", "test-chat"),
                                              lambda session_id, user_id: workspace))
        base = "/api/users/owner/sessions/test-chat/aidb"
        with TestClient(app) as client:
            export_id = result["delivery"]["export_id"]
            downloaded = client.get(f"{base}/exports/{export_id}/CHGCAR")
            assert downloaded.status_code == 200
            assert downloaded.content == delivered.read_bytes()
            training = client.get(f"{base}/exports/{export_id}/extxyz")
            atoms = read(StringIO(training.text), format="extxyz")
            assert atoms.get_potential_energy() == -27.96859470
            assert len(atoms.get_forces()) == 8

        context._invocation_context.invocation_id = "turn-4"
        context.user_content.parts[0].text = "再算一组静态 SCF，给我总能量和电荷密度。"
        pending = remote_job_tools.submit_bohr_batchjob(
            context, project_id=123, name="reuse-scf-again", image="test-vasp", command="vasp_std",
            machine_type="test-cpu", input_path="input", out_files=["CHGCAR"],
        )
        assert pending["status"] == "waiting_choice"
        next(adk.glob("vasp-jobs/**/CHGCAR")).unlink()
        context._invocation_context.invocation_id = "turn-5"
        context.user_content.parts[0].text = "选用第一条。"
        incomplete = asyncio.run(vasp_relaxation_choice("select", pending["choice_id"], context, ordinal=1))
        assert incomplete["status"] == "not_ready", incomplete
        assert "delivery" not in incomplete
        assert calls == ["submit", "download"]
        return
    assert completed["summary"] == {"formula": "Si", "energy_ev_per_cell": -43.38227814, "frame_index": 2}
    assert managed_vasp.get_managed_vasp_completion(user_id="another-owner", session_id="test-chat", job_id=job_id) is None
    assert set(completed["artifact_paths"]) == {"download/vasprun.xml", "download/OUTCAR"}

    repeated = remote_job_tools.collect_remote_job_outputs(job_id, "another-destination", context)
    assert repeated["aidb_result"] == collected["aidb_result"]
    if fault in {"download_interrupted", "download_partial"}:
        [record] = session.query({"formula": "Si"})["records"]
        assert record["record_id"] == completed["record_id"]
        assert (workspace / "download/vasprun.xml").read_bytes() == (fixtures / "vasprun.xml").read_bytes()
        assert calls == ["submit", "download", "download"]
        return
    if fault == "independent":
        import asyncio

        from matcreator.tools.aidb_archive_tools import aidb_archive_vasp
        from matcreator.tools.relaxation_tools import vasp_relaxation_choice

        context._invocation_context.invocation_id = "turn-2"
        group = remote_job_tools.create_remote_job_group(context, name="independent-batch", expected_jobs=1)

        def submit_second():
            return remote_job_tools.submit_bohr_batchjob(
                context, project_id=123, name="independent", image="test-vasp", command="vasp_std",
                machine_type="test-cpu", input_path="input", group_id=group["group_id"],
            )

        pending = submit_second()
        assert pending["status"] == "waiting_choice", pending
        context._invocation_context.invocation_id = "turn-3"
        context.user_content = SimpleNamespace(role="user", parts=[SimpleNamespace(text="继续计算。")])
        choice = asyncio.run(vasp_relaxation_choice("continue", pending["choice_id"], context))
        assert choice["status"] == "ready_to_continue", choice
        second = submit_second()
        assert second["status"] == "succeeded", second
        assert second["job_id"] != job_id
        second_collect = remote_job_tools.collect_remote_job_outputs(second["job_id"], "second", context)
        assert second_collect["archive_status"] == "verified", second_collect
        second_completion = managed_vasp.get_managed_vasp_completion(
            user_id="owner", session_id="test-chat", job_id=second["job_id"],
        )
        assert second_completion["record_id"] != completed["record_id"]
        explicit = asyncio.run(aidb_archive_vasp(["download", "second"], context))
        assert [item["status"] for item in explicit["items"]] == ["existing", "existing"], explicit
        assert [item["record_id"] for item in explicit["items"]] == [
            completed["record_id"], second_completion["record_id"],
        ]
        assert all(item["readback_verified"] for item in explicit["items"])
        assert len(session.query({"formula": "Si"})["records"]) == 2
        assert calls == ["submit", "download", "submit", "download"]
        return
    if fault is None:
        _assert_chat_history_contains_completion(tmp_path, workspace, job_id, monkeypatch)
        import asyncio

        from matcreator.tools.aidb_archive_tools import aidb_archive_vasp

        explicitly_saved = asyncio.run(aidb_archive_vasp(["download"], context))
        assert explicitly_saved["status"] == "existing", explicitly_saved
        [item] = explicitly_saved["items"]
        assert item["record_id"] == completed["record_id"]
        assert item["readback_verified"] is True
    expected = ["submit", "download"]
    if fault in {"download_corrupt", "legacy_corrupt"}:
        expected.append("download")
    assert calls == expected
    [record] = session.query({"formula": "Si"})["records"]
    assert record["record_id"] == completed["record_id"]
    submitted = record["source_metadata"]["vasp_facts"]["source"]["submitted_inputs"]
    assert (submitted["provider"], submitted["run_id"]) == ("bohr_batchjob", "provider-run-1")
    assert set(submitted["sha256"]) == {"INCAR", "KPOINTS", "POSCAR", "POTCAR"}
    app = FastAPI()
    app.include_router(create_aidb_router(
        lambda user_id, session_id: (user_id, session_id) == ("owner", "test-chat"),
        lambda session_id, user_id: workspace,
    ))
    base = "/api/users/owner/sessions/test-chat/aidb"
    row = f"{base}/completions/{job_id}"
    record_id = record["record_id"]
    binding_id = {"binding_id": binding["binding_id"]}
    with TestClient(app) as client:
        response = client.get(row)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["archive_status"] == "verified"
        assert result["summary"] == completed["summary"]
        assert [item["record_id"] for item in result["records"]] == [record_id]
        detail = client.get(f"{base}/records/{record_id}", params=binding_id)
        assert detail.status_code == 200, detail.text
        assert detail.json()["record"]["record_id"] == record_id
        artifact = client.get(f"{row}/artifact", params={"path": "download/vasprun.xml"})
        assert artifact.status_code == 200
        assert artifact.content == (fixtures / "vasprun.xml").read_bytes()

        for _ in range(2):
            exported = client.post(f"{row}/records/{record_id}/export", json=binding_id)
            assert exported.status_code == 200, exported.text
            export_id = exported.json()["export_id"]
            downloaded = client.get(f"{base}/exports/{export_id}/extxyz")
            assert downloaded.status_code == 200, downloaded.text
            atoms = read(StringIO(downloaded.text), format="extxyz")
            assert atoms.info["record_id"] == record_id
            assert atoms.get_chemical_symbols() == ["Si"] * 8
            np.testing.assert_allclose(atoms.cell.array, initial.final_structure.lattice.matrix, atol=1e-8)
            np.testing.assert_allclose(atoms.positions, initial.final_structure.cart_coords, atol=1e-8)
            assert atoms.get_potential_energy() == -43.38227814
            assert len(atoms.get_forces()) == len(record["forces"]) == 8
            np.testing.assert_allclose(atoms.get_forces(), record["forces"])
            np.testing.assert_allclose(atoms.get_stress(voigt=False), record["stress"])
    assert calls == expected
    with connect(database) as db:
        assert db.count() == 1


def _assert_chat_history_contains_completion(tmp_path, workspace, job_id, monkeypatch):
    import asyncio

    from google.adk.agents import LlmAgent
    from google.adk.models import LlmResponse
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.adk.tools import FunctionTool
    from google.genai import types
    from matcreator.agents import graph_logger
    from matcreator.agents.thinking_agent.agent import run_flash_step

    monkeypatch.setattr(graph_logger, "ADK_DIR", tmp_path / "adk")
    action = "请继续回收之前跟踪的 VASP 弛豫作业，重试归档并展示已核实的结果。"
    script = iter([
        types.Part.from_function_call(name="run_flash_step", args={
            "action": action, "suggested_skills": [],
        }),
        types.Part.from_function_call(name="collect_remote_job_outputs", args={
            "job_id": job_id, "destination_path": "download",
        }),
        types.Part.from_function_call(name="submit_step_result", args={
            "status": "success", "key_results": "Collected", "concise_summary": "Finished",
        }),
        types.Part.from_text(text="The relaxation outputs are ready."),
        types.Part.from_text(text="The relaxation outputs are ready."),
    ])

    async def model_boundary(*args, **kwargs):
        yield LlmResponse(content=types.Content(role="model", parts=[next(script)]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", model_boundary)

    async def run_chat():
        sessions = InMemorySessionService()
        identity = {"app_name": "managed_vasp", "user_id": "owner", "session_id": "test-chat"}
        await sessions.create_session(**identity, state={
            "session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace),
        })
        agent = LlmAgent(name="chat", model=LiteLlm(model="openai/test"), tools=[FunctionTool(run_flash_step)])
        message = types.Content(role="user", parts=[types.Part.from_text(text=action)])
        async with Runner(app_name=identity["app_name"], agent=agent, session_service=sessions) as runner:
            async for _ in runner.run_async(user_id="owner", session_id="test-chat", new_message=message):
                pass
        history = await sessions.get_session(**identity)
        return history.events

    events = asyncio.run(run_chat())
    [response] = [item.response for event in events for item in event.get_function_responses()
                  if item.name == "run_flash_step"]
    assert response["status"] == "success", response
    assert response.get("aidb_completions") == [{
        "kind": "aidb_completion", "result_id": job_id, "session_id": "test-chat", "user_id": "owner",
    }]
    return [event.model_dump(mode="json", by_alias=True, exclude_none=True) for event in events]
