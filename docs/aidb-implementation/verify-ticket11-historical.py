"""Archive and plot public historical Si EOS data through a scripted ADK chat."""
import argparse
import asyncio
import csv
import json
import os
from pathlib import Path
import shutil

from ase.db import connect
from google.adk.models import LlmResponse
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from pymatgen.core import Structure

from matcreator.agents import graph_logger
from matcreator.agents.execution_agent.agent import execution_agent
from matcreator.agents.orchestrator.agent import PlanningExecutionOrchestrator
from matcreator.agents.thinking_agent.agent import thinking_agent
from matcreator.aidb.service import SessionAIDB
from matcreator.aidb.vasp_archive import file_digest

COMMIT = "38294cd74797fdab9a122260f2ce2abbcbb3fc31"
UPSTREAM = f"https://github.com/materialsproject/atomate2/tree/{COMMIT}/tests/test_data/vasp/Si_EOS_MP_GGA"
SOURCES = {
    "relax": "mp-149-PBE-EOS_MP_GGA_relax_2",
    "low": "mp-149-PBE-EOS_Static_0",
    "center": "mp-149-PBE-EOS_equilibrium_static",
    "high": "mp-149-PBE-EOS_Static_1",
}
FILENAMES = ("vasprun.xml.gz", "OUTCAR.gz")
OWNER = "ticket11"
SESSION = "eos-history"


def prepare(source, output):
    output.mkdir()
    workspace = output / "workspace"
    workspace.mkdir()
    for name in SOURCES:
        destination = workspace / name
        destination.mkdir()
        for filename in FILENAMES:
            shutil.copyfile(source / name / filename, destination / filename)
    if (source / "LICENSE.atomate2").is_file():
        shutil.copyfile(source / "LICENSE.atomate2", output / "LICENSE.atomate2")
    config = output / "config.json"
    database = output / "archive.db"
    sidecar = output / "archive-sidecar.json"
    with connect(database) as db:
        db.count()
    sidecar.write_text('{"records":{}}')
    config.write_text(json.dumps({"database": {"backend": "ase_sqlite"}, "ase_sqlite": {
        "path": str(database), "json_sidecar_path": str(sidecar), "max_query_keys_per_row": 64,
    }}))
    service = SessionAIDB(workspace, SESSION)
    service.bind(config)
    return workspace, service


async def dialogue(workspace, service, output):
    script = []
    graph_logger.ADK_DIR = output / "adk"
    os.environ["MATCLAW_WORKSPACE"] = str(workspace)

    async def scripted_model(*args, **kwargs):
        yield LlmResponse(content=types.Content(role="model", parts=[script.pop(0)]))

    LiteLlm.generate_content_async = scripted_model
    identity = {"app_name": "ticket11_historical", "user_id": OWNER, "session_id": SESSION}
    sessions = InMemorySessionService()
    await sessions.create_session(**identity, state={
        "session_id": SESSION, "workspace_dir": str(workspace), "workdir": str(workspace),
        "session_workdir_initialized": True, "agent_mode": "flash",
    })
    agent = PlanningExecutionOrchestrator(
        name=identity["app_name"], planning_agent=thinking_agent.clone(), execution_agent=execution_agent.clone(),
    )
    events = []

    async def chat(runner, message, tool, arguments, summary):
        script.extend([
            types.Part.from_function_call(name="run_flash_step", args={
                "action": message, "suggested_skills": [], "label": "Historical Si EOS",
            }),
            types.Part.from_function_call(name=tool, args=arguments),
            types.Part.from_function_call(name="submit_step_result", args={
                "status": "success", "key_results": summary, "concise_summary": summary,
            }),
            types.Part.from_text(text=summary),
            types.Part.from_text(text=summary),
        ])
        content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
        responses = []
        async for event in runner.run_async(user_id=OWNER, session_id=SESSION, new_message=content):
            events.append(event.model_dump(mode="json", exclude_none=True))
            responses.extend(part.response for part in event.get_function_responses()
                             if part.name == "run_flash_step")
        assert not script, script
        [step] = responses
        assert step["status"] == "success", step
        if tool == "show_plot":
            assert step["plot_path"] == arguments["plot_path"], step
        graph = json.loads((graph_logger.ADK_DIR / "agent_graphs" / f"{SESSION}.json").read_text())
        return latest_tool_result(graph, tool)

    async with Runner(app_name=identity["app_name"], agent=agent, session_service=sessions) as runner:
        archived = await chat(
            runner, "把工作区 relax、low、center、high 目录中的历史 VASP 结果归档到本会话 AIDB。",
            "aidb_archive_vasp", {"directories": list(SOURCES)}, "已返回四份历史输出的归档回执。",
        )
        assert archived["summary"] == {"saved": 4, "existing": 0, "failed": 0, "unverified": 0}, archived
        queried = await chat(
            runner, "读取刚才归档的历史 Si 静态 SCF 记录，用实际体积和能量绘制 E–V 曲线。",
            "aidb_query", {"conditions_json": json.dumps({"formula": "Si", "calculation_type": "static"})},
            "已读取 Si 静态 SCF 记录。",
        )
        manifest = write_artifacts(workspace, service, archived, queried)
        await chat(
            runner, "在对话里显示这张由三个历史静态点生成的 E–V 曲线。",
            "show_plot", {"plot_path": str(workspace / "energy-volume.png")}, "已显示历史 Si E–V 曲线。",
        )
    (output / "chat-events.json").write_text(json.dumps(events, ensure_ascii=False, indent=2))
    return manifest


