"""Task-level reuse tests through ADK and the public isolated local database."""
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from test_aidb_skill_hook import local_aidb, run_task
from test_silicon_vasp_workflow import completed_job, silicon_outputs
from matcreator.tools.silicon_archive import archive_silicon_vasp_result


def seed_relaxation(tmp_path, monkeypatch):
    _, context, job = completed_job(tmp_path, monkeypatch, silicon_outputs())
    result = archive_silicon_vasp_result(job, 'outputs', 'relaxation', context)
    assert result['archived'], result
    return json.loads(Path(result['local_archive']['export_path']).read_text())


def test_compatible_success_reused_before_preparation_or_submission(local_aidb, tmp_path, monkeypatch):
    saved = seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = run_task(tmp_path, '请计算金刚石硅体相普通 PBE 弛豫和总能',
        [('load_skill', {'skill_name': 'vasp-pymatgen'}), ('run_python', {'code': 'prepare'}),
         ('submit_bohr_batchjob', {'name': 'Si'})])
    feedback = responses[0]['aidb_preflight']
    assert feedback['reuse']['status'] == 'reused'
    assert feedback['reuse']['record_ids'] == [saved['record_id']]
    assert feedback['reuse']['total_energy'] == {'value': -10.1, 'unit': 'eV', 'basis': 'cell'}
    assert feedback['reuse']['structure'] == saved['structure']
    assert prepared == []

def derive_candidate(record, source_id):
    candidate = copy.deepcopy(record)
    candidate.pop('record_id')
    candidate['source_id'] = source_id
    candidate['source_metadata']['job_id'] = source_id
    return candidate


