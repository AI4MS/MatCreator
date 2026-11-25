"""Run the write boundary in the installed AIDB environment."""
import json
import os
from pathlib import Path
import sys


def same_frame_candidates(workspace, intended):
    expected_source = intended['source_metadata']['vasp_facts']['source']
    hashes = expected_source['sha256']
    submitted = expected_source.get('submitted_inputs')
    verified = []
    ambiguous = []
    for candidate in workspace.select_records({}):
        metadata = candidate.source_metadata
        source = (metadata.get('vasp_facts') or {}).get('source') or {}
        decoded = source.get('sha256') or {}
        files = source.get('file_sha256') or {}
        xml = decoded.get('vasprun.xml') or files.get('vasprun.xml') or metadata.get('sha256')
        outcar = decoded.get('OUTCAR') or files.get('OUTCAR')
        if xml != hashes['vasprun.xml'] or candidate.frame_index not in (None, intended['frame_index']):
            continue
        if submitted and source.get('submitted_inputs') != submitted:
            if not source.get('submitted_inputs'):
                ambiguous.append(candidate)
            continue
        if outcar == hashes['OUTCAR'] and candidate.frame_index == intended['frame_index']:
            verified.append(candidate)
        elif outcar is None or candidate.frame_index is None:
            ambiguous.append(candidate)
    return verified, ambiguous


def archive(config_path, ops, run):
    from ai_ready_db.ai_ready_db_Renaissance.local.local_workspace import LocalASEWorkspace
    from ai_ready_db.core.hashing import generate_record_id
    from ai_ready_db.core.normalization import normalize_json_record

    config, _ = ops.workspace_config(config_path, reading=False)
    raw = json.loads((run / 'verified-source.json').read_text(encoding='utf-8'))
    facts = raw['source_metadata']['vasp_facts']
    raw['functional'] = ops.vasp_method(raw['calculation_parameters'])
    pbe_header = raw.pop('pbe_header')
    if not raw['functional'] and pbe_header:
        raw['functional'] = 'PBE'
        facts['method']['functional_evidence'] = 'OUTCAR · 势头 LEXCH=PE；XML 未指定替代泛函'
    elif raw['functional']:
        facts['method']['functional_evidence'] = 'vasprun.xml · 实际运行参数'
    record = normalize_json_record(raw, 'local_dft')
    record.hubbard_u = raw['hubbard_u']
    record.record_id = generate_record_id(source_dataset=record.source_dataset, source_id=record.source_id,
        structure_hash=record.structure_hash, energy=record.energy, functional=record.functional,
        has_hubbard_u=record.has_hubbard_u, calculation_type=record.calculation_type)
    intended = ops.stored_details(record)
    ops.write_json(run / 'intended-record.json', intended)
    identity = {'record_id': record.record_id, 'source_id': record.source_id, 'frame_index': record.frame_index}
    storage = config['ase_sqlite']
    ops.write_json(run / 'write-attempt.json', {
        **identity, 'database': storage['path'], 'sidecar': storage['json_sidecar_path'],
        'state': 'unverified',
    })
    phase = 'lookup_failed'
    try:
        with ops.database_lock(config):
            storage = LocalASEWorkspace(config)
            existing = storage.get_record(record.record_id)
            if existing is not None:
                return {'status': 'existing', **identity}
            verified, ambiguous = same_frame_candidates(storage, intended)
            if len(verified) == 1:
                return {'status': 'existing', **identity, 'record_id': verified[0].record_id,
                        'legacy_identity': True}
            if verified or ambiguous:
                return {'status': 'source_unverified', **identity,
                        'candidate_ids': [candidate.record_id for candidate in verified + ambiguous],
                        'message': '已有记录的来源不能唯一核实，需要检查原记录后再保存'}
            phase = 'write_failed'
            written = storage.write_record(record, 'fine_tune_working', 'local_calculation', 'local_only')
    except Exception as exc:
        return {'status': phase, 'message': str(exc), **identity}
    if written != record.record_id:
        return {'status': 'write_failed', 'message': 'AIDB 写入回执返回了意外的记录 ID', **identity}
    return {'status': 'saved', **identity}


def main():
    script, config, run = sys.argv[1:]
    sys.path.insert(0, str(Path(script).parent))
    try:
        # Reuse installed AIDB discovery and use its own scientific Python environment.
        if os.environ.get('_MATCREATOR_AIDB_ARCHIVE') != '1':
            from aidb_client import discover_aidb_python, discover_aidb_root
            root = discover_aidb_root()
            python = discover_aidb_python(root)
            env = dict(os.environ, _MATCREATOR_AIDB_ARCHIVE='1', PYTHONDONTWRITEBYTECODE='1')
            env['PYTHONPATH'] = str(root) + os.pathsep + env.get('PYTHONPATH', '')
            os.execve(str(python), [str(python), '-B', str(Path(__file__).resolve()), *sys.argv[1:]], env)
        import aidb_ops
        result = archive(config, aidb_ops, Path(run))
    except Exception as exc:
        result = {'status': 'lookup_failed', 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
