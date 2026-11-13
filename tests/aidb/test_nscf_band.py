from pathlib import Path
import gzip
import re
import shutil

import pytest

from matcreator.aidb.service import AIDBError
from matcreator.aidb.vasp_archive import parse_vasp_result


HISTORICAL_BANDS = Path(__file__).parent / "fixtures/band/non-scf_line"


def test_historical_nscf_requires_complete_eigenvalues_and_the_actual_kpath(tmp_path):
    raw = parse_vasp_result(HISTORICAL_BANDS)
    assert raw["calculation_type"] == "nscf"
    facts = raw["source_metadata"]["vasp_facts"]
    assert facts["labels"]["ionic_converged"] is None
    assert facts["band_structure"]["nkpoints"] == 164
    assert facts["band_structure"]["nbands"] == 16
    assert facts["band_structure"]["fermi_energy_ev"] == 5.7018

    shutil.copytree(HISTORICAL_BANDS, tmp_path / "missing-bands")
    incomplete = tmp_path / "missing-bands"
    xml = gzip.decompress((incomplete / "vasprun.xml.gz").read_bytes())
    xml = re.sub(rb"<eigenvalues>.*?</eigenvalues>", b"", xml, flags=re.S)
    (incomplete / "vasprun.xml.gz").write_bytes(gzip.compress(xml))
    with pytest.raises(AIDBError, match="能带"):
        parse_vasp_result(incomplete)

    shutil.copytree(HISTORICAL_BANDS, tmp_path / "wrong-path")
    wrong_path = tmp_path / "wrong-path"
    points = gzip.decompress((wrong_path / "KPOINTS.gz").read_bytes())
    (wrong_path / "KPOINTS.gz").write_bytes(gzip.compress(points.replace(b"0.02173913043478261", b"0.03173913043478261", 1)))
    with pytest.raises(AIDBError, match="k"):
        parse_vasp_result(wrong_path)


def prepare_band_input(folder):
    from pymatgen.io.vasp import Poscar, Vasprun

    folder.mkdir()
    raw = Vasprun(HISTORICAL_BANDS / "vasprun.xml.gz", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    raw.incar.write_file(folder / "INCAR")
    Poscar(raw.initial_structure).write_file(folder / "POSCAR")
    (folder / "KPOINTS").write_bytes(gzip.decompress((HISTORICAL_BANDS / "KPOINTS.gz").read_bytes()))
    (folder / "POTCAR").write_text("Synthetic fixture only\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")


def test_nscf_without_verified_charge_never_reaches_either_provider(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from matcreator.agents.execution_agent import remote_job_tools

    folder = tmp_path / "band"
    prepare_band_input(folder)
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(tmp_path), "workspace_dir": str(tmp_path)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )

    def unreachable(*args, **kwargs):
        pytest.fail("unverified NSCF input reached a remote provider")

    monkeypatch.setattr(remote_job_tools, "_submit", unreachable)
    batch = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="band", image="vasp", machine_type="test", command="vasp_std",
        input_path="band",
    )
    sandbox = remote_job_tools.start_managed_vasp_sandbox(
        "band", "vasp_std", context, template="test", project_id=123,
    )
    for result in (batch, sandbox):
        assert result["status"] == "error"
        assert "CHGCAR" in result["message"]


