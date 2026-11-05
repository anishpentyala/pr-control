"""NUL-delimited Git output must preserve literal filesystem path bytes."""
import json
import os
import pytest
from conftest import BUGGY_CALC,FIXED_CALC,FIX_TEST
from test_cli import run_cli
from prcontrol.core import changed_test_files,_git,_tracked_snapshot,PrControlError

@pytest.mark.parametrize('name',[
    'tests/test_cr\rname.py','tests/test_crlf\r\nname.py','tests/test_double\r\rname.py',
    'tests/test_edge\r.py','tests/test_edge\r\n.py',
])
def test_discovery_and_real_cli_preserve_carriage_returns(make_repo,name):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,name:FIX_TEST})
    assert changed_test_files(repo,base,head)==[name]
    path=repo/'report.json'
    proc=run_cli(repo,base,head,'--json-out',str(path),'--require-clean-worktrees')
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['targets']==[name]
    assert data['summary']=={'fix_confirmed':1}
    assert data['suite_diff']['complete']

@pytest.mark.parametrize('tail',[b'\xff',b'\xfe\xff',b'\x80\r',b'\xff\r\n'])
@pytest.mark.skipif(os.name!='posix',reason='arbitrary byte filenames are a POSIX contract')
def test_discovery_preserves_non_utf8_filesystem_names(make_repo,tail):
    name=os.fsdecode(b'tests/test_bytes'+tail+b'.py')
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,name:FIX_TEST})
    assert changed_test_files(repo,base,head)==[name]
    assert name in _tracked_snapshot(repo)
    assert (repo/name).read_text()==FIX_TEST

@pytest.mark.parametrize('name',['test_cr\rname.py','test_crlf\r\nname.py','test_line\nname.py','test_é.py'])
def test_snapshot_records_exact_names_and_detects_content_changes(make_repo,name):
    repo,_,_=make_repo({'calc.py':BUGGY_CALC},{name:FIX_TEST})
    before=_tracked_snapshot(repo)
    assert name in before
    (repo/name).write_text(FIX_TEST+'# changed\n')
    after=_tracked_snapshot(repo)
    assert set(before)==set(after)
    assert before[name]!=after[name]

@pytest.mark.parametrize('strip',[False,True])
def test_git_default_ref_text_stays_usable(make_repo,strip):
    repo,_,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC})
    value=_git(repo,'rev-parse','HEAD',strip=strip)
    assert value==(head if strip else head+'\n')


def test_git_failure_remains_user_facing(make_repo):
    repo,_,_=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC})
    with pytest.raises(PrControlError,match='git.*failed'):
        _git(repo,'rev-parse','--verify','definitely-missing^{commit}')
