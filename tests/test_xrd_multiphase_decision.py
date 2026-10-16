"""Finite Agent menus must not read or leak the independent control arm."""
import asyncio
import copy
import itertools
import json
import math
from types import SimpleNamespace

import pytest

from matcreator.xrd.vendor.multiphase_decision import build_decision, legal_actions, PROTOCOL_VERSION
from matcreator.xrd.vendor.multiphase_harness import MultiphaseConfig
from matcreator.xrd.vendor import multiphase_object as harness

@pytest.fixture(autouse=True)
def isolate_scientific_harness_journal(monkeypatch):
    # These unit fixtures have no MC workspace; service tests exercise its journal.
    from matcreator.xrd.vendor import sql_history
    monkeypatch.setattr(sql_history, 'database', lambda owner: None)


def fixture(count=19, max_phases=3):
    candidates = [{'candidate_id': f'candidate-{i}', 'label': f'COD {1000+i}', 'formula': 'Ti O2',
                   'search_score': 70-i, 'matched_peaks': [{'reference': 20+i, 'observed': 20.01+i, 'delta': .01}],
                   'source_cif': {'path': 'SECRET_CIF_PATH', 'text': 'SECRET_CIF_TEXT'}} for i in range(count)]
    state = {'status': 'running', 'run_id': 'test', 'optimizer_label': 'Jev',
             'protocol_version': PROTOCOL_VERSION, 'cloud_request_count': 0, 'cloud_request_limit': min(24, sum(math.comb(count, n) for n in range(1, min(count, max_phases)+1)))*min(count, max_phases),
             'decision_requests': [], 'draft_candidate_ids': [],
             'options': {'budget': 24, 'max_phases': max_phases, 'seed': 42, 'evaluate_baseline': True},
             'pool': candidates, 'trials': [], 'incumbent': None, 'search_space_size': 1159,
             'baseline': {'status': 'pending', 'trials': [], 'incumbent': None},
             'progress': {'stage': '等待', 'completed': 0, 'total': 48}}
    payload = {'candidates': candidates, 'observed_peaks': [{'two_theta': 20, 'intensity': 100, 'prominence': 90}],
               'pattern': {'points': [[1, 55555555]]}, 'source_file': 'SECRET_SOURCE_PATH'}
    payload['_test_evidence'] = {'candidates': [{'candidate_id': c['candidate_id'], 'full_reference_peaks': [[x/10, 100/(x+1)] for x in range(400)]} for c in candidates]}
    return state, payload


def decision(state, payload, draft=None):
    return build_decision(state, payload, objective=harness.OBJECTIVE, claim_scope=harness.CLAIM_SCOPE,
                          draft_candidate_ids=draft, evidence=payload.get('_test_evidence'))


def test_only_reachability_masks_actions_and_tried_drafts_can_still_be_extended():
    state, payload = fixture(3)
    state['trials'] = [{'candidate_ids': ['candidate-0'], 'status': 'completed', 'score': 80}]
    value = decision(state, payload)
    assert set(value['choices']) == {'add:P1', 'add:P2', 'add:P3'}
    extended = decision(state, payload, ['candidate-0'])
    assert set(extended['choices']) == {'add:P2', 'add:P3'}
    fresh = decision(state, payload, ['candidate-1'])
    assert set(fresh['choices']) == {'submit', 'add:P1', 'add:P3'}
    assert value['space_size'] == 7 and value['menu_size'] == 3


def test_large_space_exposes_entire_candidate_pool_without_quality_filtering():
    state, payload = fixture()
    state['trials'] = [{'candidate_ids': [f'candidate-{i}'], 'status': 'completed', 'score': 80-i} for i in range(3)]
    state['incumbent'] = state['trials'][0]
    value = decision(state, payload)
    assert value == decision(copy.deepcopy(state), copy.deepcopy(payload))
    assert value['space_size'] == 1159 and value['remaining_space_size'] == 1156
    assert value['menu_size'] == 19
    assert value['menu_policy'] == 'all_candidates_add_or_submit_reachability_only'
    assert {c['candidate_id'] for c in value['choices'].values()} == {f'candidate-{i}' for i in range(19)}
    assert value['selection_token'] == value['decision_id']
    maximum = decision(state, payload, ['candidate-2', 'candidate-0', 'candidate-1'])
    assert maximum['auto_submit'] and not maximum['choices']
    assert maximum['candidate_ids'] == ['candidate-2', 'candidate-0', 'candidate-1']


