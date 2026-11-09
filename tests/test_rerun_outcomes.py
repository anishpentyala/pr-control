"""Retries are execution-instability evidence, never clean control passes."""
import pytest

from prcontrol.core import cleanup, make_worktree, run_pytest, verdicts

pytest.importorskip('pytest_rerunfailures')


@pytest.mark.parametrize('retry_passes', [True, False])
def test_retry_report_does_not_break_probe_and_is_not_a_clean_control(make_repo, tmp_path, retry_passes):
    body = '''import pytest
import calc
attempt = 0
@pytest.mark.flaky(reruns=1)
def test_fix():
 global attempt
 attempt += 1
 assert calc.VALUE == 1 and (attempt > 1 or not RETRY_PASSES), 'synthetic retry witness'
'''.replace('RETRY_PASSES', repr(retry_passes))
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'}, {'calc.py': 'VALUE=1\n', 'test_fix.py': body})
    code = make_worktree(repo, head, tmp_path / 'head')
    try:
        run = run_pytest(code, code, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [code])
    if retry_passes:
        assert run.outcomes, 'a retry must retain test evidence instead of crashing the probe'
        assert run.outcomes[0].outcome == 'error', 'a retried pass is not a clean completed execution'
        assert not any(row['verdict'] == 'fix_confirmed' for row in verdicts(run, run))
    else:
        assert run.collection_error is None
        assert run.outcomes[0].outcome == 'passed'


@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown'])
@pytest.mark.parametrize('eventual_pass', [True, False])
def test_retry_lifecycle_keeps_failure_evidence(make_repo, tmp_path, phase, eventual_pass):
    body = """import pytest
import calc
count = 0
@pytest.fixture
def lifecycle():
 global count
 count += 1
 if PHASE == 'setup':
  assert EVENTUAL_PASS and count > 1, 'setup retry witness'
 yield
 if PHASE == 'teardown':
  assert EVENTUAL_PASS and count > 1, 'teardown retry witness'
@pytest.mark.flaky(reruns=1)
def test_fix(lifecycle):
 if PHASE == 'call':
  assert EVENTUAL_PASS and count > 1, 'call retry witness'
 assert calc.VALUE == 1
""".replace('PHASE', repr(phase)).replace('EVENTUAL_PASS', repr(eventual_pass))
    repo, _, head = make_repo({'calc.py': 'VALUE=0\n'}, {'calc.py': 'VALUE=1\n', 'test_fix.py': body})
    code = make_worktree(repo, head, tmp_path / 'head')
    try:
        run = run_pytest(code, code, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [code])
    assert run.returncode != 3, 'rerun reports must not cause a pytest internal error'
    assert run.outcomes and run.outcomes[0].outcome == 'error'
    details = next(iter(run.failure_details.values()))
    assert any(d['phase'] == phase and d['classification'] == 'error' for d in details)
    assert any(phase + ' retry witness' in d['traceback'] for d in details)
    assert not any(row['verdict'] == 'fix_confirmed' for row in verdicts(run, run))


def test_retried_base_assertions_cannot_establish_a_clean_control(make_repo, tmp_path):
    from prcontrol.core import suite_diff
    body = """import pytest
import calc
@pytest.mark.flaky(reruns=1)
def test_fix():
 assert calc.VALUE == 1, 'original defect witness'
"""
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'}, {'calc.py': 'VALUE=1\n', 'test_fix.py': body})
    b = make_worktree(repo, base, tmp_path / 'base')
    h = make_worktree(repo, head, tmp_path / 'head')
    try:
        base_run = run_pytest(b, h, 'base', ['test_fix.py'], 30)
        head_run = run_pytest(h, h, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [b, h])
    assert base_run.returncode == 1
    assert head_run.returncode == 0
    assert base_run.outcomes[0].outcome == 'error'
    assert head_run.outcomes[0].outcome == 'passed'
    assert not suite_diff(base_run, head_run)['complete']
    assert verdicts(base_run, head_run)[0]['verdict'] == 'inconclusive'
