"""MatPES group behavior through execution tools and an isolated provider boundary."""
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from ase.build import bulk
from ase.io import write
from pymatgen.io.vasp import Incar, Poscar
from pymatgen.io.ase import AseAtomsAdaptor

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.control_plane.providers.base import RemoteJobCapability, RemoteJobStatus


def test_batch_submits_independent_members_and_preserves_failed_source_index(tmp_path, monkeypatch):
    from matcreator.agents.execution_agent.matpes_tools import submit_matpes_batch

    workspace = tmp_path / "workspace"
    root = workspace / "staged"
    root.mkdir(parents=True)
    images = [bulk("Si", a=a) for a in (5.2, 5.3, 5.4, 5.5)]
    source = workspace / "frames.extxyz"
    write(source, images)
    manifest = root / "matpes-manifest.json"
    manifest.write_text(json.dumps({
        "schema": 1, "source_path": "../frames.extxyz", "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "frames": [{"frame_index": 3, "status": "prepared", "input_path": "frame_0003"},
                   {"frame_index": 1, "status": "skipped", "reason": "invalid structure"},
                   {"frame_index": 0, "status": "prepared", "input_path": "frame_0000"}],
    }))
    for index in (0, 3):
        folder = root / f"frame_{index:04d}"
        folder.mkdir()
        Incar({"IBRION": -1, "NSW": 0, "KSPACING": .22, "ENCUT": 600}).write_file(folder / "INCAR")
        Poscar(AseAtomsAdaptor.get_structure(images[index])).write_file(folder / "POSCAR")
        (folder / "POTCAR").write_text("Synthetic header\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    adk = tmp_path / "adk"
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", adk)
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)

    calls = []

    class BatchProvider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            inputs = Path(spec["input_root"]) / spec["input_path"]
            calls.append((inputs.name, sha256((inputs / "POSCAR").read_bytes()).hexdigest()))
            return str(len(calls))

        def status(self, external_id):
            status = "failed" if external_id == "1" else "running"
            return RemoteJobStatus(normalized_status=status, snapshot={})

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: BatchProvider())
    context = SimpleNamespace(
        state={"session_id": "test", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )

    args = dict(manifest_path="staged/matpes-manifest.json", name="label", image="fixture", command="vasp_std",
                project_id=123, machine_type="test-cpu")
    receipt = submit_matpes_batch(context, **args)
    assert receipt["status"] == "running", receipt
    assert [(row["frame_index"], row["status"]) for row in receipt["members"]] == [
        (3, "failed"), (1, "skipped"), (0, "running"),
    ]
    assert receipt["group_id"]
    assert len({row["job_id"] for row in receipt["members"] if row.get("job_id")}) == 2
    assert [item[0] for item in calls] == ["frame_0003", "frame_0000"]
    assert calls[0][1] != calls[1][1]
    assert submit_matpes_batch(context, **args) == receipt
    assert len(calls) == 2
    context.state["_vasp_human_message"] = "REMOTE JOB STATUS UPDATE from the harness.\n"
    assert submit_matpes_batch(context, **{**args, "name": "replacement"})["status"] == "error"
    assert len(calls) == 2


def test_historical_static_replay_labels_mixed_batch_and_reuses_each_job(tmp_path, monkeypatch):
    import gzip
    import os
    import shutil
    import subprocess

    import pytest
    from ase.db import connect
    from ase.io import read
    from pymatgen.io.vasp import Vasprun

    from matcreator.agents.execution_agent.matpes_tools import (
        collect_matpes_batch,
        submit_matpes_batch,
    )
    from matcreator.aidb.service import SessionAIDB

    if not (Path.home() / ".matcreator/skills/ai-ready-db/scripts/aidb_ops.py").is_file():
        pytest.skip("Install ai-ready-db for isolated AIDB replay")

    raw = (Path(__file__).resolve().parent /
           "fixtures/vasp/nacl-static")
    fixture = tmp_path / "historical-static"
    fixture.mkdir()
    for name in ("vasprun.xml", "OUTCAR"):
        (fixture / name).write_bytes(gzip.decompress((raw / f"{name}.gz").read_bytes()))
    parsed = Vasprun(fixture / "vasprun.xml", parse_dos=False, parse_eigen=False, parse_potcar_file=False)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = AseAtomsAdaptor.get_atoms(parsed.initial_structure)
    changed = original.copy()
    changed.positions[0, 0] += 0.01
    potcar_header = ("Synthetic fixture only\nTITEL = PAW_PBE Na_pv 19Sep2006\nLEXCH = PE\n"
                     "TITEL = PAW_PBE Cl 06Sep2000\nLEXCH = PE\n")

    def prepare(name, frames, versions):
        source = workspace / f"{name}.extxyz"
        write(source, frames)
        root = workspace / name
        root.mkdir()
        members = []
        for index, (atoms, version) in enumerate(zip(frames, versions, strict=True)):
            folder = root / f"frame_{index:04d}"
            folder.mkdir()
            parsed.incar.write_file(folder / "INCAR")
            Poscar(AseAtomsAdaptor.get_structure(atoms)).write_file(folder / "POSCAR")
            (folder / "POTCAR").write_text(potcar_header + version)
            members.append({"frame_index": index, "status": "prepared", "input_path": folder.name})
        manifest = root / "matpes-manifest.json"
        manifest.write_text(json.dumps({"schema": 1, "source_path": f"../{source.name}",
                                        "source_sha256": sha256(source.read_bytes()).hexdigest(), "frames": members}))
        return str(manifest.relative_to(workspace))

    seed = prepare("seed", [original], ["original"])
    mixed = prepare("mixed", [original, changed, original], ["original", "failure", "different"])
    database, sidecar, config = (tmp_path / name for name in ("archive.db", "sidecar.json", "aidb.json"))
    with connect(database) as db:
        db.count()
    sidecar.write_text('{"records": {}}')
    config.write_text(json.dumps({"database": {"backend": "ase_sqlite"}, "ase_sqlite": {
        "path": str(database), "json_sidecar_path": str(sidecar), "max_query_keys_per_row": 64,
    }}))
    session = SessionAIDB(workspace, "test")
    session.bind(config)

    monkeypatch.setattr(remote_job_tools, "ADK_DIR", tmp_path / "adk")
    monkeypatch.setattr(remote_job_tools, "record_remote_job_reference", lambda **kwargs: None)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    replay = fake_bin / "vasp_std"
    replay.write_text('#!/bin/bash\nset -e\ncp "$MATCREATOR_TEST_FIXTURE/"{vasprun.xml,OUTCAR} .\n')
    replay.chmod(0o700)
    submits, downloads = [], []

    class BatchProvider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            inputs = Path(spec["input_root"]) / spec["input_path"]
            external_id = str(len(submits) + 1)
            submits.append(spec["name"])
            if spec["name"] == "mixed-frame-1":
                return external_id
            output = tmp_path / f"provider-{external_id}"
            shutil.copytree(inputs, output)
            subprocess.run(spec["command"], cwd=output, shell=True, check=True, env={
                **os.environ, "PATH": str(fake_bin) + ":" + os.environ["PATH"],
                "MATCREATOR_TEST_FIXTURE": str(fixture),
            })
            return external_id

        def status(self, external_id):
            status = "failed" if submits[int(external_id) - 1] == "mixed-frame-1" else "succeeded"
            return RemoteJobStatus(normalized_status=status, snapshot={})

        def collect_outputs(self, external_id, destination):
            downloads.append(external_id)
            destination.mkdir()
            return [{"source": external_id, "destination": str(shutil.copyfile(
                tmp_path / f"provider-{external_id}" / name, destination / name,
            ))} for name in ("vasprun.xml", "OUTCAR", "matcreator-vasp-receipt.sha256")]

    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda *args: BatchProvider())
    context = SimpleNamespace(
        state={"session_id": "test", "workdir": str(workspace), "workspace_dir": str(workspace)},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
    )
    settings = dict(image="fixture", command="vasp_std", project_id=123, machine_type="test-cpu")
    seeded = submit_matpes_batch(context, manifest_path=seed, name="seed", **settings)
    seeded = collect_matpes_batch(context, seeded["batch_id"])
    [seed_result] = seeded["members"]
    assert seed_result["status"] == "archived", seeded

    submitted, before_choice, completed = _mixed_batch_chat(workspace, mixed, settings, monkeypatch)
    batch_id = submitted["batch_id"]
    assert [member["status"] for member in submitted["members"]] == ["waiting_choice", "failed", "succeeded"]
    assert submitted["members"][0]["candidates"]["records"][0]["record_id"] == seed_result["record_id"]
    assert [row["status"] for row in before_choice["members"]] == ["waiting_choice", "failed", "archived"]

    repeated = collect_matpes_batch(context, batch_id)
    assert completed["members"] == repeated["members"]
    assert [row["status"] for row in completed["members"]] == ["reused", "failed", "archived"]
    manifest_path, data_path = map(Path, repeated["artifact_paths"])
    manifest = json.loads(manifest_path.read_text())
    assert [(row["frame_index"], row.get("output_index")) for row in manifest["members"]] == [
        (0, 0), (1, None), (2, 1),
    ]
    records = read(data_path, index=":")
    assert [row.info["labeling_source_frame_index"] for row in records] == [0, 2]
    assert all(row.info["labeling_source_path"] == "mixed.extxyz" for row in records)
    assert [row.info["record_id"] for row in records] == [
        completed["members"][0]["record_id"], completed["members"][2]["record_id"],
    ]
    assert all(row.get_potential_energy() == parsed.final_energy for row in records)
    assert all(len(row.get_forces()) == len(original) for row in records)
    _assert_batch_result_rows(workspace, submitted, completed)
    assert submits == ["seed-frame-0", "mixed-frame-1", "mixed-frame-2"]
    assert downloads == ["1", "3"]
    with connect(database) as db:
        assert db.count() == 2