def test_cloud_projection_cannot_leak_baseline_cif_paths_or_future_predictions():
    state, payload = fixture()
    first = decision(state, payload)
    state['baseline'] = {'status': 'completed', 'trials': [{'score': 9999999}], 'incumbent': {'secret': 'SECRET_BO'}}
    state['pywpem_review'] = {'error': 'SECRET_REVIEW_PATH'}
    state['pool'][0]['predicted_objective'] = 'SECRET_UNMEASURED'
    state['run_directory'] = 'SECRET_RUN_PATH'
    assert first == decision(state, payload)
    text = json.dumps(first)
    assert 'SECRET' not in text and 'baseline' not in json.dumps(first['state'])
    assert '55555555' not in text
    assert len(first['state']['pool'][0]['full_reference_peaks']) == 400
    assert first['state']['pool'][0]['matched_peak_positions'] == [{'observed': 20.01, 'reference': 20, 'delta_deg': .01}]
    assert 'no extra top-k' in first['state']['evidence_scope']


def test_projection_filters_nonfinite_metrics_and_path_shaped_labels():
    state, payload = fixture(3)
    payload['candidates'][0]['label'] = r'C:\private\spectrum.txt'
    state['trials'] = [{'candidate_ids': ['candidate-0'], 'status': 'failed', 'score': float('nan'),
                        'error': 'PRIVATE_ERROR', 'metrics': {'rwp_percent': float('inf'), 'file': 'PRIVATE'}}]
    value = decision(state, payload)
    assert value['state']['pool'][0]['label'] == ''
    assert 'score' not in value['state']['trials'][0]
    assert value['state']['trials'][0]['metrics'] == {}
    json.dumps(value, allow_nan=False)


def test_budget_exhaustion_and_terminal_states_offer_no_actions():
    state, payload = fixture(3)
    state['options']['budget'] = 1
    state['trials'] = [{'candidate_ids': ['candidate-0'], 'status': 'completed', 'score': 1}]
    assert decision(state, payload)['choices'] == {}
    state['trials'] = []
    for status in ('cancelled', 'failed', 'completed', 'interrupted', 'idle'):
        state['status'] = status
        assert decision(state, payload)['choices'] == {}


def prepared(tmp_path, monkeypatch):
    state, payload = fixture(3)
    state['search_space_size'] = 7
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path))
    run = tmp_path / 'oaw-test'
    run.mkdir()
    (run / 'oaw.json').write_text(json.dumps({'run_id': 'test', 'status': 'running'}), encoding='utf-8')
    (run / 'multiphase-input.json').write_text(json.dumps(payload), encoding='utf-8')
    (run / 'decision-evidence.json').write_text(json.dumps(payload['_test_evidence']), encoding='utf-8')
    harness._persist(run, state)
    monkeypatch.setitem(harness._processes, 'test', (SimpleNamespace(returncode=0), None))
    return {'run_id': 'test', 'options': state['options'], 'state': state}, run


def test_selection_token_is_audited_and_stale_token_cannot_consume_budget(tmp_path, monkeypatch):
    value, run = prepared(tmp_path, monkeypatch)
    async def reserve(value, draft):
        value = (await harness._operate(value, {'draft_candidate_ids': draft}, 'decision'))['prepared']
        return value, await harness._decision(value['state'], draft)
    value, first = asyncio.run(reserve(value, []))
    value, choice = asyncio.run(reserve(value, ['candidate-0']))
    path = [{'decision_id': first['decision_id'], 'selection_token': first['selection_token'], 'kind': 'add', 'choice': 'add:P1', 'draft_before': [], 'draft_after': ['candidate-0']},
            {'decision_id': choice['decision_id'], 'selection_token': choice['selection_token'], 'kind': 'submit', 'choice': 'submit', 'draft_before': ['candidate-0'], 'draft_after': ['candidate-0']}]
    calls = []
    async def rpc(run_id, command, **args):
        calls.append(args)
        return {'candidate_ids': args['candidate_ids'], 'status': 'completed', 'quality_score': 80}
    monkeypatch.setattr(harness, '_rpc', rpc)
    next_value = asyncio.run(harness._operate(value, {'candidate_ids': ['candidate-0'], 'reason': 'finite decision',
                              'selection_token': choice['selection_token'], 'construction_path': path}, 'evaluate'))['prepared']
    trial = next_value['state']['trials'][0]
    assert trial['optimizer_proposal']['method'] == PROTOCOL_VERSION
    assert trial['optimizer_proposal']['construction_path'] == path
    with pytest.raises(ValueError, match='stale'):
        asyncio.run(harness._operate(next_value, {'candidate_ids': ['candidate-1'], 'reason': 'old decision',
                    'selection_token': choice['selection_token'], 'construction_path': path}, 'evaluate'))
    assert len(calls) == 1
    assert len(json.loads((run / 'multiphase-state.json').read_text(encoding='utf-8'))['trials']) == 1


