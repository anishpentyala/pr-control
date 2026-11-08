"""A later teardown skip must not erase an earlier test-call failure."""
import json

import pytest

from test_cli import run_cli


@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('direction', ['regression', 'fix'])
@pytest.mark.parametrize('companion_failure', [False, True])
def test_call_failure_survives_teardown_skip(make_repo, monkeypatch, parallel,
                                            direction, companion_failure, strict):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS', '-n 1')
    before, after = (1, 0) if direction == 'regression' else (0, 1)
    code = '''import pytest, calc
@pytest.fixture
def resource():
    yield
    if calc.VALUE == 0:
        pytest.skip("cleanup unavailable")
def test_value(resource):
    assert calc.VALUE == 1, "wrong value"
'''
    if companion_failure:
        code += '\ndef test_existing_failure():\n    assert False, "preexisting"\n'
    repo, base, head = make_repo({'calc.py': f'VALUE={before}\n'},
                                {'calc.py': f'VALUE={after}\n', 'test_value.py': code})
    output = repo / 'report.json'
    proc = run_cli(repo, base, head, '--json-out', str(output), '--md-out', str(repo / 'report.md'),
                   '--fail-on', 'regression' if direction == 'regression' else 'no-control',
                   *(['--require-no-skips'] if strict else []))
    assert proc.returncode == (1 if strict else 2), proc.stderr
    if strict:
        assert '--require-no-skips' in proc.stderr
    report = json.loads(output.read_text())
    row = next(row for row in report['verdicts'] if row['test'].endswith('::test_value'))
    failed_side = 'head' if direction == 'regression' else 'base'
    assert row[failed_side] == 'failed'
    assert row['verdict'] == ('regression_introduced' if direction == 'regression'
                              else 'transition_with_skips')
    assert report['suite_diff']['complete']
    assert report['runs'][failed_side]['runtime_skips']

    markdown = (repo / 'report.md').read_text()
    expected_count = 1 if direction == 'fix' else 0
    assert f"- transition_with_skips: {expected_count}" in markdown
