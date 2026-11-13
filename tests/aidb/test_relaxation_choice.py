"""Exercise the managed submission and chat choice seams for prepared VASP relaxation."""
import json
from hashlib import sha256
from types import SimpleNamespace

import pytest

from pymatgen.core import Lattice, Structure
from pymatgen.io.vasp import Incar, Kpoints, Poscar

from matcreator.agents.execution_agent import remote_job_tools
from matcreator.aidb.service import SessionAIDB
from matcreator.control_plane.providers.base import RemoteJobCapability


@pytest.fixture
def relaxation(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    inputs = workspace / "si"
    inputs.mkdir(parents=True)
    Incar({"IBRION": 2, "NSW": 99, "ISIF": 3, "ENCUT": 520}).write_file(inputs / "INCAR")
    Poscar(Structure(Lattice.cubic(3), ["Si"], [[0, 0, 0]])).write_file(inputs / "POSCAR")
    Kpoints.gamma_automatic((4, 4, 4)).write_file(inputs / "KPOINTS")
    (inputs / "POTCAR").write_text("Test boundary only\nTITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    config = tmp_path / "aidb.json"
    config.write_text("{}")
    SessionAIDB(workspace, "test-chat").bind(config)
    operations = []
    records = []
    retained_outputs = []

    def installed_query(*args, **kwargs):
        def query(config, workdir, conditions):
            operations.append(("query", conditions))
            matches = [r for r in records if conditions.get('record_id', r['record_id']) == r['record_id']]
            return json.loads(json.dumps(matches)), {'complete': True, 'conditions': conditions, 'match_count': len(matches)}
        return query

    class BatchProvider:
        capabilities = {RemoteJobCapability.BATCH_COLLECT}

        def create(self, spec):
            operations.append(("submit", spec["input_path"]))
            retained_outputs[:] = spec["out_files"]
            return "isolated-job"

        def status(self, external_id):
            return None

    monkeypatch.setattr("matcreator.aidb.service.InstalledSkillQuery", installed_query)
    monkeypatch.setattr(remote_job_tools.RemoteJobService, "_adapter", lambda self, provider: BatchProvider())
    monkeypatch.setattr(remote_job_tools, "ADK_DIR", tmp_path / "adk")
    monkeypatch.setattr("matcreator.agents.execution_agent.recovery.ADK_DIR", tmp_path / "adk")
    context = SimpleNamespace(
        state={"session_id": "test-chat", "workdir": str(workspace), "workspace_dir": str(workspace),
               "_test_retained_outputs": retained_outputs, "_graph_exec_node_id": "execution_1__node_relax", "step_number": 1},
        _invocation_context=SimpleNamespace(user_id="owner", invocation_id="turn-1"),
        user_content=SimpleNamespace(role="user", parts=[SimpleNamespace(text="Use prepared VASP inputs.")]),
    )

    def submit(): return remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp", command="mpirun -np 4 vasp_std",
        machine_type="test-cpu", input_path="si", out_files=None,
    )

    return workspace, inputs, context, operations, records, submit


def test_prepared_relaxation_checks_the_database_then_uses_existing_submit_path(relaxation):
    _, _, _, operations, _, submit = relaxation
    receipt = submit()
    assert receipt["status"] == "queued"
    assert "aidb" not in str(receipt).lower()
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}), ("submit", "si")]

def matching_record(inputs):
    structure = Poscar.from_file(inputs / "POSCAR", check_for_potcar=False).structure.as_dict()
    hashes = {path.name: sha256(path.read_bytes()).hexdigest() for path in inputs.iterdir()}
    run_fingerprint = "1" * 64
    return {
        "record_id": "si-one", "source_id": "known-run", "frame_index": 2,
        "formula_reduced": "Si", "calculation_type": "relaxation", "functional": "PBE", "structure": structure,
        "energy": -5.42, "forces": [[0, 0, 0]],
        "calculation_parameters": {"IBRION": 2, "NSW": 99, "ISIF": 3, "ENMAX": 520, "ISTART": 0,
                                   "ICHARG": 2, "ISPIN": 1, "LSORBIT": False, "LNONCOLLINEAR": False,
                                   "GGA": "--", "LMETAGGA": False, "LHFCALC": False, "LDAU": False},
        "source_metadata": {"software": "VASP", "logical_namespace": "fine_tune_working", "vasp_facts": {
            "initial_structure": {"structure": structure},
            "sampling": {"type": "relaxation", "stored_scope": "final_frame"},
            "labels": {"electronic_converged": True, "ionic_converged": True,
                       "energy_definition": "e_0_energy", "energy_unit": "eV/cell", "force_unit": "eV/Angstrom"},
            "method": {"code": "VASP", "input_encut_ev": 520, "potcar_symbols": ["PAW_PBE Si 05Jan2001"],
                       "functional_input": {},
                       "functional_runtime": {"GGA": "--", "LMETAGGA": False, "LHFCALC": False},
                       "spin": {"ISPIN": 1, "LSORBIT": False, "LNONCOLLINEAR": False},
                       "kpoints": Kpoints.gamma_automatic((4, 4, 4)).as_dict()},
            "source": {"run_fingerprint": run_fingerprint, "xml_outcar_checks": {"final energy": True},
                       "submitted_inputs": {"provider": "bohr_batchjob", "run_id": "known-run",
                                            "run_fingerprint": run_fingerprint,
                                            "executable": "vasp_std", "sha256": hashes}},
        }},
    }


