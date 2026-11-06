"""Unreadable policy input must fail before any verification worktree exists."""
import subprocess
import sys
import pytest
from prcontrol.core import load_failure_witnesses, PrControlError
from conftest import BUGGY_CALC, FIXED_CALC

@pytest.mark.parametrize('kind', ['array', 'object'])
def test_deep_witness_policy_is_input_error(tmp_path, kind):
    raw = '['*10000+'0'+']'*10000 if kind == 'array' else '{"n":'*10000+'0'+'}'*10000
    path = tmp_path/'witness.json'
    path.write_text(raw)
    with pytest.raises(PrControlError):
        load_failure_witnesses(path)

@pytest.mark.parametrize('kind', ['array', 'object'])
def test_cli_deep_policy_has_no_traceback_or_worktrees(make_repo, tmp_path, kind):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC})
    raw = '['*10000+'0'+']'*10000 if kind == 'array' else '{"n":'*10000+'0'+'}'*10000
    path = tmp_path/'witness.json'; path.write_text(raw)
    proc = subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
                           '--base-failure-witness',str(path)], capture_output=True,text=True)
    assert proc.returncode == 1
    assert 'pr-control: error:' in proc.stderr
    assert 'Traceback' not in proc.stderr
    state = subprocess.run(['git','worktree','list','--porcelain'],cwd=repo,capture_output=True,text=True)
    assert state.stdout.count('worktree ') == 1


def test_decoder_recursion_error_is_wrapped_without_threshold_assumption(tmp_path, monkeypatch):
    import prcontrol.core as core
    path = tmp_path/'witness.json'
    path.write_text('{"test_fix.py::test_fix":"witness"}')
    def unreadable(*args, **kwargs):
        raise RecursionError('decoder nesting limit')
    monkeypatch.setattr(core.json, 'loads', unreadable)
    with pytest.raises(PrControlError, match='cannot read base failure witnesses') as error:
        load_failure_witnesses(path)
    assert isinstance(error.value.__cause__, RecursionError)
