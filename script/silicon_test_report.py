"""Pytest JSON reporting: counts and exact failure IDs, never a copied tally."""
import json
import os
from pathlib import Path

_results = []


def pytest_runtest_logreport(report):
    if report.when == 'call' or report.failed or report.skipped:
        kind = 'error' if report.failed and report.when != 'call' else report.outcome
        _results.append({'nodeid': report.nodeid, 'outcome': kind})


def pytest_collectreport(report):
    if report.failed or report.skipped:
        _results.append({'nodeid': report.nodeid, 'outcome': 'error' if report.failed else 'skipped'})


def pytest_sessionfinish(session, exitstatus):
    output = os.environ.get('MATCREATOR_TEST_REPORT')
    if output:
        counts = {kind: sum(r['outcome'] == kind for r in _results)
            for kind in ('passed', 'failed', 'error', 'skipped')}
        Path(output).write_text(json.dumps({'exit_code': int(exitstatus), 'counts': counts,
            'failures': [r for r in _results if r['outcome'] in {'failed', 'error'}]}, indent=2))