@pytest.mark.parametrize(("message", "out_files", "required_file"), [
    ("Use prepared VASP inputs.", ["stdout.log"], "stdout.log"),
    ("Give me the calculation log.", None, "log"),
    ("请给我原始 CONTCAR。", None, "CONTCAR"),
    ("Use prepared VASP inputs.", ["POSCAR"], "POSCAR"),
    ("Give me the original POSCAR.", None, "POSCAR"),
])
def test_reuse_requires_the_requested_original_artifacts(relaxation, message, out_files, required_file):
    _, inputs, context, operations, records, _ = relaxation
    records.append(matching_record(inputs))
    context.user_content.parts[0].text = message
    receipt = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp", command="mpirun -np 4 vasp_std",
        machine_type="test-cpu", input_path="si", out_files=out_files,
    )
    assert receipt["status"] == "queued", receipt
    assert operations[-1] == ("submit", "si")
    assert required_file in context.state["_test_retained_outputs"]


def test_complete_matches_pause_real_submit_and_publish_in_record_order(relaxation):
    workspace, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    receipt = submit()

    assert receipt["status"] == "waiting_choice"
    assert receipt["candidates"]["kind"] == "aidb_result"
    result = SessionAIDB(workspace, "test-chat").result(receipt["candidates"]["result_id"])
    assert [row["record_id"] for row in result["records"]] == ["si-one"]
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"})]

    assert submit()["choice_id"] == receipt["choice_id"]
    assert len(operations) == 1

def test_choice_uses_fresh_read_and_delivers_once(relaxation):
    import asyncio
    from io import StringIO
    from ase.io import read
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    workspace, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    assert choice["status"] == "waiting_choice"
    context._invocation_context.invocation_id = "turn-2"
    records[0]["energy"] = -5.5

    result = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))

    assert result["status"] == "reused"
    assert result["energy_ev_per_cell"] == -5.5
    service = SessionAIDB(workspace, "test-chat")
    export = result["delivery"]
    atoms = read(StringIO(service.export_file(export["export_id"], "extxyz")["content"]), format="extxyz")
    assert atoms.info["record_id"] == "si-one"
    assert atoms.get_potential_energy() == -5.5
    assert service.export_file(export["export_id"], "poscar")["filename"] == "POSCAR-si-one"
    assert asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))["status"] == "closed"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}),
                          ("query", {"record_id": "si-one"})]

def test_continue_uses_the_same_prepared_batch_once(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    assert asyncio.run(vasp_relaxation_choice("continue", choice["choice_id"], context))["status"] == "waiting_choice"
    context._invocation_context.invocation_id = "turn-2"
    assert asyncio.run(vasp_relaxation_choice("continue", choice["choice_id"], context))["status"] == "ready_to_continue"
    assert submit()["batchjob_id"] == "isolated-job"
    assert submit()["batchjob_id"] == "isolated-job"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}), ("submit", "si")]


def test_changed_inputs_after_continue_cannot_submit_the_old_choice(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    context._invocation_context.invocation_id = "turn-2"
    assert asyncio.run(vasp_relaxation_choice("continue", choice["choice_id"], context))["status"] == "ready_to_continue"
    (inputs / "INCAR").write_text((inputs / "INCAR").read_text().replace("520", "600"))

    assert submit()["status"] == "error"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"})]
    assert asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))["status"] == "stale_input"
    assert submit()["status"] == "queued"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}),
                          ("query", {"formula": "Si", "calculation_type": "relaxation"}), ("submit", "si")]


