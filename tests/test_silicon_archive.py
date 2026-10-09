"""Full tool/callback round trips; synthetic outputs are not real computation."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import hashlib
import subprocess
import os
import sys

from matcreator.agents.aidb_skill_hook import after_aidb_skill_load
from matcreator.tools.silicon_vasp import collect_silicon_vasp_result
from test_aidb_skill_hook import local_aidb, run_task
from test_silicon_vasp_workflow import completed_job, silicon_outputs


def test_successful_collection_automatically_archives_and_reads_back(local_aidb, tmp_path, monkeypatch):
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    args = {'job_id': job_id, 'destination_path': 'outputs', 'calculation_type': 'relaxation'}
    response = collect_silicon_vasp_result(**args, tool_context=context)
    response = after_aidb_skill_load(SimpleNamespace(name='collect_silicon_vasp_result'), args, context, response)
    assert response['archived'] is True
    archive = response['local_archive']
    assert archive['status'] == 'verified'
    saved = json.loads(Path(archive['export_path']).read_text())
    assert saved['record_id'] == archive['record_id']
    assert saved['energy'] == -10.1
    assert saved['source_metadata']['energy_unit'] == 'eV'
    assert saved['source_metadata']['completion']['electronic_converged'] is True
    assert saved['source_metadata']['job_id'] == job_id
    assert saved['calculation_parameters']['ENCUT'] == 520
    assert saved['source_metadata']['archived_at']
    assert saved['source_metadata']['sync_status'] == 'local_only'
    assert saved['forces'] is None and saved['stress'] is None


@pytest.mark.parametrize('entry', ['thinking', 'step'])
def test_registered_agent_collection_automatically_archives(local_aidb, tmp_path, monkeypatch, entry):
    _, _, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    responses, prepared = run_task(tmp_path, '取回已有金刚石硅 PBE 弛豫成功产物并自动本地归档，不重新计算',
        [('collect_silicon_vasp_result', {'job_id': job_id, 'destination_path': 'outputs', 'calculation_type': 'relaxation'})],
        state={'session_id': 'session'}, entry=entry, extra_tools=(collect_silicon_vasp_result,))
    assert responses[0]['local_archive']['content_compared'] is True
    assert responses[0]['archived'] is True
    assert prepared == []


def test_repeated_completion_keeps_record_and_archive_time(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    first = archive_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)['local_archive']
    second = archive_silicon_vasp_result(job_id, 'ignored', 'relaxation', context)['local_archive']
    assert first['status'] == second['status'] == 'verified'
    assert second['previously_present'] is True
    assert first['record_id'] == second['record_id']
    assert first['archived_at'] == second['archived_at']


def test_unknown_scientific_completion_does_not_archive(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs(ionic_marker=False))
    result = archive_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'invalid' and result['archived'] is False
    assert not (tmp_path / '.aidb/archives').exists()
    assert (tmp_path / 'outputs/vasprun.xml').is_file()


def test_static_archive_keeps_parent_record_and_separate_energy(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    service, context, parent = completed_job(tmp_path, monkeypatch, silicon_outputs())
    relaxed = archive_silicon_vasp_result(parent, 'relax-outputs', 'relaxation', context)
    outputs = silicon_outputs(stage='static')
    outputs['vasprun.xml'] = outputs['vasprun.xml'].replace('-10.1', '-10.2')
    source = tmp_path / 'static'
    source.mkdir()
    for name in ('INCAR', 'POSCAR', 'KPOINTS'):
        (source / name).write_text(outputs[name])
    (source / 'POTCAR').write_bytes((tmp_path / 'relax/POTCAR').read_bytes())
    service.adapter_for('bohr_batchjob').outputs = outputs
    job = service.submit_job(owner_id='test', session_id='session', provider='bohr_batchjob',
        idempotency_key='static', spec={'input_root': str(tmp_path), 'input_path': 'static',
            'vasp_input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}})
    service.store.transition_job(job['job_id'], 'succeeded')
    result = archive_silicon_vasp_result(job['job_id'], 'static-outputs', 'static', context, parent)
    assert result['archived'] is True
    saved = json.loads(Path(result['local_archive']['export_path']).read_text())
    assert saved['energy'] == -10.2
    assert saved['source_metadata']['parent_record_id'] == relaxed['local_archive']['record_id']
    assert saved['source_metadata']['relaxation_source']['job_id'] == parent
    assert saved['source_metadata']['completion']['ionic_converged'] is None


def test_lookup_failure_retains_outputs_and_can_recover_without_resubmission(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    with monkeypatch.context() as missing:
        missing.setenv('AI_READY_DB_CONFIG', str(tmp_path / 'missing.json'))
        result = archive_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['archived'] is False
    assert result['local_archive']['status'] == 'failed'
    assert not list((tmp_path / '.aidb/runs').glob('**/archive-report.json'))
    assert (tmp_path / 'outputs/OUTCAR').is_file()
    recovered = archive_silicon_vasp_result(job_id, 'ignored', 'relaxation', context)
    assert recovered['archived'] is True


def test_uncertain_write_is_queried_and_read_back_before_retry(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    real_run = subprocess.run
    def lose_receipt(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if 'archive-and-requery' in argv:
            raise subprocess.TimeoutExpired(argv, 75, output=result.stdout, stderr=result.stderr)
        return result
    with monkeypatch.context() as network:
        network.setattr(subprocess, 'run', lose_receipt)
        first = archive_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert first['archived'] is False
    second = archive_silicon_vasp_result(job_id, 'ignored', 'relaxation', context)
    assert second['archived'] is True
    assert second['local_archive']['previously_present'] is True
    assert len(list((tmp_path / '.aidb/runs').glob('**/archive-report.json'))) == 1


def test_readback_difference_is_reported_without_overwriting_record(local_aidb, tmp_path, monkeypatch):
    from matcreator.tools.silicon_archive import archive_silicon_vasp_result
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    first = archive_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    saved = json.loads(Path(first['local_archive']['export_path']).read_text())
    saved['source_metadata']['energy_unit'] = 'Ha'
    edited = tmp_path / 'edited.json'
    edited.write_text(json.dumps(saved))
    subprocess.run([sys.executable, str(local_aidb / 'skills/ai-ready-db/scripts/aidb_client.py'),
        'archive-local', '--record-json', str(edited), '--namespace', 'bulk_base_pbe',
        '--origin', 'local_calculation', '--sync-status', 'local_only', '--config', os.environ['AI_READY_DB_CONFIG']], check=True, capture_output=True)
    result = archive_silicon_vasp_result(job_id, 'ignored', 'relaxation', context)
    assert result['archived'] is False
    assert 'energy_unit' in result['local_archive']['error']
    assert len(list((tmp_path / '.aidb/runs').glob('**/archive-report.json'))) == 1