def test_reservations_persist_across_reads_and_enforce_fixed_cap(tmp_path, monkeypatch):
    value, run = prepared(tmp_path, monkeypatch)
    value['state']['cloud_request_limit'] = 2
    harness._persist(run, value['state'])
    for n in range(2):
        value = asyncio.run(harness._operate(value, {}, 'decision'))['prepared']
        assert harness._read(value)['state']['cloud_request_count'] == n+1
    with pytest.raises(Exception, match='上限'):
        asyncio.run(harness._operate(value, {}, 'decision'))
    assert len(json.loads((run / 'multiphase-state.json').read_text(encoding='utf-8'))['decision_requests']) == 2


def test_auto_submit_reservation_has_no_additional_cloud_cost(tmp_path, monkeypatch):
    value, _ = prepared(tmp_path, monkeypatch)
    value = asyncio.run(harness._operate(value, {'draft_candidate_ids': ['candidate-0', 'candidate-1', 'candidate-2']}, 'decision'))['prepared']
    assert value['state']['cloud_request_count'] == 0
    assert value['state']['pending_decision']['auto_submit']
    with pytest.raises(ValueError, match='unreachable'):
        asyncio.run(harness._operate(value, {'candidate_ids': ['candidate-0', 'candidate-1', 'candidate-2'], 'reason': 'forged skip',
            'selection_token': value['state']['pending_decision']['selection_token'], 'construction_path': [
                {'selection_token': value['state']['pending_decision']['selection_token'], 'kind': 'auto_submit',
                 'draft_before': ['candidate-0', 'candidate-1', 'candidate-2'], 'draft_after': ['candidate-0', 'candidate-1', 'candidate-2']}]}, 'evaluate'))


def test_reachability_mask_equals_exhaustive_unmeasured_space():
    ids = ['a', 'b', 'c', 'd']
    universe = [c for n in range(1, 4) for c in itertools.combinations(ids, n)]
    tried = universe[::2]
    remaining = [set(c) for c in universe if c not in tried]
    for draft in [()] + universe:
        actions = legal_actions(ids, list(draft), tried, 3)
        expected = {f'add:P{i+1}' for i, cid in enumerate(ids) if cid not in draft and len(draft) < 3
                    and any(set(draft) | {cid} <= c for c in remaining)}
        if draft and draft not in tried:
            expected.add('submit')
        assert set(actions) == expected


def test_start_prepares_evidence_without_spending_any_fit_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('OAW_XRD_RUN_ROOT', str(tmp_path))
    _, payload = fixture(2)
    payload.update(excluded_candidates=[], source_files_sha256={})
    async def build(root, options, progress):
        return payload
    async def create(*args, **kwargs):
        return SimpleNamespace(returncode=0)
    commands = []
    async def rpc(run_id, command, **args):
        commands.append(command)
        assert command == 'decision_evidence'
        return payload['_test_evidence']
    monkeypatch.setattr(harness, 'build_multiphase_payload', build)
    monkeypatch.setattr(harness.asyncio, 'create_subprocess_exec', create)
    monkeypatch.setattr(harness, '_rpc', rpc)
    value = {'options': {'owner_node_id': 'test-owner', 'source_match_run_id': 'source', 'candidate_ids': ['candidate-0', 'candidate-1'],
                         'budget': 6, 'max_phases': 3, 'optimizer_label': 'Jev'}, 'state': {'status': 'idle'}, 'run_id': ''}
    result = asyncio.run(harness.prepare_start(value, {'_agent_node_id': 'jev'}))['prepared']
    try:
        assert result['state']['status'] == 'running'
        assert commands == ['decision_evidence']
        assert result['state']['trials'] == []
        assert result['state']['common_initial_combinations'] == []
        assert result['state']['cloud_request_count'] == 0
        assert result['state']['cloud_request_limit'] == 6
        summary = harness.tool_summary({'value': result})
        assert summary['max_phases'] == 2 and summary['remaining'] == 3
    finally:
        asyncio.run(harness._kill(result['run_id']))


