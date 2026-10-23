from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from matcreator.agents import aidb_skill_hook as hook


@pytest.fixture
def local_aidb(tmp_path, monkeypatch):
    root = Path(os.environ.get('AI_READY_DB_ROOT') or Path(__file__).resolve().parents[2] / 'Ai-ready_Database').expanduser().resolve()
    python = Path(os.environ.get('AI_READY_DB_PYTHON') or root / '.venv/bin/python').expanduser().absolute()
    if not python.is_file() or not (root / 'skills/ai-ready-db/scripts/aidb_loop_adapter.py').is_file():
        message = 'aidb public bridge/runtime required; configure AI_READY_DB_ROOT and AI_READY_DB_PYTHON'
        if os.environ.get('MATCREATOR_REQUIRE_AIDB') == '1':
            pytest.fail(message)
        pytest.skip(message)
    monkeypatch.setenv('AI_READY_DB_ROOT', str(root))
    monkeypatch.setenv('AI_READY_DB_PYTHON', str(python))
    monkeypatch.setenv('AI_READY_DB_BRIDGE_SOCK', str(tmp_path / 'bridge.sock'))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'ase_sqlite': {'path': str(tmp_path / 'local.db'),
        'json_sidecar_path': str(tmp_path / 'sidecar.json')}, 'work_dir': str(tmp_path),
        'xc_functional': 'PBE', 'dft_u': False, 'system_type': 'bulk'}))
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(config))
    monkeypatch.setenv('AI_READY_DB_BRIDGE_IDLE_TIMEOUT', '1')
    yield root
    # Ask the isolated read-only bridge to exit; never touch the user's server.
    import signal
    pid_file = Path(str(tmp_path / 'bridge.sock') + '.pid')
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass


def run_task(tmp_path, request, calls, state=None, entry=None, followup=None, parallel=False, extra_tools=(), turns=(), final_state=None, trace_path=None):
    class Model(BaseLlm):
        _index: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            if parallel:
                parts = [types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls] if self._index == 0 else [types.Part(text='finished')]
                self._index += 1
                yield LlmResponse(content=types.Content(role='model', parts=parts))
                return
            if self._index < len(calls):
                name, args = calls[self._index]
                part = types.Part(function_call=types.FunctionCall(name=name, args=args))
            else:
                if followup and self._index == len(calls) + 1:
                    name, args = calls[(self._index - len(calls) - 1) % len(calls)]
                    part = types.Part(function_call=types.FunctionCall(name=name, args=args))
                else:
                    part = types.Part(text='finished')
            self._index += 1
            yield LlmResponse(content=types.Content(role='model', parts=[part]))

    prepared = []
    def load_skill(skill_name: str) -> dict:
        return {'instructions': 'VASP instructions', 'skill': skill_name}
    def run_python(code: str) -> dict:
        prepared.append(code)
        return {'status': 'prepared'}
    def submit_bohr_batchjob(name: str) -> dict:
        prepared.append('remote submission')
        return {'status': 'submitted'}

    async def run():
        nonlocal calls
        service = InMemorySessionService()
        session = await service.create_session(app_name='preflight_test', user_id='test',
            state={'workspace_dir': str(tmp_path), **(state or {})})
        agent = LlmAgent(name='task_agent', model=Model(model='test'),
            tools=[load_skill, run_python, submit_bohr_batchjob, *extra_tools],
            before_tool_callback=hook.before_aidb_skill_load,
            after_tool_callback=hook.after_aidb_skill_load)
        if entry == 'thinking':
            from matcreator.agents.thinking_agent.agent import thinking_agent
            agent = thinking_agent.model_copy(update={'name': 'thinking_test', 'model': Model(model='test'),
                'tools': [load_skill, run_python, submit_bohr_batchjob, *extra_tools], 'instruction': '',
                'before_agent_callback': None, 'sub_agents': []})
        elif entry == 'step':
            from matcreator.agents.execution_agent.step_executor import build_step_executor_agent
            from matcreator.llm_cards import LLMCard
            agent = build_step_executor_agent(LLMCard(name='test', model='openai/test')).model_copy(
                update={'model': Model(model='test'), 'tools': [load_skill, run_python, submit_bohr_batchjob, *extra_tools],
                    'instruction': '', 'input_schema': None, 'sub_agents': []})
        runner = Runner(agent=agent, app_name='preflight_test', session_service=service)
        responses = []
        human_turns = [request]
        async for event in runner.run_async(user_id='test', session_id=session.id,
            new_message=types.Content(role='user', parts=[types.Part(text=request)])):
            for part in (event.content.parts if event.content else []):
                if part.function_response:
                    responses.append(part.function_response.response)
        if followup:
            human_turns.append(followup)
            async for event in runner.run_async(user_id='test', session_id=session.id,
                new_message=types.Content(role='user', parts=[types.Part(text=followup)])):
                for part in (event.content.parts if event.content else []):
                    if part.function_response:
                        responses.append(part.function_response.response)
        for message, turn_calls in turns:
            calls = turn_calls
            agent.model._index = 0
            if callable(message):
                message = message(responses)
            human_turns.append(message)
            async for event in runner.run_async(user_id='test', session_id=session.id,
                new_message=types.Content(role='user', parts=[types.Part(text=message)])):
                for part in (event.content.parts if event.content else []):
                    if part.function_response:
                        responses.append(part.function_response.response)
        if final_state is not None:
            current = await service.get_session(app_name='preflight_test', user_id='test', session_id=session.id)
            final_state.update(current.state)
        if trace_path is not None:
            Path(trace_path).write_text(json.dumps({'human_turns': human_turns,
                'responses': responses, 'performed_tools': prepared}, ensure_ascii=False), encoding='utf-8')
        return responses
    return asyncio.run(run()), prepared