def public_archive(local_aidb, tmp_path, record):
    path = tmp_path / 'candidate.json'
    path.write_text(json.dumps(record))
    proc = subprocess.run([os.environ['AI_READY_DB_PYTHON'],
        str(local_aidb / 'skills/ai-ready-db/scripts/aidb_client.py'), 'archive-local',
        '--record-json', str(path), '--namespace', 'bulk_base_pbe', '--origin', 'local_calculation',
        '--sync-status', 'local_only', '--config', os.environ['AI_READY_DB_CONFIG']], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def query(tmp_path, request='请计算金刚石硅体相普通 PBE 弛豫和总能', **kwargs):
    return run_task(tmp_path, request, [('load_skill', {'skill_name': 'vasp-pymatgen'}),
        ('submit_bohr_batchjob', {'name': 'Si'})], **kwargs)


def test_explicit_recalculation_queries_first_and_preserves_authorization(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, '请重新计算并提交金刚石硅体相 PBE 弛豫和总能')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'recalculate'
    assert Path(responses[0]['aidb_preflight']['report_path']).is_file()
    assert prepared == ['remote submission']
    responses, prepared = query(tmp_path, '只准备金刚石硅体相 PBE 弛豫重算输入，不提交')
    assert prepared == []


def test_recent_archive_selected_without_claiming_accuracy(local_aidb, tmp_path, monkeypatch):
    first = seed_relaxation(tmp_path, monkeypatch)
    second = derive_candidate(first, 'later-job')
    second['source_metadata']['archived_at'] = '2026-10-08T00:00:00+00:00'
    second['energy'] = -10.3
    public_archive(local_aidb, tmp_path, second)
    responses, prepared = query(tmp_path)
    reuse = responses[0]['aidb_preflight']['reuse']
    assert reuse['status'] == 'reused'
    assert reuse['total_energy']['value'] == -10.3
    assert '不代表精度最高' in reuse['reason']
    assert prepared == []


def test_missing_archive_order_requires_clarification(local_aidb, tmp_path, monkeypatch):
    first = seed_relaxation(tmp_path, monkeypatch)
    second = derive_candidate(first, 'undated-job')
    second['source_metadata'].pop('archived_at')
    public_archive(local_aidb, tmp_path, second)
    responses, prepared = query(tmp_path)
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'needs_clarification'
    assert prepared == []


def test_different_conditions_require_scientific_choice(local_aidb, tmp_path, monkeypatch):
    first = seed_relaxation(tmp_path, monkeypatch)
    second = derive_candidate(first, 'other-cutoff')
    second['source_metadata']['conditions']['incar']['ENCUT'] = 600
    second['calculation_parameters']['ENCUT'] = 600
    public_archive(local_aidb, tmp_path, second)
    responses, prepared = query(tmp_path)
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'needs_clarification'
    assert prepared == []
    responses, prepared = query(tmp_path, '计算金刚石硅体相 PBE ENCUT=520 弛豫和总能')
    assert responses[0]['aidb_preflight']['reuse']['record_ids'] == [first['record_id']]
    assert prepared == []


def test_linked_static_and_relaxation_keep_distinct_energies(local_aidb, tmp_path, monkeypatch):
    parent = seed_relaxation(tmp_path, monkeypatch)
    child = derive_candidate(parent, 'static-job')
    child['calculation_type'] = 'static'
    child['energy'] = -10.2
    meta = child['source_metadata']
    meta['parent_record_id'] = parent['record_id']
    meta['relaxation_source'] = {'job_id': parent['source_id']}
    meta['completion']['ionic_converged'] = None
    meta['conditions']['incar']['NSW'] = 0
    meta['conditions']['incar']['IBRION'] = -1
    child['calculation_parameters'] = meta['conditions']['incar']
    public_archive(local_aidb, tmp_path, child)
    responses, prepared = query(tmp_path)
    reuse = responses[0]['aidb_preflight']['reuse']
    assert reuse['status'] == 'reused' and len(reuse['record_ids']) == 2
    assert [r['total_energy']['value'] for r in reuse['results']] == [-10.1, -10.2]
    assert prepared == []
    meta['parent_record_id'] = 'unrelated'
    public_archive(local_aidb, tmp_path, child)
    responses, prepared = query(tmp_path)
    assert responses[0]['aidb_preflight']['reuse']['record_ids'] == [parent['record_id']]
    assert prepared == []


def test_target_change_does_not_reuse_previous_selection(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = run_task(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='改用 HSE06 计算金刚石硅体相总能')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'reused'
    assert responses[1]['aidb_preflight']['status'] == 'needs_clarification'
    assert not responses[1]['aidb_preflight'].get('reuse')
    assert prepared == []


def test_negative_recompute_intent_still_reuses(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能，不重新计算')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'reused'
    assert prepared == []
@pytest.mark.parametrize('defect', ['electronic', 'ionic', 'unit', 'energy', 'hybrid', 'potential', 'kpoints', 'phase', 'failed'])
def test_incomplete_or_incompatible_records_remain_candidates(local_aidb, tmp_path, monkeypatch, defect):
    saved = seed_relaxation(tmp_path, monkeypatch)
    meta = saved['source_metadata']
    if defect == 'electronic':
        meta['completion']['electronic_converged'] = False
    elif defect == 'ionic':
        meta['completion']['ionic_converged'] = None
    elif defect == 'unit':
        meta['energy_unit'] = 'Ha'
    elif defect == 'energy':
        saved['energy'] = None
    elif defect == 'hybrid':
        meta['conditions']['parameters']['LHFCALC'] = True
    elif defect == 'potential':
        meta['conditions'].pop('potcar_sha256')
    elif defect == 'kpoints':
        meta['conditions']['kpoints'] = None
        meta['conditions']['actual_kpoints'] = None
    elif defect == 'phase':
        saved['structure']['sites'][1]['abc'] = [.3, .25, .25]
    else:
        meta['status'] = 'failed'
    # A fresh isolated database lets the public contract store this malformed
    # candidate without merging it with the already valid archived record.
    config_path = Path(os.environ['AI_READY_DB_CONFIG'])
    config = json.loads(config_path.read_text())
    config['ase_sqlite'] = {'path': str(tmp_path / 'malformed.db'), 'json_sidecar_path': str(tmp_path / 'malformed.json')}
    config_path.write_text(json.dumps(config))
    public_archive(local_aidb, tmp_path, saved)
    responses, prepared = query(tmp_path, '只准备金刚石硅体相 PBE 弛豫和总能输入，不提交')
    feedback = responses[0]['aidb_preflight']
    assert feedback['status'] == 'found'
    assert feedback['reuse']['status'] == 'unavailable'
    assert feedback['reuse']['candidates'][0]['compatible'] is False
    assert prepared == []


@pytest.mark.parametrize('entry', ['thinking', 'step'])
def test_registered_entry_reuses_and_respects_parent_recalculation(local_aidb, tmp_path, monkeypatch, entry):
    seed_relaxation(tmp_path, monkeypatch)
    if entry == 'step':
        request = json.dumps({'action': '准备硅弛豫与总能输入', 'prior_context': ''})
        state = {'goal': '计算金刚石硅体相 PBE 弛豫和总能'}
    else:
        request, state = '计算金刚石硅体相 PBE 弛豫和总能', {}
    responses, prepared = query(tmp_path, request, entry=entry, state=state)
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'reused'
    assert prepared == []
    if entry == 'step':
        state = {'goal': '重新计算并提交金刚石硅体相 PBE 弛豫和总能'}
        request = json.dumps({'action': '提交硅弛豫计算', 'prior_context': ''})
        responses, prepared = query(tmp_path, request, entry=entry, state=state)
        assert responses[0]['aidb_preflight']['reuse']['status'] == 'recalculate'
        assert prepared == ['remote submission']


def test_missing_identity_conditions_and_query_error_never_submit(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, '请计算硅弛豫和总能')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'needs_clarification'
    assert prepared == []
    monkeypatch.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
    responses, prepared = query(tmp_path)
    assert responses[0]['aidb_preflight']['status'] == 'failed'
    assert prepared == []


def test_changed_cutoff_does_not_keep_old_result(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = run_task(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能',
        [('load_skill', {'skill_name': 'vasp-pymatgen'})], followup='改用 ENCUT=600 计算总能')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'reused'
    assert responses[1]['aidb_preflight']['reuse']['status'] == 'unavailable'
    assert responses[0]['aidb_preflight']['report_path'] != responses[1]['aidb_preflight']['report_path']
    assert prepared == []

def test_explicit_static_goal_never_substitutes_relaxation_energy(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, '只准备金刚石硅体相 PBE 弛豫和静态总能输入，不提交')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'unavailable'
    assert prepared == []


def test_flash_entry_does_not_spawn_computation_when_reused(local_aidb, tmp_path, monkeypatch):
    seed_relaxation(tmp_path, monkeypatch)
    called = []
    def run_flash_step(action: str) -> dict:
        called.append(action)
        return {'status': 'executed'}
    responses, prepared = run_task(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能',
        [('run_flash_step', {'action': 'compute Si'})], extra_tools=(run_flash_step,))
    assert responses[0]['status'] == 'reused'
    assert called == prepared == []


@pytest.mark.parametrize('setting', ['EDIFF=0.000001', 'k网格=8x8x8'])
def test_requested_parameters_are_not_replaced_with_candidate_conditions(local_aidb, tmp_path, monkeypatch, setting):
    seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, f'只准备金刚石硅体相 PBE 弛豫总能 {setting} 输入，不提交')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'unavailable'
    assert prepared == []

def test_matching_uppercase_kpoints_condition_reuses(local_aidb, tmp_path, monkeypatch):
    saved = seed_relaxation(tmp_path, monkeypatch)
    responses, prepared = query(tmp_path, '计算金刚石硅体相 PBE 弛豫和总能 KPOINTS=4x4x4')
    assert responses[0]['aidb_preflight']['reuse']['status'] == 'reused'
    assert responses[0]['aidb_preflight']['reuse']['record_ids'] == [saved['record_id']]
    assert prepared == []