def _assert_batch_result_rows(workspace, submitted, completed):
    from io import StringIO
    from ase.io import read
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from matcreator.aidb.managed_vasp import get_managed_vasp_completion
    from web.aidb_api import create_aidb_router

    store = remote_job_tools._service().store
    app = FastAPI()
    app.include_router(create_aidb_router(
        lambda user_id, session_id: (user_id, session_id) == ("owner", "test"),
        lambda session_id, user_id: workspace,
        get_completion=lambda **identity: get_managed_vasp_completion(store=store, **identity),
    ))
    base = "/api/users/owner/sessions/test/aidb"
    reference = submitted["members"][0]["candidates"]
    binding = {"binding_id": reference["binding_id"]}
    reused = completed["members"][0]
    archived = completed["members"][2]
    with TestClient(app) as client:
        candidate = client.get(f'{base}/results/{reference["result_id"]}').json()
        assert candidate["frame_index"] == 0
        receipt = client.get(f'{base}/completions/{archived["job_id"]}').json()
        assert receipt["labeling_input"]["frame_index"] == 2
        assert receipt["records"][0]["record_id"] == archived["record_id"]
        paths = [
            f'{base}/results/{reference["result_id"]}/records/{reused["record_id"]}',
            f'{base}/completions/{archived["job_id"]}/records/{archived["record_id"]}',
        ]
        for path, member in zip(paths, (reused, archived), strict=True):
            for _ in range(2):
                detail = client.get(f'{base}/records/{member["record_id"]}', params=binding)
                assert detail.json()["record"]["record_id"] == member["record_id"]
                response = client.post(path + "/export", json=binding)
                assert response.status_code == 200, response.text
                export_id = response.json()["export_id"]
                download = client.get(f"{base}/exports/{export_id}/extxyz")
                assert download.status_code == 200
                atoms = read(StringIO(download.text), format="extxyz")
                assert atoms.info["record_id"] == member["record_id"]


