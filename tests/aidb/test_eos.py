"""EOS through managed VASP tools with an isolated provider and real output replay."""
from hashlib import sha256
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from pymatgen.io.vasp import Incar, Poscar, Vasprun

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.control_plane.providers.base import RemoteJobCapability, RemoteJobStatus


def test_eos_waits_for_verified_relaxation_before_submitting_volume_points(tmp_path, monkeypatch):
    from matcreator.agents.execution_agent.eos_tools import collect_eos, submit_eos

    workspace = tmp_path / "workspace"
    relaxation = workspace / "relaxation"
    static = workspace / "static"
    relaxation.mkdir(parents=True)
    static.mkdir()
    fixture = (Path(__file__).resolve().parent /
               "fixtures/vasp/si-pbe-relax")
    parsed = Vasprun(fixture / "vasprun.xml", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    for directory in (relaxation, static):
        Poscar(parsed.initial_structure).write_file(directory / "POSCAR")
        (directory / "POTCAR").write_text("Fixture header\nTITEL = " + parsed.potcar_symbols[0] + "\nLEXCH = PE\n")
    for name in ("INCAR", "KPOINTS"):
        shutil.copyfile(fixture / name, relaxation / name)
    Incar({"IBRION": -1, "NSW": 0, "KSPACING": .22}).write_file(static / "INCAR")

    monkeypatch.setattr(remote_job_tools, "ADK_DIR", tmp_path / "adk")
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    submissions = []

    class Provider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            submissions.append(spec["name"])
            return str(len(submissions))

        def status(self, external_id):
            return RemoteJobStatus(normalized_status="succeeded", snapshot={})

        def collect_outputs(self, external_id, destination):
            # A provider-side success without verified VASP output cannot be used for EOS.
            destination.mkdir()
            return []

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: Provider())
    context = SimpleNamespace(
        state={"session_id": "test", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    receipt = submit_eos(context, relaxation_path="relaxation", static_template_path="static",
                         name="eos", image="fixture", command="vasp_std",
                         project_id=123, machine_type="test-cpu")
    assert receipt["status"] == "running", receipt
    assert submissions == ["eos-relaxation"]
    context.state["_vasp_human_message"] = "继续已经授权的体积点计算。"
    result = collect_eos(context, receipt["eos_id"], advance=True)
    assert result["status"] == "blocked_relaxation", result
    assert "reason" in result["relaxation"]
    assert all(row["status"] == "blocked" for row in result["points"])
    assert submissions == ["eos-relaxation"]


def test_eos_chat_replays_relaxation_and_reports_verified_mixed_volume_results(tmp_path, monkeypatch):
    import asyncio
    from collections import deque
    from copy import deepcopy
    import csv
    import os
    import subprocess

    import numpy as np
    import pytest
    from ase.db import connect
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from google.adk.models import LlmResponse
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from pymatgen.core import Structure
    from pymatgen.io.vasp import Kpoints

    from matcreator.agents import graph_logger
    from matcreator.agents.execution_agent.agent import execution_agent
    from matcreator.agents.orchestrator.agent import PlanningExecutionOrchestrator
    from matcreator.agents.thinking_agent.agent import thinking_agent
    from matcreator.aidb import managed_vasp
    from matcreator.aidb.service import SessionAIDB
    from web.aidb_api import create_aidb_router

    if not (Path.home() / ".matcreator/skills/ai-ready-db/scripts/aidb_ops.py").is_file():
        pytest.skip("Install ai-ready-db for isolated EOS replay")

    fixture = (Path(__file__).resolve().parent /
               "fixtures/vasp/si-pbe-relax")
    genuine_parse = managed_vasp.parse_vasp_result
    historical = genuine_parse(fixture)
    relaxed = Structure.from_dict(historical["structure"])
    starting = Structure.from_dict(historical["source_metadata"]["vasp_facts"]["initial_structure"]["structure"])
    assert relaxed.volume == pytest.approx(163.52857383287443)
    assert starting.volume == pytest.approx(160.103007)

    workspace = tmp_path / "workspace"
    for name in ("relaxation", "template", "seed"):
        directory = workspace / name
        directory.mkdir(parents=True)
        (directory / "POTCAR").write_text("Fixture header\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
        Poscar(relaxed if name == "seed" else starting).write_file(directory / "POSCAR")
        shutil.copyfile(fixture / "KPOINTS", directory / "KPOINTS")
        if name == "relaxation":
            shutil.copyfile(fixture / "INCAR", directory / "INCAR")
        else:
            Incar({"IBRION": -1, "NSW": 0, "ENCUT": 520}).write_file(directory / "INCAR")

    database = tmp_path / "archive.db"
    with connect(database) as db:
        db.count()
    sidecar = tmp_path / "archive-sidecar.json"
    sidecar.write_text('{"records":{}}')
    config = tmp_path / "aidb.json"
    config.write_text(json.dumps({"database": {"backend": "ase_sqlite"}, "ase_sqlite": {
        "path": str(database), "json_sidecar_path": str(sidecar), "max_query_keys_per_row": 64,
    }}))
    binding = SessionAIDB(workspace, "test").bind(config)["binding_id"]

    adk = tmp_path / "adk"
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", adk)
    monkeypatch.setattr(graph_logger, "ADK_DIR", adk)
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    submissions = []
    downloads = []
    points = {}
    replay = tmp_path / "vasp_std"
    replay.write_text('#!/bin/bash\nset -e\ncp "$MATCREATOR_EOS_OUTPUT/"{vasprun.xml,OUTCAR} .\n')
    replay.chmod(0o700)

    class Provider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            external_id = str(len(submissions) + 1)
            name = spec["name"]
            submissions.append(name)
            inputs = Path(spec["input_root"]) / spec["input_path"]
            if name.startswith("mixed-point-"):
                points[int(name.removeprefix("mixed-point-"))] = Poscar.from_file(
                    inputs / "POSCAR", check_for_potcar=False,
                ).structure
            if name in {"mixed-point-2", "mixed-point-9"}:
                return external_id
            if name.endswith("-relaxation"):
                source = fixture
            else:
                source = tmp_path / f"source-{external_id}"
                source.mkdir()
                static = Poscar.from_file(inputs / "POSCAR", check_for_potcar=False).structure
                kpoints = Kpoints.from_file(inputs / "KPOINTS")
                (source / "vasprun.xml").write_text(json.dumps({
                    "structure": static.as_dict(), "incar": dict(Incar.from_file(inputs / "INCAR")),
                    "kpoints": kpoints.as_dict(), "run": external_id,
                }))
                (source / "OUTCAR").write_text("Controlled SCF replay at parser boundary")
            output = tmp_path / f"provider-{external_id}"
            shutil.copytree(inputs, output)
            subprocess.run(spec["command"], cwd=output, shell=True, check=True, env={
                **os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"], "MATCREATOR_EOS_OUTPUT": str(source),
            })
            return external_id

        def status(self, external_id):
            name = submissions[int(external_id) - 1]
            return RemoteJobStatus(normalized_status="failed" if name == "mixed-point-2" else "succeeded", snapshot={})

        def collect_outputs(self, external_id, destination):
            downloads.append(external_id)
            destination.mkdir()
            if submissions[int(external_id) - 1] == "mixed-point-9":
                return []
            source = tmp_path / f"provider-{external_id}"
            return [{"source": external_id, "destination": str(shutil.copyfile(source / name, destination / name))}
                    for name in ("vasprun.xml", "OUTCAR", managed_vasp.RECEIPT)]

    def parse_outputs(folder, verify_inputs, **kwargs):
        if (folder / "vasprun.xml").read_bytes().startswith(b"<?xml"):
            return genuine_parse(folder, verify_inputs=verify_inputs, **kwargs)
        # Supply deterministic static outcomes at the parser boundary. The
        # provider staging, launcher, runtime-input check and AIDB remain real.
        staged = json.loads((folder / "vasprun.xml").read_text())
        structure = Structure.from_dict(staged["structure"])
        params = {"ISTART": 0, "ICHARG": 2, "IBRION": -1, "NSW": 0, "ISPIN": 1,
                  "ENCUT": 520, "LDAU": False, "GGA": "--", "LHFCALC": False, "LMETAGGA": False,
                  "LNONCOLLINEAR": False, "LSORBIT": False}
        parsed = SimpleNamespace(
            initial_structure=structure, final_structure=structure, incar=staged["incar"],
            potcar_symbols=["PAW_PBE Si 05Jan2001"], ionic_steps=[{}],
            kpoints=Kpoints.from_dict(staged["kpoints"]),
        )
        verify_inputs(parsed, "static", params, ["PE"])
        raw = deepcopy(historical)
        raw.update(calculation_type="static", structure=structure.as_dict(), frame_index=0,
                   energy=-44.0 + (structure.volume - relaxed.volume) ** 2 / 100,
                   forces=[[0, 0, 0]] * len(structure), stress=None, charge=None,
                   calculation_parameters=params)
        facts = raw["source_metadata"]["vasp_facts"]
        facts["sampling"] = {"type": "static", "stored_scope": "final_frame"}
        facts["initial_structure"]["structure"] = structure.as_dict()
        facts["labels"]["ionic_converged"] = False
        facts["method"]["functional_runtime"] = {"GGA": "--", "LHFCALC": False, "LMETAGGA": False}
        facts["method"]["kpoints"] = staged["kpoints"]
        facts["source"]["workflow_inputs"] = {}
        facts["source"]["file_sha256"] = facts["source"]["sha256"] = {
            name: sha256((folder / name).read_bytes()).hexdigest() for name in ("vasprun.xml", "OUTCAR")
        }
        return raw

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: Provider())
    monkeypatch.setattr(managed_vasp, "parse_vasp_result", parse_outputs)
    settings = dict(image="fixture", command="vasp_std", project_id=123, machine_type="test-cpu")
    context = SimpleNamespace(state={"session_id": "test", "workspace_dir": str(workspace), "workdir": str(workspace)},
                              _invocation_context=SimpleNamespace(user_id="owner"))
    seed = remote_job_tools.submit_bohr_batchjob(context, input_path="seed", name="seed-static", **settings)
    seeded = remote_job_tools.collect_remote_job_outputs(seed["job_id"], "seed-result", context)
    assert seeded["archive_status"] == "verified", seeded

    script = deque()

    async def model_boundary(*args, **kwargs):
        yield LlmResponse(content=types.Content(role="model", parts=[script.popleft()]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", model_boundary)

    async def run_chat():
        agent = PlanningExecutionOrchestrator(
            name="ticket11", planning_agent=thinking_agent.clone(), execution_agent=execution_agent.clone(),
        )
        sessions = InMemorySessionService()
        identity = {"app_name": "ticket11", "user_id": "owner", "session_id": "test"}
        await sessions.create_session(**identity, state={
            "session_id": "test", "workspace_dir": str(workspace), "workdir": str(workspace),
            "session_workdir_initialized": True, "agent_mode": "flash",
        })

        async def chat(runner, message, tool, args):
            script.extend([
                types.Part.from_function_call(name="run_flash_step", args={
                    "action": message, "suggested_skills": ["eos"], "label": "EOS",
                }),
                types.Part.from_function_call(name=tool, args=args),
                types.Part.from_function_call(name="submit_step_result", args={
                    "status": "success", "key_results": "EOS 作业状态已返回。", "concise_summary": "已核对 EOS 作业状态。",
                }),
                types.Part.from_text(text="已核对 EOS 作业状态。"),
                types.Part.from_text(text="已核对 EOS 作业状态。"),
            ])
            content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
            responses = []
            async for event in runner.run_async(user_id="owner", session_id="test", new_message=content):
                responses.extend(part.response for part in event.get_function_responses()
                                 if part.name == "run_flash_step")
            assert not script, list(script)
            [response] = responses
            assert "eos" in response, response
            return response

        async with Runner(app_name=identity["app_name"], agent=agent, session_service=sessions) as runner:
            submitted = await chat(
                runner, "用 VASP 算硅的状态方程。先弛豫，再算平衡体积 ±5% 内的 11 个体积点，给我 E–V 曲线。",
                "submit_eos", dict(relaxation_path="relaxation", static_template_path="template", name="mixed", **settings),
            )
            assert submissions == ["seed-static", "mixed-relaxation"]
            eos = {"eos_id": submitted["eos"]["eos_id"]}
            from matcreator.agents.execution_agent.eos_tools import collect_eos

            context.state["_vasp_human_message"] = "REMOTE JOB STATUS UPDATE from the harness.\nEOS relaxation finished"
            passive = await asyncio.to_thread(collect_eos, context, eos["eos_id"], True)
            assert passive["status"] == "ready_for_volume_points"
            assert submissions == ["seed-static", "mixed-relaxation"]
            points_started = await chat(
                runner, "继续先前授权的 EOS 体积点计算。", "collect_eos", {**eos, "advance": True},
            )
            completed = await chat(
                runner, "第 6 个体积点用现有的完全匹配记录，并汇总已完成的数据。",
                "resolve_eos_member", {**eos, "member_id": "6", "action": "select"},
            )
            repeated = await chat(runner, "重新查看同一批 EOS 结果。", "collect_eos", eos)
        return points_started, completed, repeated

    points_started, completed, repeated = asyncio.run(run_chat())
    candidate = points_started["eos"]["points"][5]["candidates"]
    [seed_record] = candidate["records"]
    assert repeated["eos"] == completed["eos"]
    eos = repeated["eos"]
    assert eos["status"] == "incomplete", eos
    assert eos["available_points"] == 9
    assert eos["missing_points"] == ["2", "9"]
    assert eos["relaxation"]["status"] == "archived"
    assert eos["points"][5]["status"] == "reused"
    assert eos["points"][5]["record_id"] == seed_record["record_id"]
    assert eos["points"][5]["result"]["run_id"] == "1"
    delivery = completed["eos"]["points"][5]["delivery"]
    assert delivery["kind"] == "aidb_export"
    assert delivery["record_id"] == seed_record["record_id"]
    assert delivery["binding_id"] == binding
    assert (delivery["user_id"], delivery["session_id"]) == ("owner", "test")
    assert "extxyz" in delivery["files"]
    assert eos["points"][8]["status"] == "archive_failed"
    assert "mixed-point-6" not in submissions
    assert len(submissions) == 12
    assert len(set(downloads)) == 11
    retried = [external_id for external_id in downloads if downloads.count(external_id) > 1]
    assert set(retried) == {eos["points"][8]["external_id"]}
    assert sorted(points) == [1, 2, 3, 4, 5, 7, 8, 9, 10, 11]
    for point_id, structure in points.items():
        assert structure.volume / relaxed.volume == pytest.approx(.94 + point_id / 100)
        np.testing.assert_allclose(structure.frac_coords, relaxed.frac_coords, atol=1e-12)
    with connect(database) as db:
        assert db.count() == 10

    manifest = workspace / f'eos-{eos["eos_id"]}' / "results"
    with (manifest / "energy-volume.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["point"] for row in rows] == ["1", "3", "4", "5", "6", "7", "8", "10", "11"]
    assert next(row for row in rows if row["point"] == "6")["energy_ev_per_cell"] == "-44.0"
    assert (manifest / "energy-volume.png").read_bytes().startswith(b"\x89PNG")
    assert json.loads((manifest / "eos-members.json").read_text()) == eos
    assert repeated["plot_path"] == str(manifest / "energy-volume.png")
    assert set(repeated["artifacts"]) == {str(manifest / name) for name in (
        "eos-members.json", "energy-volume.csv", "energy-volume.png",
    )}

    from matcreator.agents.execution_agent.eos_tools import submit_eos

    context.state["_vasp_human_message"] = "再做一组相同的 EOS 计算。"
    relaxation_hit = submit_eos(context, relaxation_path="relaxation", static_template_path="template",
                                name="reuse-relaxation", **settings)
    assert relaxation_hit["status"] == "awaiting_choice", relaxation_hit
    assert relaxation_hit["relaxation"]["candidates"]["records"][0]["record_id"] == eos["relaxation"]["record_id"]
    assert len(submissions) == 12

    store = remote_job_tools._service().store
    app = FastAPI()
    app.include_router(create_aidb_router(
        lambda user_id, session_id: (user_id, session_id) == ("owner", "test"),
        lambda session_id, user_id: workspace,
        get_completion=lambda **identity: managed_vasp.get_managed_vasp_completion(store=store, **identity),
    ))
    base = "/api/users/owner/sessions/test/aidb"
    with TestClient(app) as client:
        assert client.get(f'{base}/results/{candidate["result_id"]}').json()["eos_member"] == "6"
        selection_download = client.get(f'{base}/exports/{delivery["export_id"]}/extxyz')
        assert selection_download.status_code == 200
        assert f'record_id={seed_record["record_id"]}' in selection_download.text
        assert "energy=-44.0" in selection_download.text
        assert client.get(f'/api/users/other/sessions/test/aidb/exports/{delivery["export_id"]}/extxyz').status_code == 404
        archived = eos["points"][0]
        result = client.get(f'{base}/completions/{archived["job_id"]}').json()
        assert result["eos_member"]["member_id"] == "1"
        for endpoint, member in [(f'results/{candidate["result_id"]}', eos["points"][5]),
                                 (f'completions/{archived["job_id"]}', archived)]:
            record_id = member["record_id"]
            detail = client.get(f"{base}/records/{record_id}", params={"binding_id": binding})
            assert detail.json()["record"]["source_id"] == member["result"]["source_id"]
            exported = client.post(f"{base}/{endpoint}/records/{record_id}/export", json={"binding_id": binding})
            assert exported.status_code == 200, exported.text
            download = client.get(f'{base}/exports/{exported.json()["export_id"]}/extxyz')
            assert download.status_code == 200

    from matcreator.agents.execution_agent.eos_tools import resolve_eos_member

    SessionAIDB(workspace, "test").bind(config)
    context._invocation_context.invocation_id = "recalculate-turn"
    context.state["_vasp_human_message"] = "继续计算这次弛豫。"
    recalculated = resolve_eos_member(context, relaxation_hit["eos_id"], "relaxation", "recalculate")
    assert len(submissions) == 13
    job = store.get_job(recalculated["relaxation"]["job_id"])
    assert job["specification"]["managed_vasp"]["binding_id"] == binding
    assert recalculated["relaxation"]["status"] == "computed_archive_failed"


def test_fcc_al_uses_eleven_equally_spaced_volumes_from_completed_relaxation(tmp_path, monkeypatch):
    import os
    import subprocess

    import numpy as np
    import pytest
    from pymatgen.core import Lattice, Structure

    from matcreator.agents.execution_agent.eos_tools import collect_eos, submit_eos
    from matcreator.aidb import managed_vasp

    workspace = tmp_path / "workspace"
    original = Structure.from_spacegroup("Fm-3m", Lattice.cubic(4.2), ["Al"], [[0, 0, 0]])
    relaxed = Structure.from_spacegroup("Fm-3m", Lattice.cubic(4.0), ["Al"], [[0, 0, 0]])
    for name in ("relax", "static"):
        folder = workspace / name
        folder.mkdir(parents=True)
        Poscar(original).write_file(folder / "POSCAR")
        Incar({"IBRION": 2, "ISIF": 3, "NSW": 100, "KSPACING": .2} if name == "relax"
              else {"IBRION": -1, "NSW": 0, "KSPACING": .2}).write_file(folder / "INCAR")
        (folder / "POTCAR").write_text("Fixture header\nTITEL = PAW_PBE Al 04Jan2001\nLEXCH = PE\n")
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", tmp_path / "adk")
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    points = []
    provider_output = tmp_path / "provider-relaxation"
    fake_vasp = tmp_path / "vasp_std"
    fake_vasp.write_text("#!/bin/sh\nprintf 'Controlled fcc Al relaxation output' > vasprun.xml\n"
                         "cp vasprun.xml OUTCAR\n")
    fake_vasp.chmod(0o700)

    class Provider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            directory = Path(spec["input_root"]) / spec["input_path"]
            if spec["name"].endswith("-relaxation"):
                shutil.copytree(directory, provider_output)
                subprocess.run(["bash", managed_vasp.LAUNCHER], cwd=provider_output, check=True,
                               env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]})
                return "relaxation"
            points.append(Poscar.from_file(directory / "POSCAR", check_for_potcar=False).structure)
            return str(len(points))

        def status(self, external_id):
            return RemoteJobStatus(normalized_status="succeeded" if external_id == "relaxation" else "queued",
                                   snapshot={})

        def collect_outputs(self, external_id, destination):
            destination.mkdir()
            return [{"source": external_id,
                     "destination": str(shutil.copyfile(provider_output / name, destination / name))}
                    for name in (*managed_vasp.OUTPUTS, managed_vasp.RECEIPT)]

    def verified_result(job, evidence, files, **kwargs):
        # Inject the hypothetical fcc Al relaxation after output verification.
        return {"structure": relaxed.as_dict(), "energy": -14.0, "source_id": "fixture:al-relax",
                "frame_index": 2, "calculation_type": "relaxation", "source_metadata": {"vasp_facts": {
                    "source": {"sha256": {}}, "labels": {"energy_definition": "e_0_energy"},
                }}}, tmp_path, {}

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: Provider())
    monkeypatch.setattr(managed_vasp, "_verified_raw", verified_result)
    context = SimpleNamespace(
        state={"session_id": "test", "workdir": str(workspace), "workspace_dir": str(workspace),
               "_vasp_human_message": "用 VASP 计算 fcc Al 的状态方程，先弛豫，再算平衡体积 ±5% 范围内 11 个体积点"},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    eos = submit_eos(context, relaxation_path="relax", static_template_path="static",
                     name="fcc-al", image="fixture", command="vasp_std", project_id=123, machine_type="test")
    advanced = collect_eos(context, eos["eos_id"], advance=True)
    assert advanced["status"] == "running", advanced
    assert advanced["relaxation"]["status"] == "computed_archive_failed"
    assert [point.volume for point in points] == pytest.approx(
        [60.8, 61.44, 62.08, 62.72, 63.36, 64, 64.64, 65.28, 65.92, 66.56, 67.2],
    )
    for point in points:
        assert point.species == relaxed.species
        np.testing.assert_allclose(point.frac_coords, relaxed.frac_coords)
