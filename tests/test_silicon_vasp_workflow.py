from types import SimpleNamespace
from pathlib import Path
import json
import hashlib

import pytest

from matcreator.tools.silicon_vasp import collect_silicon_vasp_result
from matcreator.agents.execution_agent import remote_job_tools
from matcreator.control_plane.remote_jobs import RemoteJobStore
from matcreator.control_plane.remote_job_service import RemoteJobService
from matcreator.control_plane.providers.base import RemoteJobAdapter, RemoteJobCapability, RemoteJobStatus
from test_aidb_skill_hook import local_aidb, run_task


class Provider(RemoteJobAdapter):
    provider = 'bohr_batchjob'
    capabilities = frozenset({RemoteJobCapability.BATCH_COLLECT})

    def create(self, spec):
        return 'test-batch'

    def status(self, external_id):
        return RemoteJobStatus(normalized_status='succeeded' if getattr(self, 'finished', False) else 'running', snapshot={})

    def cancel(self, external_id):
        pass

    def collect_outputs(self, external_id, destination_dir):
        destination_dir.mkdir()
        for name, content in getattr(self, 'outputs', {}).items():
            (destination_dir / name).write_text(content)
        return [{'destination': str(p)} for p in destination_dir.iterdir()] or [{'destination': str(destination_dir)}]


def job_context(tmp_path, monkeypatch, outputs=None):
    service = RemoteJobService(RemoteJobStore(tmp_path / 'jobs.db'),
        adapter_overrides={'bohr_batchjob': Provider()})
    monkeypatch.setattr(remote_job_tools, '_service', lambda: service)
    context = SimpleNamespace(state={'workspace_dir': str(tmp_path), 'session_id': 'session'},
        _invocation_context=SimpleNamespace(user_id='test'))
    spec = {'input_path': 'relax', 'input_root': str(tmp_path)}
    if outputs is not None:
        source = tmp_path / 'relax'
        source.mkdir()
        for name in ('INCAR', 'POSCAR', 'KPOINTS'):
            (source / name).write_text(outputs[name])
        (source / 'POTCAR').write_text('TITEL = PAW_PBE Si 05Jan2001\nsynthetic test potential, not for computation\n')
        spec['vasp_input_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}
    job = service.submit_job(owner_id='test', session_id='session', provider='bohr_batchjob',
        idempotency_key='silicon-relax', spec=spec)
    return service, context, job['job_id']


def test_running_job_is_not_scientific_success_or_resubmitted(tmp_path, monkeypatch):
    service, context, job_id = job_context(tmp_path, monkeypatch)
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'needs_replanning'
    assert result['job_id'] == job_id
    assert not (tmp_path / 'outputs').exists()


def test_platform_success_with_missing_outputs_is_not_scientific_success(tmp_path, monkeypatch):
    service, context, job_id = job_context(tmp_path, monkeypatch)
    service.store.transition_job(job_id, 'succeeded')
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'invalid'
    assert 'vasprun.xml' in result['message']
    assert (tmp_path / 'outputs').exists()


def silicon_outputs(nelm=60, electronic_steps=2, ionic_marker=True, stage='relaxation'):
    # Deliberately synthetic provider output; never evidence of real compute.
    from pymatgen.core import Lattice, Structure
    from pymatgen.io.vasp.inputs import Poscar
    structure = Structure(Lattice([[0, 2.715, 2.715], [2.715, 0, 2.715], [2.715, 2.715, 0]]),
        ['Si', 'Si'], [[0, 0, 0], [.25, .25, .25]])
    positions = '<varray name="positions"><v>0 0 0</v><v>.25 .25 .25</v></varray>'
    cell = '<crystal><varray name="basis"><v>0 2.715 2.715</v><v>2.715 0 2.715</v><v>2.715 2.715 0</v></varray></crystal>'
    nsw, ibrion = (99, 2) if stage == 'relaxation' else (0, -1)
    params = f'<i name="NELM" type="int">{nelm}</i><i name="NSW" type="int">{nsw}</i><i name="IBRION" type="int">{ibrion}</i><i name="EDIFFG">-.02</i><i name="ENCUT">520</i>'
    energy = '<energy><i name="e_fr_energy">-10.1</i><i name="e_0_energy">-10.1</i><i name="e_wo_entrp">-10.1</i></energy>'
    xml = f'''<modeling><generator><i name="version" type="string">6.4</i></generator>
    <incar>{params}</incar><parameters>{params}</parameters>
    <atominfo><array name="atoms"><set><rc><c>Si</c></rc><rc><c>Si</c></rc></set></array>
    <array name="atomtypes"><set><rc><c>2</c><c>Si</c><c>28.085</c><c>4</c><c>PAW_PBE Si 05Jan2001</c></rc></set></array></atominfo>
    <structure name="initialpos">{cell}{positions}</structure>
    <calculation>{''.join('<scstep>'+energy+'</scstep>' for _ in range(electronic_steps))}{energy}
    <structure>{cell}{positions}</structure><varray name="forces"><v>0 0 0</v><v>0 0 0</v></varray></calculation>
    <structure name="finalpos">{cell}{positions}</structure></modeling>'''
    return {'vasprun.xml': xml, 'POSCAR': str(Poscar(structure)), 'CONTCAR': str(Poscar(structure)),
        'INCAR': f'NELM = {nelm}\nNSW = {nsw}\nIBRION = {ibrion}\nENCUT = 520\nEDIFFG = -0.02\n',
        'KPOINTS': 'Synthetic 4x4x4\n0\nGamma\n4 4 4\n0 0 0\n',
        'OUTCAR': ('reached required accuracy - stopping structural energy minimisation\n' if ionic_marker else '') + 'General timing and accounting informations for this job:\n',
        'OSZICAR': ' 1 F= -.10100000E+02 E0= -.10100000E+02\n'}


def completed_job(tmp_path, monkeypatch, outputs):
    service, context, job_id = job_context(tmp_path, monkeypatch, outputs)
    service.adapter_for('bohr_batchjob').outputs = outputs
    service.store.transition_job(job_id, 'succeeded')
    return service, context, job_id


def test_verified_relaxation_has_energy_structure_provenance_and_replays(tmp_path, monkeypatch):
    service, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'success'
    report = json.loads(Path(result['report_path']).read_text())
    assert report['total_energy'] == {'value': -10.1, 'unit': 'eV'}
    assert report['job_id'] == job_id and report['batchjob_id'] == 'test-batch'
    assert report['structure']['sites'][0]['species'][0]['element'] == 'Si'
    assert report['conditions']['parameters']['ENCUT'] == 520
    assert report['archived'] is False
    # Same durable store in a new service instance simulates reconnect.
    monkeypatch.setattr(remote_job_tools, '_service', lambda: RemoteJobService(service.store))
    replay = collect_silicon_vasp_result(job_id, 'other-output-path', 'relaxation', context)
    assert replay['report_path'] == result['report_path']
    assert not (tmp_path / 'other-output-path').exists()


def test_static_without_verified_relaxation_is_not_a_completed_two_step_task(tmp_path, monkeypatch):
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs(stage='static'))
    result = collect_silicon_vasp_result(job_id, 'outputs', 'static', context)
    assert result['status'] == 'invalid'
    assert 'relaxation_job_id' in result['message']