def test_natural_silicon_request_queries_before_preparation_without_goal(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '请准备金刚石结构单质硅体相的 VASP 弛豫与总能输入，普通 PBE，不提交计算',
        [('load_skill', {'skill_name': ' VASP-PYMATGEN '}), ('run_python', {'code': 'prepare'})])
    feedback = responses[0]['aidb_preflight']
    assert feedback['status'] == 'not_found'
    assert Path(feedback['report_path']).is_file()
    assert json.loads(Path(feedback['report_path']).read_text())['queries'][0]['record_ids'] == []
    assert prepared == ['prepare']

@pytest.mark.parametrize('prompt', ['请解释硅的 VASP 弛豫概念', 'What is VASP relaxation of silicon?'])
def test_concept_only_does_not_query(local_aidb, tmp_path, prompt):
    responses, _ = run_task(tmp_path, prompt, [('load_skill', {'skill_name': 'vasp-pymatgen'})])
    assert 'aidb_preflight' not in responses[0]
    assert not (tmp_path / '.aidb').exists()


def test_missing_identity_pauses_preparation_without_query(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '请用 VASP 准备弛豫和总能输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'needs_clarification'
    assert prepared == []
    assert not (tmp_path / '.aidb').exists()


def test_failure_is_not_empty_and_blocks_preparation_and_submission(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, prepared = run_task(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'}),
         ('submit_bohr_batchjob', {'name': 'Si'})])
    assert responses[0]['aidb_preflight']['status'] == 'failed'
    assert 'config does not exist' in responses[0]['aidb_preflight']['detail']
    assert prepared == []


def test_repeated_entry_reuses_one_current_report_and_preserves_unknowns(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备硅的 VASP 弛豫与总能输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('load_skill', {'skill_name': 'atomic-structure'})])
    assert responses[0]['aidb_preflight']['report_path'] == responses[1]['aidb_preflight']['report_path']
    assert len(list((tmp_path / '.aidb/runs').glob('*/*/audit.json'))) == 1
    context = responses[0]['aidb_preflight']['task_context']
    assert context['known'] == {'formula': 'Si'}
    assert context['retrieval_defaults']['functional'] == 'PBE'
    assert 'structure_model' in context['unknown']


def test_prepare_only_never_submits_even_after_success(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '只准备金刚石硅晶体 PBE 弛豫输入，不提交计算',
        [('submit_bohr_batchjob', {'name': 'Si'})])
    assert responses[0]['status'] == 'blocked'
    assert prepared == []


