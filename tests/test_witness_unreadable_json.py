"""Unreadable policy input must fail before any verification worktree exists."""
import subprocess
import sys
import pytest
from prcontrol.core import load_failure_witnesses, PrControlError
from conftest import BUGGY_CALC, FIXED_CALC

@pytest.mark.parametrize('kind', ['array', 'object'])
def test_deep_witness_policy_is_input_error(tmp_path, kind):
    raw = '['*2000+'0'+']'*2000 if kind == 'array' else '{"n":'*2000+'0'+'}'*2000
    path = tmp_path/'witness.json'
    path.write_text(raw)
    with pytest.raises(PrControlError, match='cannot read base failure witnesses'):
        load_failure_witnesses(path)

@pytest.mark.parametrize('kind', ['array', 'object'])
def test_cli_deep_policy_has_no_traceback_or_worktrees(make_repo, tmp_path, kind):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC})
    raw = '['*2000+'0'+']'*2000 if kind == 'array' else '{"n":'*2000+'0'+'}'*2000
    path = tmp_path/'witness.json'; path.write_text(raw)
    proc = subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
                           '--base-failure-witness',str(path)], capture_output=True,text=True)
    assert proc.returncode == 1
    assert 'pr-control: error: cannot read base failure witnesses' in proc.stderr
    assert 'Traceback' not in proc.stderr
    state = subprocess.run(['git','worktree','list','--porcelain'],cwd=repo,capture_output=True,text=True)
    assert state.stdout.count('worktree ') == 1
