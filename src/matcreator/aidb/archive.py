"""Save and verify an existing result against the session's bound AIDB."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from matcreator.aidb.service import AIDBError, _save
from matcreator.aidb.vasp_archive import parse_vasp_result


READBACK_FIELDS = (
    'record_id', 'source_dataset', 'source_id', 'frame_index', 'structure',
    'calculation_type', 'functional', 'calculation_parameters', 'hubbard_u',
    'energy', 'energy_per_atom', 'forces', 'stress', 'stress_unit', 'stress_convention',
    'virial', 'virial_unit', 'magmom', 'charge', 'provenance',
)


def readback_mismatches(actual, expected, *, legacy_identity, existing):
    if legacy_identity:
        fields = tuple(key for key in READBACK_FIELDS
                       if key not in {'record_id', 'source_dataset', 'source_id', 'provenance'})
    else:
        fields = READBACK_FIELDS
    mismatches = [key for key in fields if actual.get(key) != expected.get(key)]
    actual_meta = actual.get('source_metadata') or {}
    expected_meta = expected['source_metadata']
    actual_facts = actual_meta.get('vasp_facts') or {}
    expected_facts = expected_meta['vasp_facts']
    actual_source = actual_facts.get('source') or {}
    expected_source = expected_facts['source']
    if legacy_identity:
        if expected_source.get('submitted_inputs') and (
                actual_source.get('submitted_inputs') != expected_source['submitted_inputs']
                or actual_source.get('run_fingerprint') != expected_source['run_fingerprint']):
            mismatches.append('source_metadata.vasp_facts.source.submitted_inputs')
        hashes = {**(actual_source.get('file_sha256') or {}), **(actual_source.get('sha256') or {})}
        if any(hashes.get(name) != digest for name, digest in expected_source['sha256'].items()):
            mismatches.append('source_metadata.vasp_facts.source.sha256')
        # Historical rows may record fewer optional facts. Check the completion and label evidence they provide.
        for key in ('energy_definition', 'energy_unit', 'force_unit', 'electronic_converged', 'ionic_converged'):
            if (actual_facts.get('labels') or {}).get(key) != expected_facts['labels'][key]:
                mismatches.append('source_metadata.vasp_facts.labels.' + key)
        return mismatches
    # Repackaging and copies preserve decoded hashes. Optional input files can differ across copies.
    optional_source_fields = {'file_sha256', 'workflow_inputs'} if existing else set()
    actual_facts = {**actual_facts, 'source': {k: v for k, v in actual_source.items() if k not in optional_source_fields}}
    expected_facts = {**expected_facts, 'source': {k: v for k, v in expected_source.items() if k not in optional_source_fields}}
    if actual_facts != expected_facts:
        mismatches.append('source_metadata.vasp_facts')
    mismatches.extend(f'source_metadata.{key}' for key, value in expected_meta.items()
                      if key != 'vasp_facts' and actual_meta.get(key) != value)
    return mismatches


def archive_vasp_result(session, directory, *, owner_id=None):
    """Validate one workspace result, write once, and independently reread it by ID."""
    run = session.root / 'archive' / uuid4().hex
    receipt = {'operation': 'archive', 'source_directory': str(directory),
               'archive_receipt': str(run / 'receipt.json'), 'readback_verified': False}

    def finish(status, **fields):
        result = {**receipt, **fields, 'status': status}
        _save(run / 'receipt.json', result)
        return result

    try:
        if not str(directory).strip():
            raise AIDBError('未指定 VASP 结果目录', 'invalid_output')
        binding = session.binding()
        receipt['binding_id'] = binding['binding_id']
        folder = (session.workspace / directory).resolve(strict=True)
        if not folder.is_dir() or not folder.is_relative_to(session.workspace):
            raise AIDBError('请选择当前工作区中的 VASP 结果目录', 'invalid_output')
        from matcreator.aidb.managed_vasp import workspace_managed_vasp_source

        raw = workspace_managed_vasp_source(session, owner_id, folder) if owner_id else None
        if raw is None:
            raw = parse_vasp_result(folder)
    except (AIDBError, OSError, ValueError) as exc:
        return finish(getattr(exc, 'status', 'invalid_output'), message=str(exc))

    return archive_verified_vasp_result(session, raw, binding_id=binding['binding_id'], run=run, receipt=receipt,
                                        publish=True)


def archive_verified_vasp_result(session, raw, *, binding_id, run=None, receipt=None, publish=False):
    """Use the same AIDB write and independent readback for an already verified VASP frame."""
    run = run or session.root / 'archive' / uuid4().hex
    receipt = receipt or {'operation': 'archive', 'archive_receipt': str(run / 'receipt.json'),
                          'binding_id': binding_id, 'readback_verified': False}

    def finish(status, **fields):
        result = {**receipt, **fields, 'status': status}
        _save(run / 'receipt.json', result)
        return result

    receipt.update(source_id=raw['source_id'], frame_index=raw['frame_index'],
                   calculation_type=raw['calculation_type'])
    try:
        binding = session.binding(binding_id)
        _save(run / 'verified-source.json', raw)
        script = session.query_database.script_path()
    except (AIDBError, OSError) as exc:
        return finish('lookup_failed', message=str(exc))
    command = [sys.executable, '-B', str(Path(__file__).with_name('archive_database.py')),
               str(script), binding['config_path'], str(run)]
    try:
        proc = subprocess.run(command, cwd=run, text=True, capture_output=True, timeout=60, check=True)
        database_receipt = json.loads(proc.stdout)
        status = database_receipt['status']
        if status not in ('saved', 'existing', 'write_failed', 'lookup_failed', 'source_unverified'):
            raise ValueError('未知写入状态')
        record_id = database_receipt.get('record_id')
        if status in ('saved', 'existing') and (not record_id or database_receipt.get('source_id') != raw['source_id']):
            raise ValueError('写入回执与已核实的来源不一致')
        receipt['record_id'] = record_id
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        return finish('write_unverified', message=f'未能核实 AIDB 写入回执：{exc}')
    if status == 'source_unverified':
        return finish(status, message=database_receipt.get('message'), candidate_ids=database_receipt['candidate_ids'])
    if status in ('write_failed', 'lookup_failed'):
        return finish(status, message=database_receipt.get('message'))
    receipt['database_status'] = status

    try:
        detail = session.detail(record_id, binding_id=binding['binding_id'])
        expected = json.loads((run / 'intended-record.json').read_text(encoding='utf-8'))
        actual = detail['record']
        _save(run / 'readback.json', actual)
    except (AIDBError, OSError, ValueError) as exc:
        return finish('readback_failed', message=f'AIDB 写入状态尚未完成按 ID 核实：{exc}')
    mismatches = readback_mismatches(actual, expected, legacy_identity=database_receipt.get('legacy_identity') is True,
                                     existing=status == 'existing')
    if mismatches:
        return finish('readback_mismatch', mismatches=mismatches,
                      message='按 ID 读回内容与原始结果不一致')
    if not publish:
        return finish(status, readback_verified=True)
    try:
        result = session.publish({key: value for key, value in detail.items() if key != 'expanded_record_id'})
    except (AIDBError, OSError, ValueError) as exc:
        return finish('readback_failed', message=f'AIDB 已读回，但保存结果回执失败：{exc}')
    return finish(status, readback_verified=True, result=result)