def test_internal_executor_uses_inherited_context_without_requiring_json_from_user(local_aidb, tmp_path):
    step = json.dumps({'action': 'Prepare VASP inputs for relaxation and total energy',
        'prior_context': 'Diamond bulk silicon, ordinary PBE', 'workspace_dir': str(tmp_path)})
    responses, prepared = run_task(tmp_path, step,
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert prepared == ['prepare']


def test_non_diamond_phase_is_visible_and_does_not_silently_prepare(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '计算 beta-tin 硅体相 PBE 总能',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'needs_clarification'
    assert prepared == []

@pytest.mark.parametrize('entry', ['thinking', 'step'])
def test_production_agents_query_at_the_registered_tool_boundary(local_aidb, tmp_path, entry):
    responses, _ = run_task(tmp_path, 'Prepare diamond bulk silicon PBE VASP relaxation and total energy inputs',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], entry=entry)
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert Path(responses[0]['aidb_preflight']['audit_path']).is_file()


def test_new_turn_queries_again_and_method_switch_invalidates_old_receipt(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备金刚石硅体相 PBE VASP 弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='改用 HSE06 重算硅晶体弛豫')
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert responses[1]['aidb_preflight']['status'] == 'needs_clarification'
    assert responses[1]['aidb_preflight']['task_context']['known']['functional'] == 'HSE06'


def test_new_independent_request_queries_instead_of_using_old_logs(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备金刚石硅体相 PBE 弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='准备金刚石硅体相 PBE 弛豫输入')
    assert responses[0]['aidb_preflight']['report_path'] != responses[1]['aidb_preflight']['report_path']


def test_concept_followup_does_not_receive_previous_receipt(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备金刚石硅体相 PBE 弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='解释硅弛豫概念')
    assert 'aidb_preflight' not in responses[1]


def test_found_returns_all_candidate_ids_and_report_without_claiming_reuse(local_aidb, tmp_path):
    import subprocess
    from ase.build import bulk
    from pymatgen.io.ase import AseAtomsAdaptor
    record = tmp_path / 'sample.json'
    record.write_text(json.dumps({'source_id': 'silicon-fixture', 'source_dataset': 'test-only',
        'formula_reduced': 'Si', 'formula_pretty': 'Si', 'composition': {'Si': 2},
        'functional': 'PBE', 'energy': -10.0,
        'structure': AseAtomsAdaptor.get_structure(bulk('Si', 'diamond', a=5.43)).as_dict()}))
    result = subprocess.run([os.environ['AI_READY_DB_PYTHON'],
        str(local_aidb / 'skills/ai-ready-db/scripts/aidb_client.py'), 'archive-local',
        '--record-json', str(record), '--namespace', 'bulk_base_pbe',
        '--origin', 'local_calculation', '--sync-status', 'local_only', '--config', os.environ['AI_READY_DB_CONFIG']],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    responses, _ = run_task(tmp_path, '准备金刚石硅体相普通 PBE 弛豫和总能输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})])
    feedback = responses[0]['aidb_preflight']
    assert feedback['status'] == 'found'
    assert feedback['candidate_ids']
    assert feedback['report']['queries'][0]['record_ids'] == feedback['candidate_ids']
    assert '尚未核验' in feedback['message']


def test_parallel_skill_entries_share_the_same_actual_query(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备金刚石硅体相 PBE VASP 弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('load_skill', {'skill_name': 'atomic-structure'})],
        parallel=True)
    assert responses[0]['aidb_preflight']['report_path'] == responses[1]['aidb_preflight']['report_path']
    assert len(list((tmp_path / '.aidb/runs').glob('*/*/audit.json'))) == 1


def test_missing_identity_remains_blocked_without_literal_vasp(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '请准备弛豫和总能输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'needs_clarification'
    assert responses[1]['status'] == 'blocked'
    assert prepared == []


@pytest.mark.parametrize('prompt', ['只生成金刚石硅晶体 PBE VASP 弛豫输入', '只准备金刚石硅晶体 PBE VASP 输入，先不要计算'])
def test_generation_only_does_not_authorize_remote_submission(local_aidb, tmp_path, prompt):
    responses, prepared = run_task(tmp_path, prompt, [('submit_bohr_batchjob', {'name': 'Si'})])
    assert responses[0]['status'] == 'blocked'
    assert prepared == []


def test_internal_submission_cannot_expand_parent_preparation_authorization(local_aidb, tmp_path):
    step = json.dumps({'action': 'Submit VASP job', 'prior_context': 'diamond bulk Si PBE'})
    responses, prepared = run_task(tmp_path, step, [('submit_bohr_batchjob', {'name': 'Si'})],
        state={'goal': '只准备金刚石硅体相 PBE 弛豫输入'})
    assert responses[0]['status'] == 'blocked'
    assert prepared == []


def test_explicit_new_material_supersedes_prior_silicon(local_aidb, tmp_path):
    responses, _ = run_task(tmp_path, '准备金刚石硅体相 PBE VASP 弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='改用金刚石碳体相 PBE 计算总能')
    feedback = responses[1]['aidb_preflight']
    assert feedback['status'] == 'needs_clarification'
    assert feedback['task_context']['known']['formula'] == 'C'
    assert len(list((tmp_path / '.aidb/runs').glob('*/*/audit.json'))) == 1


def test_direct_preparation_returns_actual_query_evidence(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '准备金刚石硅体相 PBE VASP 弛豫输入',
        [('run_python', {'code': 'prepare'})])
    assert responses[0]['status'] == 'prepared'
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert Path(responses[0]['aidb_preflight']['report_path']).is_file()
    assert prepared == ['prepare']


def test_chinese_adjacent_method_name_is_not_defaulted_to_pbe(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '请用HSE06计算金刚石硅体相总能',
        [('run_python', {'code': 'prepare'})])
    assert responses[0]['status'] == 'blocked'
    assert responses[0]['aidb_preflight']['task_context']['known']['functional'] == 'HSE06'
    assert prepared == []


def test_given_structure_supplies_identity_without_manual_json(local_aidb, tmp_path):
    from ase.build import bulk
    from ase.io import write
    write(tmp_path / 'POSCAR', bulk('Si', 'diamond', a=5.43))
    responses, prepared = run_task(tmp_path, '用现有POSCAR准备VASP弛豫和总能输入',
        [('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert responses[0]['aidb_preflight']['task_context']['known']['formula'] == 'Si'
    assert prepared == ['prepare']


def test_preflight_input_structure_is_not_a_step_output(local_aidb, tmp_path, monkeypatch):
    from ase.build import bulk
    from ase.io import write
    from matcreator.agents.session_log import collect_artifact_paths
    write(tmp_path / 'POSCAR', bulk('Si', 'diamond', a=5.43))
    entries = []
    original = hook.append_session_log_entry
    def record(context, entry):
        entries.append(entry)
        return original(context, entry)
    monkeypatch.setattr(hook, 'append_session_log_entry', record)
    responses, prepared = run_task(tmp_path, '用现有POSCAR准备VASP弛豫和总能输入',
        [('run_python', {'code': 'prepare'})], entry='step')
    assert prepared == ['prepare']
    assert str(tmp_path / 'POSCAR') not in collect_artifact_paths(responses[0])
    assert all(str(tmp_path / 'POSCAR') not in collect_artifact_paths(entry) for entry in entries)
    assert responses[0]['aidb_preflight']['task_context']['known']['input_structure_path'] == str(tmp_path / 'POSCAR')


def test_structure_read_failure_keeps_diagnostic_and_blocks_tools(local_aidb, tmp_path):
    (tmp_path / 'broken.cif').write_text('not a CIF')
    responses, prepared = run_task(tmp_path, '用broken.cif准备VASP弛豫输入',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'})])
    assert responses[0]['aidb_preflight']['status'] == 'failed'
    assert Path(responses[0]['aidb_preflight']['diagnostic_path']).is_file()
    assert prepared == []


@pytest.mark.parametrize('followup', ['继续', '继续弛豫', 'continue', '重算', '照旧', 'recalculate'])
def test_continuation_preserves_prior_prepare_only_authorization(local_aidb, tmp_path, followup):
    responses, prepared = run_task(tmp_path, '只准备金刚石硅体相 PBE VASP 弛豫输入',
        [('submit_bohr_batchjob', {'name': 'Si'})], followup=followup)
    assert responses[0]['status'] == responses[1]['status'] == 'blocked'
    assert responses[1]['aidb_preflight']['status'] == 'not_found'
    assert prepared == []


def test_explicit_new_submission_authorization_overrides_prior_prepare_only(local_aidb, tmp_path):
    responses, prepared = run_task(tmp_path, '只准备金刚石硅体相 PBE VASP 弛豫输入',
        [('submit_bohr_batchjob', {'name': 'Si'})], followup='继续并提交计算')
    assert responses[0]['status'] == 'blocked'
    assert responses[1]['status'] == 'submitted'
    assert responses[1]['aidb_preflight']['status'] == 'not_found'
    assert prepared == ['remote submission']


@pytest.mark.parametrize('action', [
    'ONLY after relaxation succeeds: prepare static VASP inputs and submit the job.',
    'Record POTCAR path and hash only, no body. Submit VASP with input_path=vasp-Si.',
    '环境设置仅本次运行有效。准备并提交金刚石硅 PBE VASP 输入。',
])
def test_authorized_step_only_qualifiers_do_not_forbid_submission(local_aidb, tmp_path, action):
    step = json.dumps({'action': action, 'prior_context': 'diamond bulk Si PBE relaxation total energy'})
    responses, prepared = run_task(tmp_path, step,
        [('submit_bohr_batchjob', {'name': 'Si'})], entry='step',
        state={'goal': '继续准备并提交金刚石硅体相 PBE 弛豫和总能重算',
               'aidb_user_request': '继续准备并提交金刚石硅体相 PBE 弛豫和总能重算',
               'aidb_user_prepare_only': False})
    assert responses[0]['status'] == 'submitted'
    assert prepared == ['remote submission']


@pytest.mark.parametrize('action', [
    'Only prepare diamond bulk Si PBE VASP inputs.',
    'Generate only diamond bulk Si PBE VASP inputs.',
    '只生成金刚石硅体相 PBE VASP 输入。',
])
def test_actual_prepare_only_step_still_blocks_submission(local_aidb, tmp_path, action):
    step = json.dumps({'action': action, 'prior_context': 'diamond bulk Si PBE'})
    responses, prepared = run_task(tmp_path, step,
        [('submit_bohr_batchjob', {'name': 'Si'})], entry='step',
        state={'goal': '计算金刚石硅体相 PBE 弛豫和总能'})
    assert responses[0]['status'] == 'blocked'
    assert prepared == []


@pytest.mark.parametrize('user_text', [
    '我只要生成金刚石硅体相普通 PBE VASP 输入文件。',
    '只需生成金刚石硅体相 PBE VASP 输入文件。',
    'Only the diamond bulk Si PBE VASP input files, please.',
    'Only a VASP input set for diamond bulk Si PBE, please.',
])
def test_user_input_only_restriction_survives_executor_submit_action(local_aidb, tmp_path, user_text):
    step = json.dumps({'action': 'Submit the VASP Batch Job for diamond bulk Si PBE relaxation.',
                       'prior_context': user_text})
    responses, prepared = run_task(tmp_path, step,
        [('submit_bohr_batchjob', {'name': 'Si'})], entry='step',
        state={'goal': user_text, 'aidb_user_request': user_text,
               'aidb_user_prepare_only': False})
    assert responses[0]['status'] == 'blocked'
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert prepared == []


@pytest.mark.parametrize('user_text', [
    'Only the diamond bulk Si PBE VASP input files, please.',
    'Only a VASP input set for diamond bulk Si PBE, please.',
])
def test_input_only_noun_request_blocks_submission_at_main_entry(local_aidb, tmp_path, user_text):
    responses, prepared = run_task(tmp_path, user_text,
        [('submit_bohr_batchjob', {'name': 'Si'})])
    assert responses[0]['status'] == 'blocked'
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert prepared == []