def test_band_chain_submits_and_reuses_each_stage_independently(tmp_path, monkeypatch):
    import asyncio
    import json
    import os
    import subprocess
    from types import SimpleNamespace

    import numpy as np
    from ase.db import connect
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pymatgen.io.vasp import Chgcar, Poscar, Vasprun

    from matcreator.aidb import managed_vasp
    from matcreator.aidb.service import SessionAIDB
    from matcreator.agents.execution_agent import remote_job_tools
    from matcreator.control_plane.providers.base import RemoteJobCapability, RemoteJobStatus
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice
    from web.aidb_api import create_aidb_router

    if not (Path.home() / ".matcreator/skills/ai-ready-db/scripts/aidb_ops.py").is_file():
        pytest.skip("Install ai-ready-db for isolated archive integration tests")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    band_input = workspace / "band"
    prepare_band_input(band_input)
    scf_input = workspace / "scf"
    scf_input.mkdir()
    shutil.copyfile(band_input / "POTCAR", scf_input / "POTCAR")
    scf_replay = tmp_path / "scf-replay"
    scf_replay.mkdir()
    historical_scf = HISTORICAL_BANDS.with_name("static")
    xml = gzip.decompress((historical_scf / "vasprun.xml.gz").read_bytes())
    # The historical producer named this one-step fixed-geometry run static
    # but set NSW=1. Inject the NSW=0/IBRION=-1 classification in this replay.
    xml = re.sub(rb'(<i type="int" name="NSW">)\s+1(\s*</i>)', rb'\g<1>0\2', xml)
    xml = re.sub(rb'(<i type="int" name="IBRION">)\s+2(\s*</i>)', rb'\g<1>-1\2', xml)
    (scf_replay / "vasprun.xml").write_bytes(xml)
    (scf_replay / "OUTCAR").write_bytes(gzip.decompress((historical_scf / "OUTCAR.gz").read_bytes()))
    run = Vasprun(scf_replay / "vasprun.xml", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    run.incar.write_file(scf_input / "INCAR")
    Poscar(run.initial_structure).write_file(scf_input / "POSCAR")
    # The public CHGCAR is empty. Synthetic density tests transport and
    # identity only. The band output below is the unmodified public NSCF run.
    Chgcar(Poscar(run.final_structure), {"total": np.ones((2, 2, 2))}).write_file(scf_replay / "CHGCAR")
    band_replay = tmp_path / "band-replay"
    band_replay.mkdir()
    for name in ("vasprun.xml", "OUTCAR"):
        (band_replay / name).write_bytes(gzip.decompress((HISTORICAL_BANDS / (name + ".gz")).read_bytes()))

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
    monkeypatch.setattr(managed_vasp, "ADK_DIR", adk)
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    executable = tmp_path / "vasp_std"
    executable.write_text('#!/bin/bash\nset -e\ncp "$REPLAY/vasprun.xml" "$REPLAY/OUTCAR" .\n'
                          'if [[ -f "$REPLAY/CHGCAR" ]]; then cp "$REPLAY/CHGCAR" .; fi\n')
    executable.chmod(0o700)
    submissions = []

    class Provider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            stage = Path(spec["input_root"]) / spec["input_path"]
            job_id = f"historical-replay-{len(submissions)}"
            submissions.append(spec["input_path"])
            output = shutil.copytree(stage, tmp_path / job_id)
            replay = scf_replay if spec["input_path"] == "scf" else band_replay
            if replay == band_replay:
                assert (output / "CHGCAR").read_bytes() == (scf_replay / "CHGCAR").read_bytes()
            subprocess.run(spec["command"], cwd=output, shell=True, check=True,
                           env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "REPLAY": str(replay)})
            return job_id

        def status(self, external_id):
            return RemoteJobStatus(normalized_status="succeeded", snapshot={})

        def collect_outputs(self, external_id, destination):
            destination.mkdir()
            outputs = tmp_path / external_id
            names = ["vasprun.xml", "OUTCAR", managed_vasp.RECEIPT]
            if "scf" == submissions[int(external_id.rsplit("-", 1)[-1])]:
                names.append("CHGCAR")
            return [{"source": external_id, "destination": str(shutil.copyfile(outputs / name, destination / name))}
                    for name in names]

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: Provider())
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-0"),
    )

    def turn(number, text="给我硅的能带数据和图。", *, node=None):
        context._invocation_context.invocation_id = f"turn-{number}"
        context.user_content = SimpleNamespace(role="user", parts=[SimpleNamespace(text=text)])
        context.state["_graph_exec_node_id"] = f"step__node_{node if node is not None else number}"

    def submit(stage, **kwargs):
        return remote_job_tools.submit_bohr_batchjob(
            context, project_id=123, name=stage, image="vasp", machine_type="test", command="vasp_std",
            input_path=stage, **kwargs,
        )

    def collect(job, stage):
        assert job["status"] == "succeeded", job
        receipt = remote_job_tools.collect_remote_job_outputs(job["job_id"], stage, context)
        assert receipt["archive_status"] == "verified", receipt
        return receipt

    turn(1)
    upstream = collect(submit("scf"), "scf-results")
    scf_id = upstream["record_id"]
    shutil.copyfile(workspace / "scf-results/CHGCAR", band_input / "CHGCAR")
    turn(2)
    band_job = submit("band", upstream_record_id=scf_id)
    band = collect(band_job, "band-results")
    band_id = band["record_id"]
    record = session.detail(band_id)["record"]
    assert record["calculation_type"] == "nscf"
    source = record["source_metadata"]["vasp_facts"]["source"]
    assert source["upstream_scf"]["record_id"] == scf_id
    assert source["upstream_scf"]["source_id"] == session.detail(scf_id)["record"]["source_id"]
    data = json.loads((workspace / "band-results/bands.json").read_text())
    assert len(data["spins"]["up"]["energies_ev"]) == 164
    assert data["spins"]["up"]["energies_ev"][0][:3] == [-6.1987, 5.6162, 5.6162]
    assert "<svg" in (workspace / "band-results/bands.svg").read_text()
    assert submissions == ["scf", "band"]

    app = FastAPI()
    app.include_router(create_aidb_router(lambda owner, chat: (owner, chat) == ("owner", "test-chat"),
                                          lambda session_id, user_id: workspace))
    with TestClient(app) as client:
        base = "/api/users/owner/sessions/test-chat/aidb"
        completion = f"{base}/completions/{band_job['job_id']}"
        exported = client.post(f"{completion}/records/{band_id}/export",
                               json={"binding_id": binding["binding_id"]})
        assert exported.status_code == 200, exported.text
        export_id = exported.json()["export_id"]
        assert client.get(f"{base}/exports/{export_id}/bands.json").json() == data
        assert "<svg" in client.get(f"{base}/exports/{export_id}/bands.svg").text

    turn(3)
    choice = submit("scf")
    assert choice["status"] == "waiting_choice", choice
    turn(4, "选用第一条。")
    reused_scf = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))
    assert reused_scf["status"] == "reused", reused_scf
    assert reused_scf["delivery"]["record_id"] == scf_id
    assert reused_scf["delivery"]["artifact_paths"]["CHGCAR"]
    shutil.copyfile(workspace / reused_scf["delivery"]["artifact_paths"]["CHGCAR"], band_input / "CHGCAR")

    turn(5)
    choice = submit("band", upstream_record_id=scf_id)
    assert choice["status"] == "waiting_choice", choice
    turn(6, "选用第一条。")
    reused_band = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))
    assert reused_band["status"] == "reused", reused_band
    assert reused_band["delivery"]["record_id"] == band_id
    assert {"bands.json", "bands.svg"} <= reused_band["delivery"]["files"].keys()
    assert submissions == ["scf", "band"]
    with connect(database) as db:
        assert db.count() == 2

    # A new SCF can reproduce the exact retained charge bytes used by
    # a previous band run. Its separate record must still be preserved.
    turn(7)
    recalculate = submit("scf")
    assert recalculate["status"] == "waiting_choice", recalculate
    turn(8, "继续计算。", node=7)
    assert asyncio.run(vasp_relaxation_choice("continue", recalculate["choice_id"], context))["status"] == "ready_to_continue"
    second_scf = collect(submit("scf"), "second-scf-results")["record_id"]
    assert second_scf != scf_id
    turn(9)
    mixed = submit("band", upstream_record_id=second_scf)
    assert mixed["status"] == "waiting_choice", mixed
    turn(10, "选用第一条。")
    reused = asyncio.run(vasp_relaxation_choice("select", mixed["choice_id"], context, ordinal=1))
    assert reused["delivery"]["record_id"] == band_id
    assert submissions == ["scf", "band", "scf"]

    turn(11)
    recalculate = submit("band", upstream_record_id=scf_id)
    assert recalculate["status"] == "waiting_choice", recalculate
    turn(12, "继续计算。", node=11)
    assert asyncio.run(vasp_relaxation_choice("continue", recalculate["choice_id"], context))["status"] == "ready_to_continue"
    second_band_job = submit("band", upstream_record_id=scf_id)
    second_band = collect(second_band_job, "second-band-results")["record_id"]
    assert second_band != band_id
    assert submissions == ["scf", "band", "scf", "band"]

    turn(13)
    choice = submit("band", upstream_record_id=scf_id)
    assert choice["status"] == "waiting_choice", choice
    record_by_id = {row["record_id"]: row for row in choice["candidates"]["records"]}
    assert band_id in record_by_id
    lost_plot_file = next((adk / "vasp-jobs" / source["band_artifacts"]["job_id"]).glob("**/bands.svg"))
    lost_plot_file.unlink()
    turn(14, "选用原来的能带结果。")
    missing_band = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, record_id=band_id))
    assert missing_band["status"] == "not_ready", missing_band
    turn(15, "取消。")
    assert asyncio.run(vasp_relaxation_choice("cancel", choice["choice_id"], context))["status"] == "cancelled"
    turn(16)
    wrong_stage = submit("band", upstream_record_id=band_id)
    assert wrong_stage["status"] == "error", wrong_stage
    assert "静态 SCF" in wrong_stage["message"]
    scf_source = session.detail(scf_id)["record"]["source_metadata"]["vasp_facts"]["source"]
    lost_charge_file = next((adk / "vasp-jobs" / scf_source["restart_artifacts"]["job_id"]).glob("**/CHGCAR"))
    lost_charge_file.unlink()
    turn(17)
    lost_charge = submit("band", upstream_record_id=scf_id)
    assert lost_charge["status"] == "error", lost_charge
    assert submissions == ["scf", "band", "scf", "band"]

    # Run dialogue against the remaining complete records.
    events = _band_chain_chat(workspace, second_scf, second_band, second_band_job["job_id"], monkeypatch)
    assert events
    if evidence_file := os.environ.get("MATCREATOR_BAND_BROWSER_EVIDENCE"):
        # Preserve the original fixture bytes after the fault checks so
        # browser acceptance runs against the intact archive and receipts.
        shutil.copyfile(workspace / "band-results/bands.svg", lost_plot_file)
        shutil.copyfile(workspace / "scf-results/CHGCAR", lost_charge_file)
        output = Path(evidence_file)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({
            "owner": "owner", "session": "test-chat", "workspace": str(workspace), "adk_dir": str(adk),
            "job_id": second_band_job["job_id"], "record_id": second_band, "scf_record_id": second_scf,
            "binding_id": binding["binding_id"], "events": [event.model_dump(mode="json") for event in events],
        }, ensure_ascii=False), encoding="utf-8")


