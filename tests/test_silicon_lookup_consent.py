"""Final B at the ADK task boundary, with real isolated public aidb access."""
import json
from pathlib import Path

import pytest

from test_aidb_skill_hook import local_aidb, run_task


REQUEST = '计算金刚石单质硅体相普通 PBE VASP 弛豫和总能'
LOAD = ('load_skill', {'skill_name': 'vasp-pymatgen'})
PREPARE = ('run_python', {'code': 'prepare'})
SUBMIT = ('submit_bohr_batchjob', {'name': 'Si'})


def confirm(responses):
    return '确认继续 ' + responses[-1]['aidb_preflight']['failure_id']


def test_confirmation_continues_original_scope_without_claiming_lookup_success(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, performed = run_task(tmp_path, REQUEST, [LOAD, PREPARE, SUBMIT],
        turns=[(confirm, [LOAD, PREPARE, SUBMIT])], trace_path=tmp_path / 'task-feedback.json')
    warning = responses[0]['aidb_preflight']
    assert '无法判断' in warning['message'] and '重复计算' in warning['message']
    assert performed == ['prepare', 'remote submission']
    continued = responses[-1]['aidb_preflight']
    assert continued['status'] == 'failed' and continued['query_executed'] is False
    assert continued['failure_id'] == warning['failure_id']
    assert continued['bypass']['status'] == 'confirmed'
    assert continued['loop_complete'] is False


@pytest.mark.parametrize('reply', ['拒绝', '不确认', '继续', 'yes', '解释一下风险', '我不确认继续'])
def test_rejection_or_no_explicit_confirmation_keeps_tools_paused(local_aidb, tmp_path, monkeypatch, reply):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, performed = run_task(tmp_path, REQUEST, [LOAD], turns=[(reply, [PREPARE, SUBMIT])])
    assert performed == []
    assert all(response['status'] == 'blocked' for response in responses[1:])


def test_confirmation_preserves_preparation_only(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, performed = run_task(tmp_path, '仅准备金刚石硅体相 PBE VASP 弛豫输入，不提交计算',
        [LOAD], turns=[(confirm, [PREPARE, SUBMIT])])
    assert performed == ['prepare']
    assert responses[-1]['status'] == 'blocked'
    assert '仅授权准备' in responses[-1]['message']


@pytest.mark.parametrize('change', ['改用金刚石碳体相 PBE 总能', '改用 HSE06 计算硅总能',
    '继续，ENCUT=600', '继续，KPOINTS=6x6x6', '继续，只计算静态总能',
    'ENCUT=600', 'KPOINTS=6x6x6', 'HSE06'])
def test_changed_target_or_conditions_invalidates_failure_confirmation(local_aidb, tmp_path, monkeypatch, change):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    token = {}
    def remember(responses):
        token['old'] = confirm(responses)
        return change
    responses, performed = run_task(tmp_path, REQUEST, [LOAD],
        turns=[(remember, [LOAD, PREPARE]), (lambda _: token['old'], [PREPARE, SUBMIT])])
    assert performed == []
    assert responses[-1]['status'] == 'blocked'


def test_condition_update_without_tools_still_invalidates_old_confirmation(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    token = {}
    def change(responses):
        token['old'] = confirm(responses)
        return 'ENCUT=600'
    responses, performed = run_task(tmp_path, REQUEST, [LOAD],
        turns=[(change, []), (lambda _: token['old'], [PREPARE, SUBMIT])])
    assert performed == []
    assert responses[-1]['aidb_preflight']['task_context']['known']['encut'] == 600


def test_old_event_token_cannot_confirm_a_new_failure(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    token = {}
    def remember(responses):
        token['old'] = confirm(responses)
        return REQUEST
    responses, performed = run_task(tmp_path, REQUEST, [LOAD],
        turns=[(remember, [LOAD]), (lambda _: token['old'], [PREPARE, SUBMIT])])
    assert responses[0]['aidb_preflight']['failure_id'] != responses[1]['aidb_preflight']['failure_id']
    assert performed == []


def test_copied_session_state_does_not_authorize_old_consent(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    state = {}
    responses, _ = run_task(tmp_path, REQUEST, [LOAD], final_state=state)
    responses, performed = run_task(tmp_path, confirm(responses), [PREPARE, SUBMIT], state=state)
    assert performed == []


def test_executor_text_cannot_supply_human_consent(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, performed = run_task(tmp_path, REQUEST, [LOAD], turns=[
        (lambda responses: json.dumps({'action': confirm(responses), 'prior_context': REQUEST}), [PREPARE, SUBMIT])])
    assert performed == []


def test_changed_supplied_structure_invalidates_confirmation(local_aidb, tmp_path, monkeypatch):
    from ase.build import bulk
    from ase.io import write
    write(tmp_path / 'POSCAR', bulk('Si', 'diamond', a=5.43))
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    def reply(responses):
        write(tmp_path / 'POSCAR', bulk('Si', 'diamond', a=5.6))
        return confirm(responses)
    responses, performed = run_task(tmp_path, REQUEST + ' 使用POSCAR', [LOAD],
        turns=[(reply, [PREPARE, SUBMIT])])
    assert performed == []
    assert responses[-1]['aidb_preflight']['failure_id'] != responses[0]['aidb_preflight']['failure_id']


def test_previously_confirmed_event_does_not_authorize_later_failure(local_aidb, tmp_path, monkeypatch):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    token = {}
    def reply(responses):
        token['old'] = confirm(responses)
        return token['old']
    responses, performed = run_task(tmp_path, REQUEST, [LOAD],
        turns=[(reply, [PREPARE, SUBMIT]), (REQUEST, [LOAD, PREPARE]),
            (lambda _: token['old'], [PREPARE, SUBMIT])])
    assert performed == ['prepare', 'remote submission']
    assert responses[-1]['status'] == 'blocked'


@pytest.mark.parametrize('entry', ['thinking', 'step'])
def test_final_b_is_registered_on_both_agent_entries(local_aidb, tmp_path, monkeypatch, entry):
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, performed = run_task(tmp_path, REQUEST, [LOAD], entry=entry,
        turns=[(confirm, [PREPARE, SUBMIT])])
    assert performed == ['prepare', 'remote submission']


@pytest.mark.parametrize('recover', [False, True])
def test_failed_lookup_bypass_still_attempts_local_archive(local_aidb, tmp_path, monkeypatch, recover):
    from test_silicon_vasp_workflow import completed_job, silicon_outputs
    from matcreator.tools.silicon_vasp import collect_silicon_vasp_result
    _, _, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    config = str(tmp_path / 'config.json')
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    collect = ('collect_silicon_vasp_result', {'job_id': job_id,
        'destination_path': 'outputs', 'calculation_type': 'relaxation'})
    def reply(responses):
        if recover:
            monkeypatch.setenv('AI_READY_DB_CONFIG', config)
        return confirm(responses)
    responses, performed = run_task(tmp_path, REQUEST, [LOAD], state={'session_id': 'session'},
        turns=[(reply, [PREPARE, collect])], extra_tools=[collect_silicon_vasp_result],
        trace_path=tmp_path / 'task-feedback.json')
    assert performed == ['prepare']
    assert responses[-1]['archived'] is recover
    assert responses[-1]['local_archive']['status'] == ('verified' if recover else 'failed')
    assert responses[-1]['loop_complete'] is False
    if recover:
        saved = json.loads(Path(responses[-1]['local_archive']['export_path']).read_text())
        assert saved['energy'] == -10.1
        assert saved['source_metadata']['sync_status'] == 'local_only'
    else:
        assert 'error' in responses[-1]['local_archive']
    assert (tmp_path / 'outputs/OUTCAR').is_file()


@pytest.mark.parametrize('prepare_only,changed', [(False, False), (True, False), (False, True)])
def test_confirmed_parent_can_delegate_only_original_scope_to_real_executor(
    local_aidb, tmp_path, monkeypatch, prepare_only, changed
):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types
    from pydantic import PrivateAttr
    from matcreator.agents.execution_agent import step_executor_runner, recovery
    from matcreator.agents.execution_agent.step_executor import build_step_executor_agent, submit_step_result
    from matcreator.agents.thinking_agent.agent import run_flash_step
    from matcreator.agents import graph_logger
    from matcreator.llm_cards import LLMCard

    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    monkeypatch.setattr(recovery, 'ADK_DIR', tmp_path)
    monkeypatch.setattr(graph_logger, 'ADK_DIR', tmp_path)
    performed = []
    def run_python(code: str) -> dict:
        performed.append('prepare')
        return {'status': 'prepared'}
    def submit_bohr_batchjob(name: str) -> dict:
        performed.append('submission')
        return {'status': 'submitted'}
    child_calls = [PREPARE, SUBMIT, ('submit_step_result', {'status': 'success',
        'key_results': 'Controlled tools completed.', 'concise_summary': 'Controlled tools completed.'})]
    class Model(BaseLlm):
        _index: int = PrivateAttr(default=0)
        async def generate_content_async(self, llm_request, stream=False):
            if self._index < len(child_calls):
                name, args = child_calls[self._index]
                part = types.Part(function_call=types.FunctionCall(name=name, args=args))
            else:
                part = types.Part(text='finished')
            self._index += 1
            yield LlmResponse(content=types.Content(role='model', parts=[part]))
    card = LLMCard(name='test', model='openai/test')
    monkeypatch.setattr(step_executor_runner, 'select_executor_llm_card', lambda **_: card)
    monkeypatch.setattr(step_executor_runner, 'build_step_executor_agent', lambda _: build_step_executor_agent(card).model_copy(
        update={'model': Model(model='test'), 'tools': [run_python, submit_bohr_batchjob, submit_step_result],
            'instruction': '', 'input_schema': None, 'sub_agents': []}))
    action = REQUEST + (' ENCUT=600' if changed else '')
    request = '仅准备' + REQUEST if prepare_only else REQUEST
    run_task(tmp_path, request, [LOAD], state={'session_id': 'controlled-child'},
        turns=[(confirm, [('run_flash_step', {'action': action, 'suggested_skills': ['vasp-pymatgen']})])],
        extra_tools=[run_flash_step])
    assert performed == ([] if changed else ['prepare'] if prepare_only else ['prepare', 'submission'])
