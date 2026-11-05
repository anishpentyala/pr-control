"""Unreadable/incompatible probe evidence is an incomplete run, not a traceback."""
import json

import pytest

from prcontrol.core import cleanup, make_worktree, run_pytest


@pytest.mark.parametrize('patch', [
    {'tests': []},
    {'tests': {'test_fix.py::test_fix': 'passed'}},
    {'tests': {'test_fix.py::test_fix': {'call': 'unknown'}}},
    {'tests': {'test_fix.py::test_fix': {'cleanup': 'passed'}}},
    {'collected': [None]},
    {'finished': 'test_fix.py::test_fix'},
    {'imports': []},
    {'imports': {'calc': [None]}},
    {'imports': {'calc': ['relative.py']}},
    {'collection_errors': ['not-an-error-object']},
    {'collection_skips': [False]},
    {'runtime_skips': None},
    {'deselected': 1},
    {'failure_details': {'test_fix.py::test_fix': [None]}},
    {'failure_details': {'test_fix.py::test_fix': [{'phase': 'call', 'classification': 'failed', 'traceback': []}]}},
    {'exitstatus': False},
    {'exitstatus': 3},
])
def test_malformed_probe_report_is_cleanly_incomplete(make_repo, tmp_path, patch):
    conf = '''import json,os,pytest
from pathlib import Path
@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_sessionfinish(session,exitstatus):
 yield
 path=Path(os.environ['PR_CONTROL_REPORT'])
 data=json.loads(path.read_text())
 data.update(PATCH)
 path.write_text(json.dumps(data))
'''.replace('PATCH', repr(patch))
    repo, _, head = make_repo({'calc.py': 'VALUE=0'}, {
        'calc.py': 'VALUE=1', 'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1',
        'conftest.py': conf})
    tree = make_worktree(repo, head, tmp_path / 'head')
    try:
        result = run_pytest(tree, tree, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [tree])
    assert 'invalid probe report shape' in (result.collection_error or '')
    assert not result.source_verified
    assert not result.outcomes


@pytest.mark.parametrize('raw', ['null', '[]', '{}', '{"tests":{}}'])
def test_missing_report_fields_are_incomplete(make_repo, tmp_path, raw):
    conf = '''import os,pytest
from pathlib import Path
@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_sessionfinish(session,exitstatus):
 yield
 Path(os.environ['PR_CONTROL_REPORT']).write_text(RAW)
'''.replace('RAW', repr(raw))
    repo, _, head = make_repo({'calc.py': 'VALUE=0'}, {
        'calc.py': 'VALUE=1', 'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1',
        'conftest.py': conf})
    tree = make_worktree(repo, head, tmp_path / 'head')
    try:
        result = run_pytest(tree, tree, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [tree])
    assert 'invalid probe report shape' in (result.collection_error or '')
    assert not result.source_verified


@pytest.mark.parametrize('field,value', [
    ('phase', []), ('classification', {}), ('traceback', None),
    ('exception_message', []), ('subtest', {}),
])
def test_malformed_failure_values_do_not_raise(field, value):
    from prcontrol.probe_report import valid_probe_report
    failure = {'phase': 'call', 'classification': 'failed', 'traceback': 'failure'}
    failure[field] = value
    data = {'exitstatus': 1, 'tests': {'test_a': {'call': 'failed'}},
            'collected': ['test_a'], 'finished': ['test_a'],
            'imports': {}, 'collection_errors': [], 'failure_details': {'test_a': [failure]}}
    assert valid_probe_report(data, 1) is False
