"""Active archive acceptance through the session and installed database boundaries."""
import asyncio
import gzip
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from matcreator.aidb.service import InstalledSkillQuery, SessionAIDB


FIXTURES = Path(__file__).resolve().parent / 'fixtures/vasp'
STATIC = FIXTURES / 'nacl-static'
RELAX = FIXTURES / 'si-pbe-relax'
INDEPENDENT_RELAX = FIXTURES / 'si-r2scan-relax'
MD = FIXTURES / 'si-md-1'


@pytest.fixture
def isolated_session(tmp_path):
    from ase.db import connect

    installed = Path.home() / '.matcreator/skills/ai-ready-db/scripts/aidb_ops.py'
    if not installed.is_file():
        pytest.skip('Install ai-ready-db for the real isolated archive test')
    workdir = tmp_path / 'workspace'
    workdir.mkdir()
    database = str(tmp_path / 'archive.db')
    sidecar = tmp_path / 'archive.sidecar.json'
    sidecar.write_text('{"records": {}}', encoding='utf-8')
    with connect(database) as db:
        db.count()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'database': {'backend': 'ase_sqlite'}, 'ase_sqlite': {
        'path': database, 'json_sidecar_path': str(sidecar), 'max_query_keys_per_row': 64,
    }}), encoding='utf-8')
    session = SessionAIDB(workdir, 'archive-test', query_database=InstalledSkillQuery(installed))
    session.bind(config)
    return session


