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
    root = Path(__file__).resolve().parents[2] / 'Ai-ready_Database'
    if not (root / '.venv/bin/python').exists():
        pytest.skip('adjacent aidb checkout and runtime required for public bridge integration')
    monkeypatch.setenv('AI_READY_DB_ROOT', str(root))
    monkeypatch.setenv('AI_READY_DB_PYTHON', str(root / '.venv/bin/python'))
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


def run_task(tmp_path, request, calls, state=None, entry=None, followup=None):
    class Model(BaseLlm):
        _index: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
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
        service = InMemorySessionService()
        session = await service.create_session(app_name='preflight_test', user_id='test',
            state={'workspace_dir': str(tmp_path), **(state or {})})
        agent = LlmAgent(name='task_agent', model=Model(model='test'),
            tools=[load_skill, run_python, submit_bohr_batchjob],
            before_tool_callback=hook.before_aidb_skill_load,
            after_tool_callback=hook.after_aidb_skill_load)
        if entry == 'thinking':
            from matcreator.agents.thinking_agent.agent import thinking_agent
            agent = thinking_agent.model_copy(update={'name': 'thinking_test', 'model': Model(model='test'),
                'tools': [load_skill, run_python, submit_bohr_batchjob], 'instruction': '',
                'before_agent_callback': None, 'sub_agents': []})
        elif entry == 'step':
            from matcreator.agents.execution_agent.step_executor import build_step_executor_agent
            from matcreator.llm_cards import LLMCard
            agent = build_step_executor_agent(LLMCard(name='test', model='openai/test')).model_copy(
                update={'model': Model(model='test'), 'tools': [load_skill, run_python, submit_bohr_batchjob],
                    'instruction': '', 'input_schema': None, 'sub_agents': []})
        runner = Runner(agent=agent, app_name='preflight_test', session_service=service)
        responses = []
        async for event in runner.run_async(user_id='test', session_id=session.id,
            new_message=types.Content(role='user', parts=[types.Part(text=request)])):
            for part in (event.content.parts if event.content else []):
                if part.function_response:
                    responses.append(part.function_response.response)
        if followup:
            async for event in runner.run_async(user_id='test', session_id=session.id,
                new_message=types.Content(role='user', parts=[types.Part(text=followup)])):
                for part in (event.content.parts if event.content else []):
                    if part.function_response:
                        responses.append(part.function_response.response)
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
