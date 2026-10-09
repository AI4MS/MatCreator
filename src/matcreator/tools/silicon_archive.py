"""Local Si result round trips through the aidb Skill's public adapter only."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from google.adk.tools.tool_context import ToolContext
from monty.json import MontyEncoder


def _write(path, payload):
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    temporary.write_text(json.dumps(payload, cls=MontyEncoder, indent=2), encoding='utf-8')
    temporary.replace(path)


def _compare(saved, source):
    from pymatgen.core import Structure
    from .silicon_vasp import _same_structure
    source = json.loads(json.dumps(source, cls=MontyEncoder))
    mismatches = []
    if not _same_structure(Structure.from_dict(saved['structure']), Structure.from_dict(source['structure'])):
        mismatches.append('structure')
    for key in ('source_dataset', 'source_id', 'energy', 'functional', 'calculation_type', 'calculation_parameters'):
        if saved.get(key) != source.get(key):
            mismatches.append(key)
    metadata = saved.get('source_metadata') or {}
    for key, value in source['source_metadata'].items():
        if metadata.get(key) != value:
            mismatches.append(f'source_metadata.{key}')
    if not metadata.get('archived_at'):
        mismatches.append('source_metadata.archived_at')
    if mismatches:
        raise ValueError('Archived content differs from verified output: ' + ', '.join(mismatches))


def archive_verified_result(report: dict, tool_context: ToolContext) -> dict:
    """Archive a freshly scientifically verified handoff; never submit a job.

    Query/export before each write, including recovery after an uncertain
    timeout. Never overwrite a differing record for the same tracked job.
    """
    from matcreator.agents.aidb_skill_hook import _adapter_path
    workspace = Path(tool_context.state['workspace_dir']).resolve()
    directory = workspace / '.aidb' / 'archives' / report['job_id']
    directory.mkdir(parents=True, exist_ok=True)
    attempt = directory / uuid.uuid4().hex
    attempt.mkdir()
    receipt = {'status': 'failed', 'archived': False, 'job_id': report['job_id'],
        'source_report_path': str(Path(report['output_path']) / 'silicon-result.json'),
        'receipt_path': str(attempt / 'receipt.json'), 'cloud_upload': False}
    try:
        if report.get('status') != 'success':
            raise ValueError('Scientific success is required before local archive.')
        # This Unix lock matches the supported Unix-socket aidb bridge. It also
        # prevents concurrent completion callbacks from racing the same write.
        import fcntl
        with (directory / 'archive.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            task_path = attempt / 'task.json'
            _write(task_path, {'task_id': f"silicon-{report['job_id']}", 'target': {
                'formula': 'Si', 'functional': 'PBE', 'domain': 'bulk', 'min_records': 1},
                'related_materials': []})
            adapter = _adapter_path()

            def command(stage, arguments):
                env = {**os.environ, 'AI_READY_DB_BRIDGE_TIMEOUT': '20', 'AI_READY_DB_BRIDGE_STARTUP_TIMEOUT': '15'}
                try:
                    proc = subprocess.run([sys.executable, str(adapter), '--workspace', str(workspace),
                        *arguments], capture_output=True, text=True, env=env, timeout=75)
                except subprocess.TimeoutExpired as exc:
                    receipt['write_completion_unknown'] = stage == 'archive'
                    _write(attempt / f'{stage}.json', {'timed_out': True, 'stage': stage,
                        'stdout': str(exc.stdout or ''), 'stderr': str(exc.stderr or '')})
                    raise
                _write(attempt / f'{stage}.json', {'returncode': proc.returncode,
                    'stdout': proc.stdout, 'stderr': proc.stderr})
                payload = json.loads(proc.stdout)
                receipt[f'{stage}_audit_path'] = payload.get('audit_path')
                receipt[f'{stage}_report_path'] = payload.get('report_path')
                if proc.returncode or payload.get('ok') is not True:
                    raise ValueError(f'{stage} failed: {payload}')
                return payload

            preflight = command('recovery_query', ['preflight', '--task-json', str(task_path),
                '--command-timeout', '20', '--export-dir', str(attempt / 'before'), '--export-format', 'jsonl'])
            query = json.loads(Path(preflight['report_path']).read_text())
            records = []
            for export in query['exports']:
                records.extend(json.loads(line) for line in Path(export['output_path']).read_text().splitlines() if line.strip())
            existing = [r for r in records if r.get('source_dataset') == 'matcreator-bohrium'
                and r.get('source_id') == report['job_id']]
            if len(existing) > 1:
                raise ValueError('Multiple archive identities for this tracked job; resolve conflict before writing.')
            conditions = report['conditions']
            metadata = {'job_id': report['job_id'], 'batchjob_id': report['batchjob_id'],
                'session_id': str(tool_context.state.get('session_id') or ''),
                'status': 'success', 'completion': report['completion'], 'energy_unit': report['total_energy']['unit'],
                'energy_basis': 'cell', 'conditions': conditions, 'artifacts': report['artifacts'],
                'submission': report['submission'], 'output_path': report['output_path'],
                'relaxation_source': report.get('relaxation_source'),
                'parent_record_id': None, 'origin': 'local_calculation', 'sync_status': 'local_only'}
            parent = report.get('relaxation_source')
            if parent:
                parents = [r for r in records if r.get('source_dataset') == 'matcreator-bohrium'
                    and r.get('source_id') == parent['job_id'] and r.get('calculation_type') == 'relaxation']
                if len(parents) != 1:
                    raise ValueError('Archive and verify preceding relaxation before static result.')
                from matcreator.agents.execution_agent.remote_job_tools import _service
                from .silicon_vasp import _validate, _input_directory
                parent_spec = _service().store.get_job(parent['job_id'])['specification']
                parent_report = _validate(Path(parent['output_path']), 'relaxation',
                    _input_directory(parent_spec, workspace), parent_spec.get('vasp_input_sha256', {}))
                parent_source = {'source_dataset': 'matcreator-bohrium', 'source_id': parent['job_id'],
                    'structure': parent_report['structure'], 'energy': parent_report['total_energy']['value'],
                    'functional': 'PBE', 'calculation_type': 'relaxation',
                    'calculation_parameters': parent_report['conditions']['incar'],
                    'source_metadata': {'conditions': parent_report['conditions'], 'status': 'success',
                        'completion': parent_report['completion'], 'energy_unit': 'eV', 'job_id': parent['job_id']}}
                _compare(parents[0], parent_source)
                metadata['parent_record_id'] = parents[0]['record_id']
            source = {'source_dataset': 'matcreator-bohrium', 'source_id': report['job_id'],
                'structure': report['structure'], 'energy': report['total_energy']['value'],
                'functional': 'PBE', 'calculation_type': report['calculation_type'],
                'calculation_parameters': conditions['incar'], 'source_metadata': metadata,
                'provenance': {'provider': 'bohr_batchjob', 'job_id': report['job_id'], 'batchjob_id': report['batchjob_id']}}
            # Normalize JSON exactly as it travels through the public contract.
            source = json.loads(json.dumps(source, cls=MontyEncoder))
            if existing:
                _compare(existing[0], source)
                saved = existing[0]
                export_path = attempt / 'existing.jsonl'
                _write(export_path, saved)
                receipt['previously_present'] = True
            else:
                record_path = directory / 'record.json'
                if record_path.exists():
                    pending = json.loads(record_path.read_text())
                    _compare(pending, source)
                source['source_metadata']['archived_at'] = datetime.now(timezone.utc).isoformat()
                _write(record_path, source)
                archived = command('archive', ['archive-and-requery', '--task-json', str(task_path),
                    '--record-json', str(record_path), '--origin', 'local_calculation', '--sync-status', 'local_only',
                    '--export-dir', str(attempt / 'after'), '--export-format', 'jsonl'])
                receipt['record_id'] = archived['record_id']
                export_path = Path(archived['export_path'])
                readback = [json.loads(line) for line in export_path.read_text().splitlines() if line.strip()]
                if len(readback) != 1 or readback[0]['record_id'] != archived['record_id']:
                    raise ValueError('Archive export does not contain exactly the returned record ID.')
                saved = readback[0]
                _compare(saved, source)
                receipt['previously_present'] = False
            receipt.update(status='verified', archived=True, record_id=saved['record_id'],
                export_path=str(export_path), archived_at=saved['source_metadata']['archived_at'],
                verified_in_requery=True, content_compared=True)
    except Exception as exc:
        receipt['error'] = str(exc) or type(exc).__name__
        receipt['message'] = 'Local archive unverified; retain outputs and retry lookup before writing. Do not resubmit calculation.'
    _write(attempt / 'receipt.json', receipt)
    return receipt


def archive_silicon_vasp_result(job_id: str, destination_path: str, calculation_type: str,
    tool_context: ToolContext, relaxation_job_id: str = '') -> dict:
    """Retry local archive of an existing Si job after rechecking original outputs.

    Does not submit any computation or upload to a cloud database.
    """
    from .silicon_vasp import collect_silicon_vasp_result
    collected = collect_silicon_vasp_result(job_id, destination_path, calculation_type, tool_context, relaxation_job_id)
    if collected.get('status') != 'success':
        return collected
    report = json.loads(Path(collected['report_path']).read_text())
    archive = archive_verified_result(report, tool_context)
    return {**collected, 'archived': archive['archived'], 'local_archive': archive,
        'message': 'Scientific result verified; local archive ' + archive['status'] + '.'}
