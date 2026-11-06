"""Unreadable/ambiguous raw probe JSON is never proof of a passing run."""
import json

import pytest

from prcontrol.core import cleanup,make_worktree,run_pytest


@pytest.mark.parametrize('mode',['top','escaped','outcome','phase','equal','deep'])
def test_raw_probe_json_ambiguity_fails_closed(make_repo,tmp_path,mode):
    conf='''import json,os,pytest
from pathlib import Path
@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_sessionfinish(session,exitstatus):
 yield
 path=Path(os.environ['PR_CONTROL_REPORT']);data=json.loads(path.read_text())
 raw=path.read_text()
 if MODE=='top':raw='{"tests":{},'+raw[1:]
 elif MODE=='escaped':raw='{"te\\\\u0073ts":{},'+raw[1:]
 elif MODE=='outcome':
  node=next(iter(data['tests']));raw=raw.replace('"tests": {','"tests": {'+json.dumps(node)+':{"call":"failed"},',1)
 elif MODE=='phase':raw=raw.replace('"call": "passed"','"call":"failed","call":"passed"',1)
 elif MODE=='equal':raw='{"exitstatus":0,'+raw[1:]
 else:raw='['*10000+'0'+']'*10000
 path.write_text(raw)
'''.replace('MODE',repr(mode))
    repo,_,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1','conftest.py':conf})
    tree=make_worktree(repo,head,tmp_path/'head')
    try:result=run_pytest(tree,tree,'head',['test_fix.py'],30)
    finally:cleanup(repo,[tree])
    expected = ('pytest produced no valid report', 'invalid probe report shape') if mode == 'deep' else ('pytest produced no valid report',)
    assert any(message in (result.collection_error or '') for message in expected)
    assert not result.source_verified and not result.outcomes


def test_duplicate_looking_string_values_are_not_duplicate_members(make_repo,tmp_path):
    repo,_,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\ndef test_fix():\n assert calc.VALUE==1\n assert \'"call":"passed","call":"failed"\''})
    tree=make_worktree(repo,head,tmp_path/'head')
    try:result=run_pytest(tree,tree,'head',['test_fix.py'],30)
    finally:cleanup(repo,[tree])
    assert result.returncode==0 and not result.collection_error and result.source_verified


def test_raw_probe_decoder_recursion_is_cleanly_incomplete(make_repo, tmp_path, monkeypatch):
    import prcontrol.core as core
    repo, _, head = make_repo({'calc.py': 'VALUE=0'}, {'calc.py': 'VALUE=1',
        'test_fix.py': 'import calc\ndef test_fix():assert calc.VALUE==1'})
    tree = make_worktree(repo, head, tmp_path/'head')
    def unreadable(*args, **kwargs):
        raise RecursionError('decoder nesting limit')
    try:
        with monkeypatch.context() as context:
            context.setattr(core.json, 'loads', unreadable)
            result = run_pytest(tree, tree, 'head', ['test_fix.py'], 30)
    finally:
        cleanup(repo, [tree])
    assert 'pytest produced no valid report' in (result.collection_error or '')
    assert not result.source_verified and not result.outcomes