def test_details_and_ambiguity_leave_order_and_choice_intact(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    first = matching_record(inputs)
    second = {**matching_record(inputs), "record_id": "si-two"}
    second_source = second["source_metadata"]["vasp_facts"]["source"]
    second_source["run_fingerprint"] = "2" * 64
    second_source["submitted_inputs"].update(run_fingerprint="2" * 64, run_id="independent")
    records.extend([first, second])
    choice = submit()
    context._invocation_context.invocation_id = "turn-2"
    assert asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context))["status"] == "clarify"
    assert asyncio.run(vasp_relaxation_choice("detail", choice["choice_id"], context, ordinal=2))[
        "detail"]["expanded_record_id"] == "si-two"
    records.pop()
    assert asyncio.run(vasp_relaxation_choice("detail", choice["choice_id"], context, ordinal=2))["status"] == "not_found"
    assert submit()["choice_id"] == choice["choice_id"]
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}),
                          ("query", {"record_id": "si-two"}), ("query", {"record_id": "si-two"})]


def test_cancelled_or_revised_choice_cannot_be_reused(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    context._invocation_context.invocation_id = "turn-2"
    assert asyncio.run(vasp_relaxation_choice("cancel", choice["choice_id"], context))["status"] == "cancelled"
    context._invocation_context.invocation_id = "turn-3"
    assert asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))["status"] == "closed"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"})]


@pytest.mark.parametrize("issue", ["rejected", "wrong_cutoff", "wrong_start", "wrong_potential", "wrong_mesh",
                                    "no_force", "no_provenance", "conflicting_evidence"])
def test_wrong_or_unverifiable_record_is_silent(relaxation, issue):
    _, inputs, _, operations, records, submit = relaxation
    record = matching_record(inputs)
    metadata = record["source_metadata"]
    facts = metadata["vasp_facts"]
    if issue == "rejected":
        metadata["logical_namespace"] = "rejected_records"
    elif issue == "wrong_cutoff":
        record["calculation_parameters"]["ENMAX"] = 600
    elif issue == "wrong_start":
        facts["initial_structure"]["structure"]["sites"][0]["abc"] = [0.1, 0.2, 0.3]
    elif issue == "wrong_potential":
        facts["method"]["potcar_symbols"] = ["PAW_PBE Si 01Jan2026"]
    elif issue == "wrong_mesh":
        facts["method"]["kpoints"]["kpoints"] = [[6, 6, 6]]
    elif issue == "no_force":
        record["forces"] = None
    elif issue == "no_provenance":
        facts["source"].pop("submitted_inputs")
    else:
        facts["source"]["xml_outcar_checks"]["final energy"] = False
    records.append(record)

    result = submit()

    assert result["status"] == "queued"
    assert "aidb" not in str(result).lower()
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}), ("submit", "si")]


@pytest.mark.parametrize("fault", ["failed", "timeout", "incomplete"])
def test_database_faults_are_logged_separately_and_do_not_block_compute(relaxation, monkeypatch, caplog, fault):
    from matcreator.aidb.service import AIDBError
    import logging

    _, _, _, operations, _, submit = relaxation

    def installed_query(*args, **kwargs):
        def query(config, workdir, conditions):
            raise AIDBError("isolated failure", fault)
        return query

    monkeypatch.setattr("matcreator.aidb.service.InstalledSkillQuery", installed_query)
    with caplog.at_level(logging.INFO, logger="matcreator.aidb.relaxation"):
        receipt = submit()

    assert receipt["status"] == "queued"
    assert "aidb" not in str(receipt).lower()
    assert operations == [("submit", "si")]
    assert f"outcome={fault}" in caplog.text
    assert "no_match" not in caplog.text

def test_same_turn_model_export_reuses_automatic_delivery_receipt(relaxation):
    import asyncio
    from matcreator.tools.aidb_tools import aidb_export
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    context._invocation_context.invocation_id = "turn-2"
    result = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))
    assert result["status"] == "reused"

    repeat = asyncio.run(aidb_export("si-one", ["extxyz"], context))

    assert repeat["export_id"] == result["delivery"]["export_id"]
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}),
                          ("query", {"record_id": "si-one"})]


def test_same_turn_provider_retry_cannot_create_a_job_under_a_new_name(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    context._invocation_context.invocation_id = "turn-2"
    assert asyncio.run(vasp_relaxation_choice("continue", choice["choice_id"], context))["status"] == "ready_to_continue"
    expected = submit()

    retry = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="renamed", image="test-vasp", command="mpirun -np 4 vasp_std",
        machine_type="test-cpu", input_path="si", out_files=None,
    )

    assert retry["job_id"] == expected["job_id"]
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"}), ("submit", "si")]

