#!/usr/bin/env python3
"""Repeatable local-loop regression and read-only persistent reuse acceptance."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parents[1]
LOOP_TESTS = ['tests/test_aidb_skill_hook.py', 'tests/test_silicon_vasp_workflow.py',
    'tests/test_silicon_archive.py', 'tests/test_silicon_reuse.py',
    'tests/test_silicon_lookup_consent.py', 'tests/test_silicon_checks.py']


class CheckError(ValueError):
    pass


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def require(condition, message):
    if not condition:
        raise CheckError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def emit(path, payload):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)
    print(json.dumps({'ok': payload.get('ok'), 'report_path': str(path),
        'error': payload.get('error')}, ensure_ascii=False))


def check_results(report, baseline=None):
    require(report['exit_code'] in (0, 1), f"pytest did not complete: exit {report['exit_code']}")
    counts = report['counts']
    require(all(type(counts[k]) is int and counts[k] >= 0 for k in ('passed', 'failed', 'error', 'skipped')), 'invalid test counts')
    require((report['exit_code'] == 0) == (counts['failed'] + counts['error'] == 0), 'pytest exit code contradicts failures')
    require(counts['passed'] > 0, 'no tests passed')
    require(counts['skipped'] == 0, 'unexpected skipped tests')
    actual = {(r['outcome'], r['nodeid']) for r in report['failures']}
    expected = {(r['outcome'], r['nodeid']) for r in (baseline or {}).get('failures', [])}
    new = sorted(actual - expected)
    require(not new, f'new failures: {new}')
    require(counts['failed'] + counts['error'] == len(report['failures']), 'failure tally inconsistent')
    return {'counts': counts, 'new_failures': new, 'resolved_baseline_failures': sorted(expected - actual),
        'baseline_used': baseline is not None,
        'all_tests_passed': counts['failed'] + counts['error'] == 0}


def runtime(args):
    root = Path(args.aidb_root or os.environ.get('AI_READY_DB_ROOT') or REPO.parent / 'Ai-ready_Database').expanduser().resolve()
    python = Path(args.aidb_python or os.environ.get('AI_READY_DB_PYTHON') or root / '.venv/bin/python').expanduser().absolute()
    scripts = root / 'skills/ai-ready-db/scripts'
    require(python.is_file(), f'aidb Python missing: {python}')
    require(all((scripts / name).is_file() for name in ('aidb_loop_adapter.py', 'aidb_client.py', 'aidb_server.py')),
        f'public bridge missing: {scripts}')
    env = {**os.environ, 'AI_READY_DB_ROOT': str(root), 'AI_READY_DB_PYTHON': str(python)}
    help_result = subprocess.run([str(python), str(scripts / 'aidb_loop_adapter.py'), 'preflight', '--help'],
        capture_output=True, text=True, env=env, timeout=20)
    require(help_result.returncode == 0 and '--command-timeout' in help_result.stdout,
        'aidb adapter lacks bounded preflight; use the pinned integration version')
    return root, python, env


def run_tests(args):
    _, _, env = runtime(args)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = output.with_suffix('.pytest.json')
    require(not report.exists(), f'refuse stale pytest report: {report}; choose a new output path')
    env.update(MATCREATOR_REQUIRE_AIDB='1', MATCREATOR_TEST_REPORT=str(report),
        PYTHONPATH=os.pathsep.join([str(REPO), str(REPO / 'src'), env.get('PYTHONPATH', '')]))
    command = [sys.executable, '-m', 'pytest', *( ['tests/'] if args.full else LOOP_TESTS),
        '-q', '--continue-on-collection-errors', '-p', 'script.silicon_test_report',
        '--junitxml', str(output.with_suffix('.xml'))]
    with output.with_suffix('.log').open('w', encoding='utf-8') as log:
        process = subprocess.run(command, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    require(report.exists(), f'pytest produced no report; inspect {output.with_suffix(".log")}')
    results = read_json(report)
    require(results['exit_code'] == process.returncode, 'pytest exit code mismatch')
    baseline = read_json(args.baseline) if args.baseline else None
    return {'ok': True, 'scope': 'full' if args.full else 'silicon-local-loop',
        **check_results(results, baseline), 'pytest_report': str(report),
        'junit': str(output.with_suffix('.xml')), 'log': str(output.with_suffix('.log'))}


def verify_reuse(args):
    root, python, env = runtime(args)
    # The bridge may initialize a missing store even during local-status.
    # Validate configuration paths before starting a supposedly read-only audit.
    config_path = Path(args.config or env.get('AI_READY_DB_CONFIG') or root / 'configs/base_database.yaml').resolve()
    import yaml
    config = yaml.safe_load(config_path.read_text(encoding='utf-8-sig'))
    store = Path(config['ase_sqlite']['path']).expanduser()
    if not store.is_absolute():
        store = root / store
    require(store.is_file(), f'existing local store required for read-only audit: {store}')
    env['AI_READY_DB_CONFIG'] = str(config_path)
    workspace = args.workspace.resolve()
    evidence = read_json(workspace / 'result.json')
    feedback = evidence['feedback']
    reuse = feedback['reuse']
    require(evidence.get('independent_session') is True, 'independent-session evidence absent')
    require(not evidence.get('guard_denied'), 'acceptance guard intervened; cannot claim natural reuse')
    require(feedback.get('query_executed') is True and reuse.get('status') == 'reused', 'current evidence is not successful reuse')
    require(reuse.get('new_calculation_needed') is False, 'receipt requests computation')
    require(len(set(reuse['record_ids'])) == len(reuse['record_ids']) > 0, 'invalid selected record identities')
    request = (workspace / 'request.txt').read_text(encoding='utf-8-sig')
    require(all(r not in request for r in reuse['record_ids']), 'request contains selected record ID')
    events = [json.loads(line) for line in (workspace / 'events.jsonl').read_text().splitlines() if line.strip()]
    responses = [p['function_response'] for e in events for p in (e.get('content') or {}).get('parts', []) if p.get('function_response')]
    require(any((r.get('response') or {}).get('aidb_preflight', {}).get('reuse', {}).get('record_ids') == reuse['record_ids'] for r in responses),
        'no matching live tool feedback in session events')
    messages = [e for e in events if (e.get('content') or {}).get('role') == 'model'
        and any(p.get('text') and not p.get('thought') for p in e['content'].get('parts', []))]
    require(bool(messages), 'final response absent')
    final = '\n'.join(p['text'] for p in messages[-1]['content']['parts'] if p.get('text') and not p.get('thought'))
    require(all(r in final for r in reuse['record_ids']), 'final response omits selected IDs')
    require('eV' in final, 'final response omits energy unit')
    # Scientific accuracy claims require reviewer judgement; no keyword test declares
    # a statement scientifically sound. Save the actual final response for it.
    source = read_json(args.source_report)
    stages = {step['archive']['record_id']: step for step in source['steps']}
    require(all(r in stages for r in reuse['record_ids']), 'selection is not in the source archive evidence')
    verification_dir = args.output.resolve().parent / ('reuse-check-' + uuid.uuid4().hex)
    verification_dir.mkdir(parents=True)
    task_path = verification_dir / 'task.json'
    task = feedback['task_context']
    task_path.write_text(json.dumps({'task_id': 'verify-persistent-reuse', 'target': {
        'formula': task['known']['formula'], 'functional': task['known']['functional'],
        'domain': task['known']['domain'], 'min_records': 1}}))
    if args.config:
        env['AI_READY_DB_CONFIG'] = str(args.config.resolve())
    command = [str(python), str(root / 'skills/ai-ready-db/scripts/aidb_loop_adapter.py'),
        '--workspace', str(verification_dir), 'preflight', '--task-json', str(task_path),
        '--command-timeout', '20', '--export-dir', str(verification_dir / 'export'), '--export-format', 'jsonl']
    process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=90)
    (verification_dir / 'adapter-result.json').write_text(json.dumps({'returncode': process.returncode,
        'stdout': process.stdout, 'stderr': process.stderr}, indent=2))
    require(process.returncode == 0, 'fresh public preflight failed; see adapter-result.json')
    receipt = json.loads(process.stdout)
    require(receipt.get('ok') is True, 'public preflight returned failure')
    query = read_json(receipt['report_path'])
    records = [json.loads(line) for export in query['exports'] for line in Path(export['output_path']).read_text().splitlines() if line.strip()]
    require(sorted(r['record_id'] for r in records) == sorted(query['queries'][0]['record_ids']), 'public export identities incomplete')
    sys.path.insert(0, str(REPO / 'src'))
    from matcreator.agents.silicon_reuse import select_reuse
    from matcreator.tools.silicon_vasp import _validate
    from matcreator.tools.silicon_archive import _compare
    selected = select_reuse(records, task)
    require(selected.get('status') == 'reused' and selected['record_ids'] == reuse['record_ids'],
        'fresh public selection changed; review current candidates')
    compared = []
    for record_id in selected['record_ids']:
        saved = next(r for r in records if r['record_id'] == record_id)
        step = stages[record_id]
        report_path = Path(step['source_result_json']['path'])
        require(digest(report_path) == step['source_result_json']['sha256'], 'source result report changed')
        report = read_json(report_path)
        require(report.get('status') == 'success', 'source scientific completion is not success')
        input_dir = Path(source['workspace']) / report['submission']['input_path']
        parsed = _validate(report_path.parent, step['calculation_type'], input_dir, report['conditions']['input_sha256'])
        expected = {'structure': parsed['structure'], 'energy': parsed['total_energy']['value'],
            'functional': 'PBE', 'calculation_type': step['calculation_type'],
            'calculation_parameters': parsed['conditions']['incar'], 'source_dataset': 'matcreator-bohrium',
            'source_id': step['job_id'], 'source_metadata': {'status': 'success', 'completion': parsed['completion'],
                'conditions': parsed['conditions'], 'energy_unit': 'eV', 'energy_basis': 'cell'}}
        _compare(saved, expected)
        previous = next(r for r in reuse['results'] if r['record_id'] == record_id)
        current = next(r for r in selected['results'] if r['record_id'] == record_id)
        require(previous == current, 'old tool receipt differs from current saved result')
        require(str(saved['energy']) in final, 'final response omits actual energy')
        archive = read_json(step['archive']['receipt_path'])
        require(archive.get('status') == 'verified' and archive['record_id'] == record_id, 'source archive unverified')
        for name, artifact in saved['source_metadata']['artifacts'].items():
            require(digest(report_path.parent / name) == artifact['sha256'], f'source artifact changed: {name}')
        compared.append({'record_id': record_id, 'job_id': step['job_id'], 'energy_eV': saved['energy']})
    from types import SimpleNamespace
    from matcreator.workspace import ADK_DIR
    from matcreator.agents.execution_agent.remote_job_tools import list_remote_jobs
    require((ADK_DIR / 'remote-jobs.db').is_file(), 'tracked-job store missing; cannot verify zero jobs')
    context = SimpleNamespace(state={'session_id': evidence['session_id']}, _invocation_context=SimpleNamespace(user_id=args.user_id))
    jobs = list_remote_jobs(context)
    require(jobs.get('status') == 'ok' and jobs['job_count'] == 0, 'acceptance session owns remote jobs')
    final_path = verification_dir / 'final-response.txt'
    final_path.write_text(final, encoding='utf-8')
    return {'ok': True, 'verification': 'fresh-public-readback-and-retained-real-artifacts',
        'session_id': evidence['session_id'], 'records': compared, 'new_jobs_in_session': 0,
        'cloud_database_upload': False, 'query_report': receipt['report_path'],
        'final_response': str(final_path), 'scientific_claim_review': 'required'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    test = sub.add_parser('tests', help='run isolated regression; fail on skips or new failures')
    test.add_argument('--full', action='store_true')
    test.add_argument('--baseline', type=Path)
    verify = sub.add_parser('verify-reuse', help='read back existing real records; never submit or archive')
    verify.add_argument('--workspace', type=Path, required=True)
    verify.add_argument('--source-report', type=Path, required=True)
    verify.add_argument('--config', type=Path)
    verify.add_argument('--user-id', default='user')
    compare = sub.add_parser('compare-results', help='validate a pytest JSON report without running tests')
    compare.add_argument('--pytest-report', type=Path, required=True)
    compare.add_argument('--baseline', type=Path)
    for command in (test, verify):
        command.add_argument('--aidb-root')
        command.add_argument('--aidb-python')
    for command in (test, verify, compare):
        command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'tests':
            payload = run_tests(args)
        elif args.command == 'verify-reuse':
            payload = verify_reuse(args)
        else:
            payload = {'ok': True, **check_results(read_json(args.pytest_report), read_json(args.baseline) if args.baseline else None)}
    except (CheckError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        emit(args.output, {'ok': False, 'error': str(exc)})
        return 1
    emit(args.output, payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