def test_static_archive_reads_back_energy_forces_and_tensor_units(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(STATIC, isolated_session.workspace / 'nacl-static')
    outcome = archive_vasp_result(isolated_session, 'nacl-static')
    assert outcome['status'] == 'saved', outcome
    assert outcome['readback_verified'] is True
    record = isolated_session.detail(outcome['record_id'])['record']
    assert record['calculation_type'] == 'static'
    assert record['energy'] == -27.96859470
    assert record['frame_index'] == 0
    assert len(record['forces']) == 8
    assert record['stress_unit'] == 'eV/Angstrom^3'
    assert record['stress_convention'] == 'tensile_positive'
    assert record['virial_unit'] == 'eV'
    assert record['source_metadata']['vasp_facts']['labels']['ionic_converged'] is None


def test_md_archive_reads_back_one_electronically_converged_final_frame(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(MD, isolated_session.workspace / 'si-md')
    outcome = archive_vasp_result(isolated_session, 'si-md')
    assert outcome['status'] == 'saved', outcome
    detail = isolated_session.detail(outcome['record_id'])
    record = detail['record']
    saved = record['source_metadata']['vasp_facts']
    assert record['calculation_type'] == 'molecular_dynamics'
    assert record['frame_index'] == 2
    assert record['energy'] == -10.85400315
    assert record['forces'][0] == pytest.approx([-0.020744, 0.158652, 0.087005], abs=5e-7)
    assert record['stress_unit'] == 'eV/Angstrom^3'
    assert record['virial_unit'] == 'eV'
    assert saved['labels']['energy_definition'] == 'e_0_energy'
    assert saved['labels']['ionic_converged'] is None
    assert [site['label'] for site in record['structure']['sites']] == ['Si', 'Si']
    assert record['structure']['lattice']['matrix'][0] == pytest.approx([3.348898, 0, 1.933487])
    assert record['structure']['sites'][0]['abc'] == pytest.approx([0.2509247, 0.24796294, 0.24970717])
    assert saved['initial_structure']['structure']['sites'][0]['abc'] == [0.25, 0.25, 0.25]
    sampling = saved['sampling']
    assert sampling['ensemble'] == 'NVT'
    assert sampling['target_start_kelvin'] == sampling['target_end_kelvin'] == 300
    assert sampling['observed_temperature_kelvin'] == 289.76
    assert sampling['step_fs'] == 1
    assert sampling['source_ionic_steps'] == sampling['planned_ionic_steps'] == 3
    assert sampling['stored_scope'] == 'final_frame'
    assert isolated_session.query({'source_id': outcome['source_id']})['matched_count'] == 1


def test_relaxation_repacked_into_another_directory_is_the_same_verified_result(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    first = isolated_session.workspace / 'original'
    first.mkdir()
    second = isolated_session.workspace / 'copy'
    second.mkdir()
    for name in ('vasprun.xml', 'OUTCAR'):
        shutil.copyfile(RELAX / name, first / name)
        (second / (name + '.gz')).write_bytes(gzip.compress((RELAX / name).read_bytes()))

    saved = archive_vasp_result(isolated_session, first)
    existing = archive_vasp_result(isolated_session, second)
    assert (saved['status'], existing['status']) == ('saved', 'existing'), (saved.get('message'), existing.get('message'))
    assert saved['record_id'] == existing['record_id']
    assert existing['readback_verified'] is True
    found = isolated_session.query({'formula': 'Si'})
    assert found['matched_count'] == 1
    [record] = found['records']
    assert record['energy'] == -43.38227814
    assert record['frame_index'] == 2
    assert record['source_metadata']['vasp_facts']['labels']['ionic_converged'] is True


def test_compressed_source_reports_verified_frame_in_normal_query(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(STATIC, isolated_session.workspace / 'nacl-static')
    outcome = archive_vasp_result(isolated_session, 'nacl-static')
    assert outcome['status'] == 'saved', outcome
    assert isolated_session.query({'source_id': outcome['source_id']})['coverage']['verified_frames'] == 1


def test_normal_chat_and_executor_offer_workspace_archive_tool():
    from matcreator.agents.thinking_agent.agent import thinking_agent
    from matcreator.agents.execution_agent.step_executor import build_step_executor_agent
    from matcreator.llm_cards import LLMCard

    card = LLMCard(name='test', model='openai/test', base_url='http://localhost', api_key='test')
    for agent in (thinking_agent, build_step_executor_agent(card)):
        tool = next((tool for tool in agent.tools if getattr(tool, 'name', None) == 'aidb_archive_vasp'), None)
        assert tool is not None
        declaration = tool._get_declaration()
        schema = declaration.parameters_json_schema or declaration.parameters.model_dump()
        assert set(schema['properties']) == {'directories'}


def test_new_session_retries_same_write_after_installed_query_fails(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result
    from matcreator.aidb.service import AIDBError

    shutil.copytree(STATIC, isolated_session.workspace / 'nacl-static')
    installed = isolated_session.query_database

    class InterruptedQuery(InstalledSkillQuery):
        def __call__(self, config, workspace, conditions):
            raise AIDBError('injected database read failure')

    isolated_session.query_database = InterruptedQuery(installed.script_path())
    failed = archive_vasp_result(isolated_session, 'nacl-static')
    assert failed['status'] == 'readback_failed', failed
    assert failed['database_status'] == 'saved'
    assert failed['readback_verified'] is False
    assert 'result' not in failed
    assert json.loads(Path(failed['archive_receipt']).read_text()) == failed

    fresh_session = SessionAIDB(isolated_session.workspace, isolated_session.session_id, query_database=installed)
    retried = archive_vasp_result(fresh_session, 'nacl-static')
    assert retried['status'] == 'existing', retried
    assert retried['record_id'] == failed['record_id']
    assert fresh_session.query({'elements': ['Na', 'Cl']})['matched_count'] == 1


def test_readback_field_mismatch_keeps_receipt_without_successful_result(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(STATIC, isolated_session.workspace / 'nacl-static')

    class DisagreeingQuery(InstalledSkillQuery):
        def __call__(self, config, workspace, conditions):
            records, report = super().__call__(config, workspace, conditions)
            records[0]['energy'] = 42
            return records, report

    isolated_session.query_database = DisagreeingQuery(isolated_session.query_database.script_path())
    outcome = archive_vasp_result(isolated_session, 'nacl-static')
    assert outcome['status'] == 'readback_mismatch', outcome
    assert 'energy' in outcome['mismatches']
    assert outcome['record_id']
    assert 'result' not in outcome
    assert outcome['readback_verified'] is False


def test_write_failure_can_retry_against_original_database(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(STATIC, isolated_session.workspace / 'nacl-static')
    config = json.loads(Path(isolated_session.binding()['config_path']).read_text())
    database = Path(config['ase_sqlite']['path'])
    with sqlite3.connect(database) as db:
        db.execute("CREATE TRIGGER stop_archive BEFORE INSERT ON systems BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
    failed = archive_vasp_result(isolated_session, 'nacl-static')
    assert failed['status'] == 'write_failed', failed
    assert not failed['readback_verified']
    assert failed['record_id']
    assert isolated_session.query({'record_id': failed['record_id']})['matched_count'] == 0

    with sqlite3.connect(database) as db:
        db.execute('DROP TRIGGER stop_archive')
    saved = archive_vasp_result(isolated_session, 'nacl-static')
    assert saved['status'] == 'saved', saved
    assert saved['record_id'] == failed['record_id']


def test_distinct_completed_si_runs_keep_distinct_records(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(RELAX, isolated_session.workspace / 'pbe')
    shutil.copytree(INDEPENDENT_RELAX, isolated_session.workspace / 'r2scan')
    results = [archive_vasp_result(isolated_session, directory) for directory in ('pbe', 'r2scan')]
    assert [result['status'] for result in results] == ['saved', 'saved'], [result.get('message') for result in results]
    assert len({result['record_id'] for result in results}) == 2
    assert isolated_session.query({'formula': 'Si'})['matched_count'] == 2


def test_same_labels_and_inputs_are_not_a_run_identity(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    # Replay an otherwise identical calculation at a different recorded start time.
    first, second = (isolated_session.workspace / name for name in ('original', 'another-run'))
    for folder in (first, second):
        shutil.copytree(RELAX, folder)
    xml, outcar = second / 'vasprun.xml', second / 'OUTCAR'
    for path in (xml, outcar):
        original = path.read_bytes()
        replay = original.replace(b'08:36:02', b'08:36:03', 1)
        assert replay != original
        path.write_bytes(replay)
    first_result = archive_vasp_result(isolated_session, first)
    second_result = archive_vasp_result(isolated_session, second)
    assert first_result['status'] == second_result['status'] == 'saved'
    assert first_result['record_id'] != second_result['record_id']
    records = isolated_session.query({'formula': 'Si'})['records']
    assert len(records) == 2
    assert records[0]['energy'] == records[1]['energy'] == -43.38227814
    assert records[0]['calculation_parameters'] == records[1]['calculation_parameters']


def test_verified_existing_frame_with_an_old_record_id_is_reused(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    # Seed the old source identity and hash fields through the installed AIDB API.
    from matcreator.aidb.vasp_archive import parse_vasp_result

    raw = parse_vasp_result(RELAX)
    facts = raw['source_metadata']['vasp_facts']
    source = facts['source']
    raw['source_id'] = source['sha256']['vasprun.xml']
    source['file_sha256'] = source.pop('sha256')
    raw['functional'] = 'PBE'
    raw.pop('pbe_header')
    config = isolated_session.binding()['config_path']
    ops = isolated_session.query_database.script_path()
    seed = FIXTURES.parent / 'seed_legacy_record.py'
    subprocess.run([sys.executable, '-B', str(seed), str(ops), config],
                   input=json.dumps(raw), text=True, check=True)
    [old] = isolated_session.query({'formula': 'Si'})['records']
    shutil.copytree(RELAX, isolated_session.workspace / 'relax')
    outcome = archive_vasp_result(isolated_session, 'relax')
    assert outcome['status'] == 'existing', outcome
    assert outcome['record_id'] == old['record_id']
    assert isolated_session.query({'formula': 'Si'})['matched_count'] == 1


def test_matching_optional_input_files_are_reported_with_their_evidence(isolated_session):
    from matcreator.aidb.archive import archive_vasp_result

    shutil.copytree(RELAX, isolated_session.workspace / 'relax')
    outcome = archive_vasp_result(isolated_session, 'relax')
    assert outcome['status'] == 'saved', outcome
    record = isolated_session.detail(outcome['record_id'])['record']
    evidence = record['source_metadata']['vasp_facts']['source']['workflow_inputs']
    assert evidence['KPOINTS']['sha256'] == 'da5d84a1241d7b98a313421d4b5d7850f2b4d48524544bc0df34376425db30c1'
    assert evidence['KPOINTS']['verified'] is True
    assert evidence['INCAR']['verified'] is False
    assert evidence['INCAR']['unverified_tags'] == ['NCORE']


@pytest.mark.parametrize(('scenario', 'status'), [
    ('mismatch', 'source_conflict'), ('xml-truncated', 'invalid_output'),
    ('outcar-truncated', 'invalid_output'), ('electronic', 'not_converged'), ('ionic', 'not_converged'),
])
def test_invalid_or_unconverged_source_leaves_database_empty(isolated_session, scenario, status):
    from matcreator.aidb.archive import archive_vasp_result

    folder = isolated_session.workspace / scenario
    shutil.copytree(RELAX, folder)
    xml, outcar = folder / 'vasprun.xml', folder / 'OUTCAR'
    if scenario == 'mismatch':
        outcar.write_bytes(gzip.decompress((STATIC / 'OUTCAR.gz').read_bytes()))
    elif scenario == 'xml-truncated':
        xml.write_bytes(xml.read_bytes()[:-3000])
    elif scenario == 'outcar-truncated':
        outcar.write_bytes(outcar.read_bytes()[:-3000])
    elif scenario == 'electronic':
        outcar.write_bytes(outcar.read_bytes().replace(b'aborting loop because EDIFF is reached', b'iteration limit reached'))
    else:
        outcar.write_bytes(outcar.read_bytes().replace(b'reached required accuracy - stopping structural energy minimisation',
                                                      b'ionic convergence not reached'))
    receipt = archive_vasp_result(isolated_session, folder)
    assert receipt['status'] == status, receipt
    assert 'record_id' not in receipt
    assert isolated_session.query({'formula': 'Si'})['matched_count'] == 0


def test_mixed_chat_archive_reports_each_item_and_reuses_saved_md(isolated_session):
    from matcreator.tools.aidb_archive_tools import aidb_archive_vasp

    for name, fixture in [('relax', RELAX), ('static', STATIC), ('md', MD)]:
        shutil.copytree(fixture, isolated_session.workspace / name)
    context = SimpleNamespace(state={'session_id': 'archive-test', 'workdir': str(isolated_session.workspace)})
    first = asyncio.run(aidb_archive_vasp(['md'], context))['items'][0]
    batch = asyncio.run(aidb_archive_vasp(['relax', 'missing', 'static', 'md', ''], context))
    assert batch['status'] == 'batch_result'
    assert [(item['source_directory'], item['status']) for item in batch['items']] == [
        ('relax', 'saved'), ('missing', 'invalid_output'), ('static', 'saved'), ('md', 'existing'),
        ('', 'invalid_output'),
    ]
    assert batch['summary'] == {'saved': 2, 'existing': 1, 'failed': 2, 'unverified': 0}
    assert batch['items'][3]['record_id'] == first['record_id']
    retry = asyncio.run(aidb_archive_vasp(['relax', 'static', 'md'], context))
    assert [item['record_id'] for item in retry['items']] == [
        batch['items'][index]['record_id'] for index in (0, 2, 3)
    ]
    assert retry['summary'] == {'saved': 0, 'existing': 3, 'failed': 0, 'unverified': 0}
    for item in retry['items']:
        matches = isolated_session.query({'source_id': item['source_id']})
        assert matches['matched_count'] == 1


@pytest.mark.parametrize('scenario', ['early-stop', 'scf-failed', 'missing-temperature'])
def test_md_completion_and_missing_evidence_are_distinct(isolated_session, scenario):
    from matcreator.aidb.archive import archive_vasp_result
    from matcreator.tools.aidb_tools import aidb_detail

    folder = isolated_session.workspace / scenario
    shutil.copytree(MD, folder)
    outcar = folder / 'OUTCAR.gz'
    output = gzip.decompress(outcar.read_bytes())
    if scenario == 'early-stop':
        xml = folder / 'vasprun.xml.gz'
        output = output.replace(b'NSW = 3', b'NSW = 4').replace(b'NSW    =      3', b'NSW    =      4')
        content = gzip.decompress(xml.read_bytes())
        before, _, runtime = content.partition(b'<parameters>')
        runtime = runtime.replace(b'<i type="int" name="NSW">     3</i>',
                                  b'<i type="int" name="NSW">     4</i>')
        assert b'name="NSW">     4</i>' in runtime
        xml.write_bytes(gzip.compress(before + b'<parameters>' + runtime))
    elif scenario == 'scf-failed':
        head, marker, tail = output.rpartition(b'aborting loop because EDIFF is reached')
        assert marker
        output = head + b'iteration limit reached' + tail
    else:
        output = output.replace(b'(temperature  289.76 K)', b'(temperature unavailable)')
    outcar.write_bytes(gzip.compress(output))
    receipt = archive_vasp_result(isolated_session, scenario)
    if scenario != 'missing-temperature':
        assert receipt['status'] == ('invalid_output' if scenario == 'early-stop' else 'not_converged'), receipt
        assert 'record_id' not in receipt
        assert isolated_session.query({'formula': 'Si'})['matched_count'] == 0
        return
    assert receipt['status'] == 'saved', receipt
    context = SimpleNamespace(state={'session_id': 'archive-test', 'workdir': str(isolated_session.workspace)})
    detail = asyncio.run(aidb_detail(receipt['record_id'], context))
    sampling = detail['records'][0]['facts']['sampling']
    assert sampling['planned_ionic_steps'] == 3
    assert sampling['source_ionic_steps'] == 3
    assert sampling['observed_temperature_kelvin'] is None
    assert sampling['unknown_reasons']['observed_temperature_kelvin'] == 'OUTCAR 末步缺少唯一可核实的瞬时温度'