def _band_chain_chat(workspace, scf_id, band_id, band_job_id, monkeypatch):
    import asyncio
    from collections import deque

    from google.adk.models import LlmResponse
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from matcreator.agents import graph_logger
    from matcreator.agents.execution_agent.agent import execution_agent
    from matcreator.agents.orchestrator.agent import PlanningExecutionOrchestrator
    from matcreator.agents.thinking_agent.agent import thinking_agent

    monkeypatch.setattr(graph_logger, "ADK_DIR", workspace.parent / "adk")
    script = deque()

    async def model_boundary(*args, **kwargs):
        assert script, "unexpected model call during isolated dialogue"
        yield LlmResponse(content=types.Content(role="model", parts=[script.popleft()]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", model_boundary)
    call = types.Part.from_function_call
    say = types.Part.from_text

    async def run_chat():
        sessions = InMemorySessionService()
        agent = PlanningExecutionOrchestrator(name="ticket09", planning_agent=thinking_agent.clone(),
                                              execution_agent=execution_agent.clone())
        identity = {"app_name": "ticket09", "user_id": "owner", "session_id": "test-chat"}
        await sessions.create_session(**identity, state={
            "session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace),
            "session_workdir_initialized": True, "agent_mode": "flash",
        })

        async def chat(runner, text, responses):
            script.extend(responses)
            message = types.Content(role="user", parts=[say(text=text)])
            receipts = []
            async for event in runner.run_async(user_id="owner", session_id="test-chat", new_message=message):
                receipts.extend((part.name, part.response) for part in event.get_function_responses()
                                if part.name in {"run_flash_step", "vasp_relaxation_choice"})
            assert not script, list(script)
            return receipts

        def stage(label, text, **kwargs):
            return [
                call(name="run_flash_step", args={"action": text, "suggested_skills": [], "label": label}),
                call(name="submit_bohr_batchjob", args={
                    "project_id": 123, "name": label, "image": "vasp", "machine_type": "test",
                    "command": "vasp_std", "input_path": label, **kwargs,
                }),
                say(text="找到已核实的结果，请确认是否选用。"),
            ]

        async with Runner(app_name=identity["app_name"], agent=agent, session_service=sessions) as runner:
            [(_, collected)] = await chat(runner, "收取已经运行的硅能带作业，给我数据和图。", [
                call(name="run_flash_step", args={"action": "收取已经运行的能带作业", "suggested_skills": []}),
                call(name="collect_remote_job_outputs", args={
                    "job_id": band_job_id, "destination_path": "second-band-results",
                }),
                call(name="submit_step_result", args={
                    "status": "success", "key_results": "已核实归档及下载文件",
                    "concise_summary": "能带数据和图已交付。",
                }),
                say(text="能带作业的归档回执已核实。"),
                say(text="能带数据和图已交付。"),
            ])
            assert collected["status"] == "success", collected
            assert collected["aidb_completions"][0]["result_id"] == band_job_id
            prompt = "用 VASP 算硅的能带，先做静态自洽，再沿硅晶体高对称路径计算，给我能带数据和图。"
            [(_, first)] = await chat(runner, prompt, stage("scf", "静态自洽并保留 CHGCAR", out_files=["CHGCAR"]))
            assert first["status"] == "waiting_choice", first
            assert first["candidates"]["records"][0]["record_id"] == scf_id
            [(_, upstream)] = await chat(runner, "选用第一条静态自洽结果。", [
                call(name="vasp_relaxation_choice", args={"action": "select", "choice_id": first["choice_id"], "ordinal": 1}),
                say(text="已交付静态自洽结果和 CHGCAR。"),
            ])
            assert upstream["status"] == "reused", upstream
            shutil.copyfile(workspace / upstream["delivery"]["artifact_paths"]["CHGCAR"], workspace / "band/CHGCAR")
            [(_, second)] = await chat(runner, "继续沿高对称路径计算硅的能带并给我数据和图。", stage(
                "band", "以已交付的静态 SCF 电荷密度计算 NSCF 能带", upstream_record_id=scf_id,
            ))
            assert second["status"] == "waiting_choice", second
            assert second["candidates"]["records"][0]["record_id"] == band_id
            [(_, downstream)] = await chat(runner, "选用第一条能带结果，给我数据和图。", [
                call(name="vasp_relaxation_choice", args={"action": "select", "choice_id": second["choice_id"], "ordinal": 1}),
                say(text="能带数据和图已交付。"),
            ])
            assert downstream["status"] == "reused", downstream
            assert {"bands.json", "bands.svg"} <= downstream["delivery"]["files"].keys()
        return (await sessions.get_session(**identity)).events

    return asyncio.run(run_chat())