@pytest.mark.parametrize('failure', ['electronic', 'ionic', 'truncated', 'wrong_method', 'wrong_structure', 'changed_input'])
def test_bad_scientific_outputs_are_preserved_but_rejected(tmp_path, monkeypatch, failure):
    outputs = silicon_outputs(nelm=2) if failure == 'electronic' else silicon_outputs(ionic_marker=failure != 'ionic')
    if failure == 'truncated':
        outputs['vasprun.xml'] = outputs['vasprun.xml'][:-12]
    if failure == 'wrong_method':
        outputs['vasprun.xml'] = outputs['vasprun.xml'].replace('PAW_PBE Si', 'PAW_LDA Si')
    if failure == 'wrong_structure':
        outputs['vasprun.xml'] = outputs['vasprun.xml'].replace('<c>Si</c>', '<c>Ge</c>')
    _, context, job_id = completed_job(tmp_path, monkeypatch, outputs)
    if failure == 'changed_input':
        (tmp_path / 'relax/INCAR').write_text('NSW=0')
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'invalid'
    assert (tmp_path / 'outputs/vasprun.xml').read_text() == outputs['vasprun.xml']
    report = json.loads(Path(result['report_path']).read_text())
    assert report['status'] == 'invalid' and report['archived'] is False


def test_static_total_energy_keeps_verified_relaxation_source(tmp_path, monkeypatch):
    service, context, parent_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    assert collect_silicon_vasp_result(parent_id, 'relax-outputs', 'relaxation', context)['status'] == 'success'
    outputs = silicon_outputs(stage='static')
    source = tmp_path / 'static'
    source.mkdir()
    for name in ('INCAR', 'POSCAR', 'KPOINTS'):
        (source / name).write_text(outputs[name])
    (source / 'POTCAR').write_bytes((tmp_path / 'relax/POTCAR').read_bytes())
    service.adapter_for('bohr_batchjob').outputs = outputs
    job = service.submit_job(owner_id='test', session_id='session', provider='bohr_batchjob',
        idempotency_key='silicon-static', spec={'input_path': 'static', 'input_root': str(tmp_path),
            'vasp_input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}})
    service.store.transition_job(job['job_id'], 'succeeded')
    result = collect_silicon_vasp_result(job['job_id'], 'static-outputs', 'static', context, parent_id)
    assert result['status'] == 'success'
    report = json.loads(Path(result['report_path']).read_text())
    assert report['relaxation_source']['job_id'] == parent_id
    assert report['completion']['ionic_converged'] is None
    # Source outputs are checked again; editing a cached JSON cannot confer success.
    (tmp_path / 'relax-outputs/OUTCAR').write_text('incomplete output')
    replay = collect_silicon_vasp_result(job['job_id'], 'ignored', 'static', context, parent_id)
    assert replay['status'] == 'invalid'


def test_modified_potential_cannot_change_submitted_provenance(tmp_path, monkeypatch):
    _, context, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    (tmp_path / 'relax/POTCAR').write_text('TITEL = PAW_PBE Si 05Jan2001\nchanged potential\n')
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'invalid'
    assert 'POTCAR' in result['message']


@pytest.mark.parametrize('correction', ['IVDW', 'LUSE_VDW'])
def test_dispersion_corrected_pbe_is_outside_ordinary_pbe(tmp_path, monkeypatch, correction):
    outputs = silicon_outputs()
    tag = '<i name="IVDW" type="int">12</i>' if correction == 'IVDW' else '<i name="LUSE_VDW" type="logical">T</i>'
    outputs['vasprun.xml'] = outputs['vasprun.xml'].replace('</parameters>', tag + '</parameters>')
    _, context, job_id = completed_job(tmp_path, monkeypatch, outputs)
    result = collect_silicon_vasp_result(job_id, 'outputs', 'relaxation', context)
    assert result['status'] == 'invalid'
    assert 'ordinary PBE' in result['message']


def test_tracked_submit_binds_potential_before_transfer_and_replay(tmp_path, monkeypatch):
    provider = Provider()
    provider.outputs = silicon_outputs()
    provider.finished = True
    service = RemoteJobService(RemoteJobStore(tmp_path / 'jobs.db'), adapter_overrides={'bohr_batchjob': provider})
    monkeypatch.setattr(remote_job_tools, '_service', lambda: service)
    from matcreator.agents.execution_agent import recovery
    monkeypatch.setattr(recovery, 'ADK_DIR', tmp_path)
    context = SimpleNamespace(state={'workspace_dir': str(tmp_path), 'session_id': 'snapshot-test'},
        _invocation_context=SimpleNamespace(user_id='test'))
    source = tmp_path / 'relax'
    source.mkdir()
    for name in ('INCAR', 'POSCAR', 'KPOINTS'):
        (source / name).write_text(provider.outputs[name])
    (source / 'POTCAR').write_text('TITEL = PAW_PBE Si 05Jan2001\nsynthetic potential\n')
    args = dict(name='test-si', image='test-image', machine_type='test-cpu', project_id=1,
        command='test provider', input_path='relax')
    submission = remote_job_tools.submit_bohr_batchjob(context, **args)
    result = collect_silicon_vasp_result(submission['job_id'], 'outputs', 'relaxation', context)
    assert result['status'] == 'success'
    (source / 'POTCAR').write_text('TITEL = PAW_PBE Si 05Jan2001\nchanged potential\n')
    repeated = remote_job_tools.submit_bohr_batchjob(context, **args)
    assert repeated['job_id'] == submission['job_id']
    checked = collect_silicon_vasp_result(repeated['job_id'], 'unused', 'relaxation', context)
    assert checked['status'] == 'invalid' and 'POTCAR' in checked['message']
    assert not (tmp_path / 'unused').exists()


@pytest.mark.parametrize('entry', ['thinking', 'step'])
def test_natural_agent_request_queries_then_reads_scientific_result(local_aidb, tmp_path, monkeypatch, entry):
    _, _, job_id = completed_job(tmp_path, monkeypatch, silicon_outputs())
    calls = [('load_skill', {'skill_name': 'vasp-pymatgen'}),
        ('run_python', {'code': 'prepare'}),
        ('collect_silicon_vasp_result', {'job_id': job_id, 'destination_path': 'outputs', 'calculation_type': 'relaxation'})]
    responses, prepared = run_task(tmp_path, '重新计算金刚石单质硅体相普通 PBE 弛豫和总能',
        calls, state={'session_id': 'session'}, entry=entry, extra_tools=[collect_silicon_vasp_result])
    assert responses[0]['aidb_preflight']['status'] == 'not_found'
    assert prepared == ['prepare']
    assert responses[-1]['status'] == 'success'
    assert Path(responses[-1]['report_path']).is_file()
