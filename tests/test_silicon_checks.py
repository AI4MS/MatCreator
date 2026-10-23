"""Command-level checks of the regression guard; no database or remote jobs."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'script/check_silicon_loop.py'


def compare(tmp_path, counts, failures=(), baseline=None, exit_code=0):
    report = tmp_path / 'pytest.json'
    report.write_text(json.dumps({'exit_code': exit_code, 'counts': counts, 'failures': failures}))
    output = tmp_path / 'acceptance.json'
    args = [sys.executable, str(SCRIPT), 'compare-results', '--pytest-report', str(report), '--output', str(output)]
    if baseline is not None:
        path = tmp_path / 'baseline.json'
        path.write_text(json.dumps({'failures': baseline}))
        args += ['--baseline', str(path)]
    process = subprocess.run(args, capture_output=True, text=True)
    return process.returncode, json.loads(output.read_text())


def test_counts_come_from_completed_pytest_report(tmp_path):
    code, result = compare(tmp_path, {'passed': 7, 'failed': 0, 'error': 0, 'skipped': 0})
    assert code == 0 and result['counts']['passed'] == 7
    assert result['all_tests_passed'] is True


@pytest.mark.parametrize('counts, exit_code', [
    ({'passed': 7, 'failed': 0, 'error': 0, 'skipped': 1}, 0),
    ({'passed': 0, 'failed': 0, 'error': 0, 'skipped': 0}, 0),
    ({'passed': 7, 'failed': 0, 'error': 0, 'skipped': 0}, 3),
])
def test_incomplete_or_skipped_run_cannot_pass(tmp_path, counts, exit_code):
    code, result = compare(tmp_path, counts, exit_code=exit_code)
    assert code == 1 and result['ok'] is False


def test_exact_baseline_is_reported_without_claiming_clean_suite(tmp_path):
    known = [{'nodeid': 'tests/test_old.py::test_old', 'outcome': 'failed'}]
    code, result = compare(tmp_path, {'passed': 7, 'failed': 1, 'error': 0, 'skipped': 0}, known, known, 1)
    assert code == 0 and result['all_tests_passed'] is False
    assert result['new_failures'] == []


def test_equal_failure_count_cannot_hide_new_failure(tmp_path):
    known = [{'nodeid': 'tests/test_old.py::test_old', 'outcome': 'failed'}]
    new = [{'nodeid': 'tests/test_new.py::test_new', 'outcome': 'failed'}]
    code, result = compare(tmp_path, {'passed': 7, 'failed': 1, 'error': 0, 'skipped': 0}, new, known, 1)
    assert code == 1 and 'test_new' in result['error']


def test_collection_error_is_a_failure_without_baseline(tmp_path):
    errors = [{'nodeid': 'tests/test_import.py', 'outcome': 'error'}]
    code, result = compare(tmp_path, {'passed': 7, 'failed': 0, 'error': 1, 'skipped': 0}, errors, exit_code=1)
    assert code == 1 and 'test_import' in result['error']


def test_missing_dependency_fails_before_tests_can_skip(tmp_path):
    output = tmp_path / 'report.json'
    process = subprocess.run([sys.executable, str(SCRIPT), 'tests', '--aidb-root', str(tmp_path / 'missing'),
        '--aidb-python', str(tmp_path / 'missing-python'), '--output', str(output)], capture_output=True, text=True)
    assert process.returncode == 1
    assert 'aidb Python missing' in json.loads(output.read_text())['error']


def test_pytest_plugin_reports_failures_errors_and_collection_skips(tmp_path):
    import os
    (tmp_path / 'test_examples.py').write_text('''import pytest

def test_pass():
    assert True

def test_fail():
    assert False

@pytest.fixture
def broken():
    raise ValueError("setup failed")

def test_setup(broken):
    pass
''')
    (tmp_path / 'test_skipped.py').write_text('import pytest\npytest.skip("unavailable", allow_module_level=True)\n')
    report_path = tmp_path / 'report.json'
    env = {**os.environ, 'MATCREATOR_TEST_REPORT': str(report_path),
        'PYTHONPATH': str(SCRIPT.parents[1])}
    process = subprocess.run([sys.executable, '-m', 'pytest', str(tmp_path), '-q',
        '-p', 'script.silicon_test_report'], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert process.returncode == 1
    report = json.loads(report_path.read_text())
    assert report['counts'] == {'passed': 1, 'failed': 1, 'error': 1, 'skipped': 1}
    assert {r['outcome'] for r in report['failures']} == {'failed', 'error'}


@pytest.mark.parametrize('counts,exit_code', [
    ({'passed': 1, 'failed': 0, 'error': 0, 'skipped': 0}, 1),
    ({'passed': 1, 'failed': 1, 'error': 0, 'skipped': 0}, 0),
    ({'passed': 1, 'failed': -1, 'error': 0, 'skipped': 0}, 0),
])
def test_contradictory_counts_are_rejected(tmp_path, counts, exit_code):
    code, result = compare(tmp_path, counts, exit_code=exit_code)
    assert code == 1 and result['ok'] is False
