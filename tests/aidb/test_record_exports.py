"""Exercise exports through the session boundary and public file readers."""
import asyncio
from io import StringIO
from types import SimpleNamespace

import pytest
from ase.io import read
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pymatgen.core import Lattice, Structure

from matcreator.aidb.service import AIDBError
from matcreator.aidb.service import SessionAIDB
from web.aidb_api import create_aidb_router


@pytest.fixture
def bound_record(tmp_path):
    structure = Structure(Lattice([[3, 0, 0], [1, 4, 0], [0, 0, 5]], pbc=(True, False, True)),
                          ['Na', 'Cl', 'Na'], [[0, 0, 0], [0.25, 0.5, 0.25], [0.75, 0.5, 0.25]])
    record = {
        'record_id': 'example', 'formula_reduced': 'Na2Cl', 'nsites': 3, 'structure': structure.as_dict(),
        'frame_index': 4, 'source_id': 'output <sample>', 'energy': -8.25,
        'forces': [[0.1, 0, 0], [0, 0.2, 0], [-0.1, -0.2, 0]],
        'source_metadata': {'vasp_facts': {'labels': {
            'energy_definition': 'e_0_energy', 'energy_unit': 'eV/cell', 'force_unit': 'eV/Angstrom'}}},
    }
    queries = []

    def database(config, workspace, conditions):
        queries.append(conditions)
        return [record.copy()], {'conditions': conditions, 'match_count': 1, 'complete': True}

    config = tmp_path / 'database.json'
    config.write_text('{}')
    service = SessionAIDB(tmp_path, 'export-session', query_database=database)
    binding = service.bind(config)
    return service, record, queries, binding['binding_id']


def test_extxyz_is_built_from_fresh_readback_with_order_and_frame_labels(bound_record):
    service, record, queries, binding_id = bound_record
    reference = service.publish(service.query({'formula': 'Na2Cl'}))
    record['energy'] = -9.5

    result = service.export('example', ['extxyz'], result_id=reference['result_id'], binding_id=binding_id)
    file = service.export_file(result['export_id'], 'extxyz')
    atoms = read(StringIO(file['content']), format='extxyz')

    assert queries == [{'formula': 'Na2Cl'}, {'record_id': 'example'}]
    assert result['status'] == 'complete'
    assert file['filename'] == 'aidb-example.extxyz'
    assert atoms.get_chemical_symbols() == ['Na', 'Cl', 'Na']
    assert atoms.cell.array.tolist() == [[3, 0, 0], [1, 4, 0], [0, 0, 5]]
    assert atoms.pbc.tolist() == [True, False, True]
    assert atoms.positions.tolist() == [[0, 0, 0], [1.25, 2, 1.25], [2.75, 2, 1.25]]
    assert atoms.get_potential_energy() == -9.5
    assert atoms.get_forces().tolist() == [[0.1, 0, 0], [0, 0.2, 0], [-0.1, -0.2, 0]]
    assert atoms.info['record_id'] == 'example'
    assert atoms.info['frame_index'] == 4
    assert atoms.info['energy_definition'] == 'e_0_energy'
    assert atoms.info['energy_unit'] == 'eV'
    assert atoms.info['energy_basis'] == 'cell_total'
    assert atoms.info['force_unit'] == 'eV/Angstrom'


@pytest.mark.parametrize('forces', [None, [[1, 2, 3]], [[False, True, False]] * 3])
def test_missing_force_does_not_block_download_of_structure_and_full_detail(bound_record, forces):
    service, record, _, _ = bound_record
    record['forces'] = forces

    receipt = service.export('example', ['extxyz', 'poscar', 'html'])

    assert receipt['status'] == 'partial'
    assert set(receipt['files']) == {'poscar', 'html'}
    assert '逐原子力' in receipt['issues']['extxyz']
    poscar = service.export_file(receipt['export_id'], 'poscar')
    atoms = read(StringIO(poscar['content']), format='vasp')
    assert atoms.get_chemical_symbols() == ['Na', 'Cl', 'Na']
    assert atoms.cell.array.tolist() == [[3, 0, 0], [1, 4, 0], [0, 0, 5]]
    assert atoms.positions.tolist() == [[0, 0, 0], [1.25, 2, 1.25], [2.75, 2, 1.25]]
    html = service.export_file(receipt['export_id'], 'html')['content']
    assert 'output &lt;sample&gt;' in html
    assert 'e_0_energy' in html
    assert 'eV/cell' in html
    assert 'eV/Å' in html
    for section in ('末帧结构与采样', '计算方法与磁性', '能量、力、应力和维里张量', '附加结果', '来源与核对依据'):
        assert f'<summary>{section}</summary>' in html
    assert '完整 JSON' in html


