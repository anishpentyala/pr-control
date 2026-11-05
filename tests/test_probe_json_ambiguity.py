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
    assert 'pytest produced no valid report' in (result.collection_error or '')
    assert not result.source_verified and not result.outcomes


def test_duplicate_looking_string_values_are_not_duplicate_members(make_repo,tmp_path):
    repo,_,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\ndef test_fix():\n assert calc.VALUE==1\n assert \'"call":"passed","call":"failed"\''})
    tree=make_worktree(repo,head,tmp_path/'head')
    try:result=run_pytest(tree,tree,'head',['test_fix.py'],30)
    finally:cleanup(repo,[tree])
    assert result.returncode==0 and not result.collection_error and result.source_verified
