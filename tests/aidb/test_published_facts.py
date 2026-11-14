"""Model-visible facts through the real tools; only the external DB is controlled."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from matcreator.aidb.service import InstalledSkillQuery, SessionAIDB
from matcreator.tools.aidb_tools import aidb_bind, aidb_detail, aidb_query


@pytest.fixture
def database(tmp_path, monkeypatch):
    records = [{
        'record_id': 'si-pbe', 'formula_reduced': 'Si', 'functional': 'PBE',
        'calculation_type': 'static', 'nsites': 2, 'frame_index': 0,
        'structure': {'lattice': {'matrix': [[3, 0, 0], [0, 3, 0], [0, 0, 3]]},
                      'sites': [{'label': 'Si', 'abc': [0, 0, 0]},
                                {'label': 'Si', 'abc': [0.25, 0.25, 0.25]}]},
        'energy': -10.5, 'forces': [[0, 0, 0], [0.1, 0, 0]],
        'stress': [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'stress_unit': None,
        'virial': None, 'hubbard_u': None,
        'calculation_parameters': {'ENCUT': 520, 'EDIFF': 1e-6, 'LDAU': False},
        'source_dataset': 'reference-si', 'source_id': 'independent-run-a',
        'source_metadata': {'vasp_facts': {
            'phase': 'diamond', 'spacegroup': 'Fd-3m',
            'method': {'version': '6.4', 'kpoints': {'grid': [4, 4, 4]},
                       'potcar_symbols': ['PAW_PBE Si'],
                       'spin': {'ISPIN': 1, 'LSORBIT': False},
                       'functional_input': {'GGA': 'PE'},
                       'functional_runtime': {'GGA': 'PE'}},
            'labels': {'energy_definition': 'e_0_energy', 'electronic_converged': False,
                       'ionic_converged': None},
            'source': {'sample_name': 'Si-reference', 'public_source': 'https://example.test/Si/',
                       'file_sha256': {'vasprun.xml': 'run-a-hash'},
                       'field_evidence': {'energy': 'vasprun.xml final calculation'},
                       'xml_outcar_checks': {'matched': False}},
        }},
    }]

    def read_external_database(self, config, workspace, conditions):
        selected = [r for r in records if not conditions.get('record_id')
                    or r['record_id'] == conditions['record_id']]
        return deepcopy(selected), {'conditions': conditions, 'match_count': len(selected), 'complete': True}

    monkeypatch.setattr(InstalledSkillQuery, '__call__', read_external_database)
    config = tmp_path / 'db.json'
    config.write_text('{}')
    context = SimpleNamespace(state={'session_id': 'facts-chat', 'workdir': str(tmp_path)})
    assert asyncio.run(aidb_bind(str(config), context))['status'] == 'bound'
    return records, context, SessionAIDB(tmp_path, 'facts-chat')


def test_detail_tool_delivers_fresh_answerable_facts_and_exact_ui_snapshot(database):
    records, context, session = database
    first = asyncio.run(aidb_detail('si-pbe', context))
    assert first['status'] == 'complete'
    assert first['operation'] == 'detail'
    assert first['fresh_read'] is True
    assert first['expanded_record_id'] == 'si-pbe'
    facts = first['records'][0]['facts']
    assert facts['structure']['lattice_angstrom'] == [[3, 0, 0], [0, 3, 0], [0, 0, 3]]
    assert facts['method']['input_encut_ev'] == 520
    assert facts['method']['calculation_parameters']['EDIFF'] == 1e-6
    assert facts['method']['calculation_parameters']['LDAU'] is False
    assert facts['method']['kpoints'] == {'grid': [4, 4, 4]}
    assert facts['method']['potcar_symbols'] == ['PAW_PBE Si']
    assert facts['labels']['energy']['value'] == -10.5
    assert facts['labels']['energy']['unit'] == 'eV/cell'
    assert facts['labels']['energy']['definition'] == 'e_0_energy'
    assert facts['labels']['forces']['shape'] == [2, 3]
    assert facts['labels']['forces']['unit'] == 'eV/angstrom'
    assert facts['labels']['stress']['unit'] is None
    assert facts['labels']['virial']['present'] is False
    assert facts['labels']['electronic_converged'] is False
    assert facts['labels']['ionic_converged'] == 'not_applicable'
    assert facts['sampling']['target_start_kelvin'] == 'not_applicable'
    assert facts['source']['source_id'] == 'independent-run-a'
    assert facts['source']['file_sha256'] == {'vasprun.xml': 'run-a-hash'}
    assert facts['source']['xml_outcar_checks'] == {'matched': False}
    assert facts['training_suitability'] == 'not_assessed'
    assert 'training_ready' not in json.dumps(first)
    snapshot = session.result(first['result_id'])
    assert snapshot['record'] == records[0]
    assert snapshot['expanded_record_id'] == 'si-pbe'

    records[0]['energy'] = -11.25
    records[0]['calculation_parameters']['ENCUT'] = 600
    second = asyncio.run(aidb_detail('si-pbe', context))
    assert second['records'][0]['facts']['labels']['energy']['value'] == -11.25
    assert second['records'][0]['facts']['method']['input_encut_ev'] == 600
    assert session.result(first['result_id'])['record']['energy'] == -10.5


def test_query_tool_keeps_native_counts_filters_frame_coverage_and_method_differences(database):
    records, context, session = database
    original = records[0]
    duplicate = deepcopy(original)
    duplicate['record_id'] = 'same-frame-copy'
    independent = deepcopy(original)
    independent['record_id'] = 'different-method'
    independent['source_metadata']['vasp_facts']['source']['file_sha256']['vasprun.xml'] = 'run-b-hash'
    independent['calculation_parameters']['ENCUT'] = 600
    independent['source_metadata']['vasp_facts']['method']['kpoints'] = {'grid': [8, 8, 8]}
    independent['source_metadata']['vasp_facts']['method']['potcar_symbols'] = ['PAW_PBE Si_sv']
    unverified = deepcopy(original)
    unverified['record_id'] = 'unverified-frame'
    unverified.pop('frame_index')
    excluded = deepcopy(original)
    excluded['record_id'] = 'wrong-symmetry'
    excluded['source_metadata']['vasp_facts']['spacegroup'] = 'P1'
    records.extend([duplicate, independent, unverified, excluded])

    conditions = {'formula': 'Si', 'functional': 'PBE', 'has_forces': True,
                  'spacegroup': 'Fd-3m', 'structure_role': 'final'}
    result = asyncio.run(aidb_query(json.dumps(conditions), context))
    assert result['status'] == 'complete'
    assert result['report']['match_count'] == 5
    assert result['report']['complete'] is True
    assert result['report']['conditions'] == {'formula': 'Si', 'functional': 'PBE', 'has_forces': True}
    assert result['conditions'] == conditions
    assert result['host_filter'] == {'spacegroup': 'Fd-3m', 'structure_role': 'final'}
    assert result['matched_count'] == 3
    assert result['duplicates'] == 1
    assert result['coverage'] == {'records': 3, 'verified_frames': 2, 'unverified_records': 1,
                                  'energy_force_frames': 2, 'also_virial_frames': 0}
    rows = result['records']
    assert rows[0]['facts']['method']['input_encut_ev'] == 520
    assert rows[1]['facts']['method']['input_encut_ev'] == 600
    assert rows[1]['facts']['method']['kpoints'] == {'grid': [8, 8, 8]}
    assert rows[1]['facts']['method']['potcar_symbols'] == ['PAW_PBE Si_sv']
    assert rows[2]['facts']['structure']['frame_identity'] == 'unverified'
    assert rows[2]['facts']['structure']['frame_index'] is None
    assert rows[0]['facts']['training_suitability'] == 'not_assessed'
    assert session.result(result['result_id'])['excluded'][0]['record_id'] == 'wrong-symmetry'


def test_large_arrays_are_bounded_explicitly_without_losing_full_detail(database):
    records, context, session = database
    record = records[0]
    small = asyncio.run(aidb_detail('si-pbe', context))
    record['nsites'] = 20000
    record['forces'] = [[0.1, 0.2, 0.3]] * 20000
    record['structure']['sites'] *= 10000
    large_forces = asyncio.run(aidb_detail('si-pbe', context))
    assert large_forces['records'][0]['facts']['labels']['forces']['shape'] == [20000, 3]
    assert len(json.dumps(large_forces)) < len(json.dumps(small)) + 100
    record['source_metadata']['vasp_facts']['method']['kpoints'] = {'points': [[0, 0, 0]] * 10000}
    record['calculation_parameters']['MAGMOM'] = [1] * 20000
    record['source_metadata']['vasp_facts']['source']['field_evidence']['notes'] = 'evidence ' * 10000
    result = asyncio.run(aidb_detail('si-pbe', context))
    facts = result['records'][0]['facts']
    points = facts['method']['kpoints']['points']
    assert points['truncated'] is True
    assert points['item_count'] == 10000
    assert len(points['items']) < 10000
    assert facts['method']['calculation_parameters']['MAGMOM']['item_count'] == 20000
    assert len(json.dumps(result)) < 12000
    assert result['facts_scope']['omitted_arrays'] == 'full_values_in_ui_snapshot'
    assert result['facts_scope']['comparison'] == 'not_exhaustive'
    assert session.result(result['result_id'])['record'] == record


def test_optional_field_availability_does_not_turn_omission_into_missing_data(database):
    records, context, session = database
    record = records[0]
    record.update(charge=[3.1] * 8, magmom=None, bandgap=0.6)
    saved = record['source_metadata']['vasp_facts']
    saved.update(paw_population_e_per_atom=[3.1] * 8, density_g_cm3=2.33, fermi_energy_ev=5.2)
    saved['source']['field_evidence']['charge'] = 'OUTCAR total charge: PAW sphere projection'
    for result in (asyncio.run(aidb_query('{"formula":"Si"}', context)),
                   asyncio.run(aidb_detail('si-pbe', context))):
        optional = result['records'][0]['facts']['optional_results']
        assert optional['charge']['status'] == 'available'
        assert optional['charge']['shape'] == [8]
        assert optional['charge']['evidence'] == 'OUTCAR total charge: PAW sphere projection'
        assert optional['charge']['oxidation_state'] == 'not_established'
        assert optional['paw_population_e_per_atom']['status'] == 'available'
        assert optional['paw_population_e_per_atom']['quantity'] == 'PAW_projected_electron_population'
        assert optional['magmom']['status'] == 'null'
        assert optional['magnetism'] == 'not_assessed'
        assert optional['dos']['status'] == 'not_provided'
        assert optional['bandgap']['value'] == 0.6
        assert optional['density_g_cm3']['value'] == 2.33
        assert optional['fermi_energy_ev']['value'] == 5.2
        assert result['facts_scope']['unlisted_fields'] == 'not_evidence_of_missing_or_not_applicable'
    before = len(json.dumps(result))
    record['charge'] *= 2500
    saved['paw_population_e_per_atom'] *= 2500
    large = asyncio.run(aidb_detail('si-pbe', context))
    assert large['records'][0]['facts']['optional_results']['charge']['shape'] == [20000]
    assert len(json.dumps(large)) < before + 100
    assert session.result(large['result_id'])['record']['charge'] == record['charge']