@pytest.mark.parametrize(('update', 'has_stress', 'has_virial'), [
    ({}, True, True),
    ({'virial': None}, True, False),
    ({'virial': [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}, False, False),
    ({'stress_convention': 'unknown'}, False, False),
    ({'frame_index': None}, False, False),
])
def test_optional_tensors_need_frame_and_conversion_evidence(bound_record, update, has_stress, has_virial):
    service, record, _, _ = bound_record
    record.update(stress=[[0.1, 0, 0], [0, 0.2, 0], [0, 0, 0.3]],
                  stress_unit='eV/Angstrom^3', stress_convention='tensile_positive',
                  virial=[[-6, 0, 0], [0, -12, 0], [0, 0, -18]], virial_unit='eV')
    record.update(update)

    receipt = service.export('example', ['extxyz'])
    atoms = read(StringIO(service.export_file(receipt['export_id'], 'extxyz')['content']), format='extxyz')

    assert ('stress' in atoms.calc.results) is has_stress
    assert ('virial' in atoms.info) is has_virial
    if has_stress:
        assert atoms.get_stress().tolist() == [0.1, 0.2, 0.3, 0, 0, 0]
        assert atoms.info['stress_unit'] == 'eV/Angstrom^3'
        assert atoms.info['stress_convention'] == 'tensile_positive'
    if has_virial:
        assert atoms.info['virial'].tolist() == [[-6, 0, 0], [0, -12, 0], [0, 0, -18]]
        assert atoms.info['virial_unit'] == 'eV'


def test_result_row_download_is_bound_to_owner_record_and_fresh_readback(bound_record, monkeypatch):
    service, record, queries, binding_id = bound_record
    reference = service.publish(service.query({'formula': 'Na2Cl'}))
    database = service.query_database

    def read_installed(config, workspace, conditions):
        if record['energy'] is None:
            raise AIDBError('读回失败')
        return database(config, workspace, conditions)

    monkeypatch.setattr('matcreator.aidb.service.InstalledSkillQuery', lambda: read_installed)
    app = FastAPI()
    app.include_router(create_aidb_router(lambda owner, session: owner == 'owner', lambda session, user_id: service.workspace))
    client = TestClient(app)
    base = '/api/users/owner/sessions/export-session/aidb'
    row = f"/results/{reference['result_id']}/records/example/export"
    binding = {'binding_id': binding_id}

    assert client.post(base.replace('/owner/', '/other/') + row, json=binding).status_code == 404
    assert client.post(base + row.replace('/example/', '/other/'), json=binding).status_code == 409
    assert queries == [{'formula': 'Na2Cl'}]
    for energy in (-9.0, -10.0):
        record['energy'] = energy
        receipt = client.post(base + row, json=binding).json()
        download = f"/exports/{receipt['export_id']}/extxyz"
        response = client.get(base + download)
        assert response.headers['Content-Disposition'] == 'attachment; filename="aidb-example.extxyz"'
        assert read(StringIO(response.text), format='extxyz').get_potential_energy() == energy
        assert client.get(base.replace('/owner/', '/other/') + download).status_code == 404

    assert queries == [{'formula': 'Na2Cl'}, {'record_id': 'example'}, {'record_id': 'example'}]
    record['energy'] = None
    assert client.post(base + row, json=binding).json()['detail']['status'] == 'failed'
    service.bind(service.binding()['config_path'])
    assert client.get(base + download).json()['detail']['status'] == 'stale_binding'


def test_chat_export_tool_returns_downloads_for_the_same_session_readback(bound_record, monkeypatch):
    from google.adk.tools.function_tool import FunctionTool
    from matcreator.tools.aidb_tools import aidb_export

    service, record, queries, _ = bound_record
    reference = service.publish(service.query({'formula': 'Na2Cl'}))
    monkeypatch.setattr('matcreator.aidb.service.InstalledSkillQuery', lambda: service.query_database)
    tool = FunctionTool(aidb_export)
    declaration = tool._get_declaration()
    schema = declaration.parameters_json_schema or declaration.parameters.model_dump()
    assert set(schema['properties']) == {'record_id', 'formats', 'result_id'}
    context = SimpleNamespace(
        state={'workdir': str(service.workspace), 'session_id': service.session_id},
        _invocation_context=SimpleNamespace(user_id='owner'))
    record['energy'] = -13.0

    receipt = asyncio.run(tool.run_async(args={
        'record_id': 'example', 'formats': ['extxyz'], 'result_id': reference['result_id'],
    }, tool_context=context))

    assert receipt['user_id'] == 'owner'
    assert receipt['session_id'] == service.session_id
    assert receipt['kind'] == 'aidb_export'
    assert receipt['files'] == {'extxyz': {'filename': 'aidb-example.extxyz'},
                                'poscar': {'filename': 'POSCAR-example'},
                                'html': {'filename': 'aidb-example.html'}}
    assert queries == [{'formula': 'Na2Cl'}, {'record_id': 'example'}]
    file = service.export_file(receipt['export_id'], 'extxyz')
    assert read(StringIO(file['content']), format='extxyz').get_potential_energy() == -13.0


def test_detail_discards_readback_when_database_binding_changes_mid_read(bound_record):
    service, _, _, binding_id = bound_record
    query = service.query_database
    config_path = service.binding()['config_path']

    def rebind_during_read(config, workspace, conditions):
        service.bind(config_path)
        return query(config, workspace, conditions)

    service.query_database = rebind_during_read
    for expected in (binding_id, None):
        with pytest.raises(AIDBError) as error:
            service.detail('example', binding_id=expected)
        assert error.value.status == 'stale_binding'


def test_completion_recovers_the_same_run_and_preserves_artifacts(bound_record, monkeypatch):
    service, record, queries, binding_id = bound_record
    (service.workspace / 'outputs').mkdir()
    (service.workspace / 'outputs' / 'OUTCAR').write_text('original output')
    (service.workspace / 'private').write_text('unrelated workspace data')
    monkeypatch.setattr('matcreator.aidb.service.InstalledSkillQuery', lambda: service.query_database)
    state = {
        'compute_status': 'succeeded',
        'status': 'failed',
        'reason': 'AIDB 写入暂时失败',
        'binding_id': binding_id,
        'artifact_paths': ['outputs/OUTCAR'],
        'record_id': None,
        'summary': {'formula': 'Na2Cl', 'energy_ev_per_cell': -8.25, 'frame_index': 4},
    }

    def receipt(*, user_id, session_id, job_id):
        return state if (user_id, session_id, job_id) == ('owner', 'export-session', 'same-job') else None

    app = FastAPI()
    app.include_router(create_aidb_router(
        lambda owner, session: session == 'export-session',
        lambda session, user_id: service.workspace,
        get_completion=receipt,
    ))
    client = TestClient(app)
    base = '/api/users/owner/sessions/export-session/aidb'
    row = base + '/completions/same-job'
    export = row + '/records/example/export'
    binding = {'binding_id': binding_id}
    artifact = row + '/artifact'
    params = {'path': 'outputs/OUTCAR'}

    failed = client.get(row).json()
    assert failed['result_id'] == 'same-job'
    assert failed['compute_status'] == 'succeeded'
    assert failed['archive_status'] == 'failed'
    assert failed['reason'] == 'AIDB 写入暂时失败'
    assert failed['summary']['formula'] == 'Na2Cl'
    assert failed['artifact_paths'] == ['outputs/OUTCAR']
    assert failed['records'] == []
    assert client.get(artifact, params=params).content == b'original output'
    assert client.get(artifact, params={'path': 'private'}).status_code == 404
    assert client.get(row.replace('/owner/', '/other/')).status_code == 404
    assert client.get(artifact.replace('/owner/', '/other/'), params=params).status_code == 404
    assert client.post(export, json=binding).json()['detail']['status'] == 'not_ready'
    assert queries == []

    state.update(status='verified', record_id='example', outcome='existing', reason=None)
    ready = client.get(row).json()
    assert ready['result_id'] == failed['result_id']
    assert ready['archive_status'] == 'verified'
    assert ready['outcome'] == 'existing'
    assert [item['record_id'] for item in ready['records']] == ['example']
    assert client.post(export.replace('/example/', '/another/'), json=binding).status_code == 409
    assert client.post(export, json={'binding_id': 'wrong-binding'}).status_code == 409

    for energy in (-9.5, -10.5):
        record['energy'] = energy
        detail = client.get(base + '/records/example', params=binding).json()['record']
        assert detail['energy'] == energy
        response = client.post(export, json=binding)
        assert response.status_code == 200, response.text
        export_id = response.json()['export_id']
        file = client.get(f'{base}/exports/{export_id}/extxyz')
        atoms = read(StringIO(file.text), format='extxyz')
        assert atoms.info['record_id'] == 'example'
        assert atoms.get_potential_energy() == energy
        assert atoms.get_forces().tolist() == record['forces']
    assert queries == [{'record_id': 'example'}] * 5

    config = service.workspace / 'database.json'
    service.bind(config)
    unavailable = client.get(row).json()
    assert unavailable['archive_status'] == 'verified'
    assert unavailable['records'] == []
    assert '数据库已切换' in unavailable['readback_issue']
    assert client.post(export, json=binding).status_code == 409
    assert client.get(artifact, params=params).content == b'original output'