def _mixed_batch_chat(workspace, manifest, settings, monkeypatch):
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
        yield LlmResponse(content=types.Content(role="model", parts=[script.popleft()]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", model_boundary)

    async def run_chat():
        sessions = InMemorySessionService()
        agent = PlanningExecutionOrchestrator(name="ticket10", planning_agent=thinking_agent.clone(),
                                              execution_agent=execution_agent.clone())
        identity = {"app_name": "ticket10", "user_id": "owner", "session_id": "test"}
        await sessions.create_session(**identity, state={
            "session_id": "test", "workdir": str(workspace), "workspace_dir": str(workspace),
            "session_workdir_initialized": True, "agent_mode": "flash",
        })

        async def chat(runner, text, tool_name, tool_args, reply):
            script.extend([
                types.Part.from_function_call(name="run_flash_step", args={
                    "action": text, "suggested_skills": [], "label": "label frames",
                }),
                types.Part.from_function_call(name=tool_name, args=tool_args),
                types.Part.from_function_call(name="submit_step_result", args={
                    "status": "success", "concise_summary": reply,
                }),
                types.Part.from_text(text=reply),
                types.Part.from_text(text=reply),
            ])
            message = types.Content(role="user", parts=[types.Part.from_text(text=text)])
            responses = []
            async for event in runner.run_async(user_id="owner", session_id="test", new_message=message):
                responses.extend(part.response for part in event.get_function_responses()
                                 if part.name == "run_flash_step")
            assert not script, list(script)
            [response] = responses
            assert "matpes_batch" in response, response
            return response["matpes_batch"]

        async with Runner(app_name=identity["app_name"], agent=agent, session_service=sessions) as runner:
            submitted = await chat(
                runner, "用 VASP 的 MatPES 静态方案给 mixed.extxyz 的前三个构型标注能量和力。",
                "submit_matpes_batch", dict(manifest_path=manifest, name="mixed", **settings),
                "源帧 0 有候选，源帧 1 失败，源帧 2 已完成计算。",
            )
            batch = {"batch_id": submitted["batch_id"]}
            collected = await chat(
                runner, "先回收这批 MatPES 已经算完的结果。",
                "collect_matpes_batch", batch, "已回收源帧 2。源帧 0 等待选择。",
            )
            completed = await chat(
                runner, "源帧 0 用已有的完全匹配结果。",
                "resolve_matpes_frame", {**batch, "frame_index": 0, "action": "select"},
                "已汇总源帧 0 和 2 的标签。源帧 1 未获得标签。",
            )
        return submitted, collected, completed

    return asyncio.run(run_chat())