def latest_tool_result(value, name):
    """Read the tool receipt retained in the normal execution graph."""
    results = []

    def visit(node):
        if isinstance(node, dict):
            if node.get("name") == name and "output" in node:
                results.append(node["output"])
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    assert results, name
    return results[-1]


def write_artifacts(workspace, service, archived, queried):
    snapshots = service.result(queried["result_id"])
    records = {record["record_id"]: record for record in snapshots["records"]}
    assert len(records) == 3, queried
    rows = []
    for source, receipt in zip(SOURCES, archived["items"], strict=True):
        record_id = receipt["record_id"]
        record = records[record_id] if source != "relax" else service.detail(record_id)["record"]
        assert record["calculation_type"] == ("relaxation" if source == "relax" else "static")
        facts = record["source_metadata"]["vasp_facts"]
        assert facts["labels"]["electronic_converged"] and all(facts["source"]["xml_outcar_checks"].values())
        if source == "relax":
            assert facts["labels"]["ionic_converged"]
        rows.append({
            "member": source, "calculation_type": record["calculation_type"],
            "volume_angstrom3_per_cell": Structure.from_dict(record["structure"]).volume,
            "energy_ev_per_cell": record["energy"], "energy_definition": facts["labels"]["energy_definition"],
            "functional": record["functional"], "encut_ev": facts["method"]["input_encut_ev"],
            "kpoint_mesh": facts["method"]["kpoints"]["kpoints"][0],
            "record_id": record_id, "source_id": record["source_id"], "frame_index": record["frame_index"],
            "source_url": f"{UPSTREAM}/{SOURCES[source]}/outputs", "archive_receipt": receipt["archive_receipt"],
            "decoded_sha256": facts["source"]["sha256"],
            "compressed_sha256": {filename: file_digest(workspace / source / filename) for filename in FILENAMES},
        })
    relaxation, *points = rows
    for point in points:
        point["volume_change_percent"] = 100 * (
            point["volume_angstrom3_per_cell"] / relaxation["volume_angstrom3_per_cell"] - 1
        )
    manifest = {
        "upstream_commit": COMMIT, "historical": True, "relaxation": relaxation, "points": points,
        "query": queried, "archive": archived,
    }
    (workspace / "eos-history.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    with (workspace / "energy-volume.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "member", "volume_angstrom3_per_cell", "volume_change_percent", "energy_ev_per_cell",
            "energy_definition", "functional", "encut_ev", "kpoint_mesh", "record_id", "source_id", "source_url",
        ], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(points)
    figure = Figure(figsize=(7, 4.5), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots()
    axes.plot([point["volume_angstrom3_per_cell"] for point in points],
              [point["energy_ev_per_cell"] for point in points], "o-", color="#256d85")
    axes.set(xlabel="Volume (Å³/cell)", ylabel="Energy (eV/cell)",
             title="Historical Si E–V samples")
    figure.savefig(workspace / "energy-volume.png", dpi=160)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture_directory", type=Path)
    parser.add_argument("new_evidence_directory", type=Path)
    args = parser.parse_args()
    evidence = args.new_evidence_directory.resolve()
    workspace, service = prepare(args.fixture_directory.resolve(), evidence)
    result = asyncio.run(dialogue(workspace, service, evidence))
    print(json.dumps({"manifest": str(workspace / "eos-history.json"), "points": result["points"]}))