def test_adapter_refuses_actual_submit_if_the_authorized_revision_changes_in_dry_run(tmp_path, monkeypatch):
    from matcreator.aidb.relaxation import read_spec_inputs
    from matcreator.control_plane.providers import bohr_batchjob

    inputs = tmp_path / "relax"
    inputs.mkdir()
    Incar({"IBRION": 2, "NSW": 3, "ENCUT": 520}).write_file(inputs / "INCAR")
    Poscar(Structure(Lattice.cubic(3), ["Si"], [[0, 0, 0]])).write_file(inputs / "POSCAR")
    Kpoints.gamma_automatic((4, 4, 4)).write_file(inputs / "KPOINTS")
    (inputs / "POTCAR").write_text("TITEL = PAW_PBE Si 05Jan2001\nLEXCH = PE\n")
    spec = {"name": "relax", "image": "test", "project_id": 123, "machine_type": "cpu",
            "command": "mpirun -np 4 vasp_std", "input_root": str(tmp_path), "input_path": "relax"}
    spec["_expected_vasp_inputs"] = read_spec_inputs(spec).revision
    calls = []

    def cli(args, cwd=None):
        calls.append(args)
        if "--dry-run" in args:
            (inputs / "INCAR").write_text((inputs / "INCAR").read_text().replace("520", "600"))
        return {"jobId": "test"}

    monkeypatch.setattr(bohr_batchjob, "run_bohr_json", cli)
    with pytest.raises(ValueError, match="VASP inputs changed"):
        bohr_batchjob.BohrBatchJobAdapter().create(spec)

    assert len(calls) == 1
    assert "--dry-run" in calls[0]

def test_automatic_remote_notification_cannot_choose_a_candidate(relaxation):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    context._invocation_context.invocation_id = "not-a-human-turn"
    context.user_content.parts[0].text = "REMOTE JOB STATUS UPDATE from the harness.\nJob succeeded."
    receipt = asyncio.run(vasp_relaxation_choice("select", choice["choice_id"], context, ordinal=1))

    assert receipt["status"] == "waiting_choice"
    assert operations == [("query", {"formula": "Si", "calculation_type": "relaxation"})]


def test_changed_input_on_empty_database_is_a_distinct_job(relaxation):
    _, inputs, context, operations, _, submit = relaxation

    original = submit()
    context._invocation_context.invocation_id = "turn-2"
    (inputs / "INCAR").write_text((inputs / "INCAR").read_text().replace("520", "600"))
    revised = submit()
    assert revised["job_id"] != original["job_id"]
    assert [name for name, _ in operations] == ["query", "submit", "query", "submit"]


def test_candidate_cannot_deliver_requested_raw_vasp_output(relaxation):
    _, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    context.state["_vasp_requested_output_text"] = "Give me vasprun.xml after the relaxation."

    assert submit()["status"] == "queued"
    assert [name for name, _ in operations] == ["query", "submit"]