def test_history_projection_preserves_validation_residuals_and_phase_contributions():
    state, payload = fixture(3)
    state['trials'] = [{'candidate_ids': ['candidate-0'], 'status': 'completed', 'objective': .11,
                        'validation': {'metrics': {'rwp_percent': 31}}, 'metrics': {'rwp_percent': 29},
                        'residual_peaks': [{'two_theta': i, 'unexplained_intensity': 4} for i in range(20)],
                        'phase_contributions': [{'candidate_id': 'candidate-0', 'profile_area_fraction': .7,
                                                 'profile_scale': 1.2, 'cif_sha256': 'SECRET_HASH'}],
                        'fit': {'converged': False}}]
    result = decision(state, payload)['state']['trials'][0]
    assert result['validation']['metrics']['rwp_percent'] == 31
    assert result['metrics']['rwp_percent'] == 29
    assert len(result['residual_peaks']) == 20
    assert result['phase_contributions'][0]['profile_area_fraction'] == .7
    assert result['converged'] is False
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('status', ['cancelled', 'interrupted', 'failed'])
def test_terminal_projection_repairs_nested_running_without_mutating_archives(tmp_path, monkeypatch, status):
    value, run = prepared(tmp_path, monkeypatch)
    value['state'].update(status=status, pywpem_review={'status': 'running', 'live': {'iteration': 7}})
    harness._persist(run, value['state'])
    assert harness._read(value)['state']['pywpem_review']['status'] == status
    assert json.loads((run / 'multiphase-state.json').read_text(encoding='utf-8'))['pywpem_review']['status'] == 'running'


def test_stop_cancels_nested_review_and_preserves_history(tmp_path, monkeypatch):
    value, run = prepared(tmp_path, monkeypatch)
    value['state']['progress']['stage'] = 'PyWPEM 联合复核'
    value['state']['trials'] = [{'candidate_ids': ['candidate-0'], 'score': 81}]
    harness._persist(run, value['state'])
    killed = []
    async def kill(run_id):
        killed.append(run_id)
    monkeypatch.setattr(harness, '_kill', kill)
    result = asyncio.run(harness._operate(value, {}, 'stop'))['prepared']['state']
    assert killed == ['test'] and result['status'] == 'cancelled'
    assert result['pywpem_review']['status'] == 'cancelled'
    assert result['trials'][0]['score'] == 81
    assert result['stop_reason'] == 'user_stopped'


@pytest.mark.parametrize('reason,status,field,stage', [
    ('controller_failed', 'failed', 'failure_reason', '优化器失败，已停止'),
    ('controller_cancelled', 'cancelled', 'cancel_reason', '优化器已取消，计算已停止'),
])
def test_controller_stop_preserves_real_cause(tmp_path, monkeypatch, reason, status, field, stage):
    value, run = prepared(tmp_path, monkeypatch)
    value['state'].update(llm_status='running', trials=[{'candidate_ids': ['candidate-0'], 'score': 81}])
    harness._persist(run, value['state'])
    killed = []
    async def kill(run_id):
        killed.append(run_id)
    monkeypatch.setattr(harness, '_kill', kill)
    result = asyncio.run(harness._operate(value, {'reason': reason}, 'stop'))['prepared']['state']
    assert killed == ['test']
    assert result['status'] == result['llm_status'] == status
    assert result['stop_reason'] == result[field] == reason
    assert result['progress']['stage'] == stage
    assert result['trials'][0]['score'] == 81
    persisted = json.loads((run / 'multiphase-state.json').read_text(encoding='utf-8'))
    assert persisted['stop_reason'] == reason


def test_invalid_stop_reason_cannot_kill_worker(tmp_path, monkeypatch):
    value, _ = prepared(tmp_path, monkeypatch)
    async def unexpected(run_id):
        pytest.fail('Invalid reason must be rejected before stopping')
    monkeypatch.setattr(harness, '_kill', unexpected)
    with pytest.raises(Exception, match='不支持的停止原因'):
        asyncio.run(harness._operate(value, {'reason': 'arbitrary'}, 'stop'))


def test_optimizer_label_is_bounded_and_exposed_without_changing_science_options():
    assert MultiphaseConfig().optimizer_label == 'LLM Agent'
    config = MultiphaseConfig(optimizer_label='Jev', evaluate_baseline=False)
    summary = harness.tool_summary({'value': {'options': config.model_dump(), 'state': {'status': 'idle'}}})
    assert summary['evaluate_baseline'] is False
    assert summary['optimizer_label'] == 'Jev'
    with pytest.raises(ValueError):
        MultiphaseConfig(optimizer_label='x'*81)