def test_managed_adk_turn_cancels_proven_choice_and_rejects_later_ordinal(relaxation, monkeypatch):
    import asyncio

    from google.adk.models import LlmResponse
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types
    from matcreator.agents.execution_agent.agent import execution_agent
    from matcreator.agents.orchestrator.agent import PlanningExecutionOrchestrator
    from matcreator.agents.thinking_agent.agent import thinking_agent

    workspace, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    assert choice["status"] == "waiting_choice"
    scripted = iter([
        types.Part.from_function_call(name="vasp_relaxation_choice", args={
            "action": "cancel", "choice_id": choice["choice_id"],
        }),
        types.Part.from_text(text="已取消。布里渊区是倒空间中的原胞。"),
        types.Part.from_function_call(name="run_flash_step", args={
            "action": "Find a new candidate for the old first row", "suggested_skills": [], "label": "relax",
        }),
        types.Part.from_function_call(name="submit_bohr_batchjob", args={
            "name": "relax", "project_id": 123, "image": "test-vasp", "command": "mpirun -np 4 vasp_std",
            "machine_type": "test-cpu", "input_path": "si", "out_files": None,
        }),
        types.Part.from_text(text="上一次的弛豫候选已关闭。"),
        types.Part.from_text(text="配色方案一是深蓝色，方案二是灰色。"),
        types.Part.from_text(text="已选深蓝色。"),
        types.Part.from_function_call(name="run_flash_step", args={
            "action": "Check the prepared VASP inputs for a new relaxation.", "suggested_skills": [], "label": "relax",
        }),
        types.Part.from_function_call(name="submit_bohr_batchjob", args={
            "name": "relax", "project_id": 123, "image": "test-vasp", "command": "mpirun -np 4 vasp_std",
            "machine_type": "test-cpu", "input_path": "si", "out_files": None,
        }),
        types.Part.from_text(text="已打开新的弛豫候选。"),
    ])

    async def model_boundary(*args, **kwargs):
        yield LlmResponse(content=types.Content(role="model", parts=[next(scripted)]))

    monkeypatch.setattr(LiteLlm, "generate_content_async", model_boundary)

    async def chat():
        session_service = InMemorySessionService()
        orchestrator = PlanningExecutionOrchestrator(
            name="ticket05", planning_agent=thinking_agent.clone(),
            execution_agent=execution_agent.clone(),
        )
        runner = Runner(app_name="ticket05", agent=orchestrator, session_service=session_service)
        await session_service.create_session(
            app_name="ticket05", user_id="owner", session_id="test-chat",
            state={"session_id": "test-chat", "session_workdir_initialized": True,
                   "workdir": str(workspace), "agent_mode": "flash"},
        )
        turns = []
        async with runner:
            for message in (
                "这次 VASP 先不跑了，告诉我什么是布里渊区。",
                "用第一条。",
                "这个报告的配色请给我两个选项。",
                "用第一条。",
                "请重新用工作区的输入做一次 Si 的 VASP 弛豫。",
            ):
                content = types.Content(role="user", parts=[types.Part.from_text(text=message)])
                replies = []
                async for event in runner.run_async(user_id="owner", session_id="test-chat", new_message=content):
                    for response in event.get_function_responses():
                        if response.name in {"vasp_relaxation_choice", "run_flash_step"}:
                            replies.append(response.response["status"])
                    if event.content and not event.get_function_calls() and not event.get_function_responses():
                        replies.extend(part.text for part in event.content.parts or [] if part.text)
                turns.append((replies, list(operations)))
        return turns

    (cancellation, cancel_ops), (stale, stale_ops), _, (unrelated, unrelated_ops), (fresh, fresh_ops) = asyncio.run(chat())
    query = ("query", {"formula": "Si", "calculation_type": "relaxation"})
    assert "cancelled" in cancellation
    assert cancel_ops == stale_ops == unrelated_ops == [query]
    assert stale == ["closed", "上一次的弛豫候选已关闭。"]
    assert unrelated == ["已选深蓝色。"]
    assert "waiting_choice" in fresh
    assert fresh_ops == [query, query]


def test_ancillary_readme_keeps_batch_relaxation_managed(relaxation):
    _, inputs, _, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    (inputs / "README.md").write_text("Local input notes.")
    assert submit()["status"] == "waiting_choice"
    assert [operation for operation, _ in operations] == ["query"]


def test_unrecognized_vasp_command_never_falls_back_to_generic_batch(relaxation):
    _, _, context, operations, _, _ = relaxation
    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp",
        command="srun vasp_std", machine_type="test-cpu", input_path="si",
    )
    assert result["status"] == "error"
    assert operations == []


def test_direct_grouped_vasp_batch_also_checks_candidates(relaxation):
    _, inputs, context, operations, records, _ = relaxation
    records.append(matching_record(inputs))
    result = remote_job_tools.submit_bohr_batchjob(
        context, project_id=123, name="relax", image="test-vasp",
        command="mpirun -np 4 vasp_std", machine_type="test-cpu",
        input_path="si", group_id="group-without-any-submissions-yet",
    )
    assert result["status"] == "waiting_choice"
    assert [operation for operation, _ in operations] == ["query"]


def test_continue_after_rebinding_does_not_redirect_archive(relaxation, tmp_path):
    import asyncio
    from matcreator.tools.relaxation_tools import vasp_relaxation_choice

    workspace, inputs, context, operations, records, submit = relaxation
    records.append(matching_record(inputs))
    choice = submit()
    original = choice["candidates"]["binding_id"]
    replacement = tmp_path / "replacement.json"
    replacement.write_text("{}")
    assert SessionAIDB(workspace, "test-chat").bind(replacement)["binding_id"] != original
    context._invocation_context.invocation_id = "turn-2"
    decision = asyncio.run(vasp_relaxation_choice("continue", choice["choice_id"], context))
    assert decision["status"] == "ready_to_continue", decision
    result = submit()
    job = remote_job_tools._service().store.get_job(result["job_id"])
    assert job["specification"]["managed_vasp"]["binding_id"] == original
    assert [operation for operation, _ in operations] == ["query", "submit"]
