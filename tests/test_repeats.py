"""Repeated controls retain evidence and reject observed instability."""
import json
import os
import subprocess
import sys
import pytest
from conftest import BUGGY_CALC,FIXED_CALC,FIX_TEST
from prcontrol.core import PrControlError


def run_cli(repo,base,head,*flags):
    return subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,'--targets','test_fix.py',*flags],capture_output=True,text=True)

@pytest.mark.parametrize('repeats',[1,2,3])
def test_stable_fix_retains_every_attempt(make_repo,tmp_path,repeats):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':FIX_TEST},{'calc.py':FIXED_CALC})
    path=tmp_path/'report.json';md=tmp_path/'report.md'
    proc=run_cli(repo,base,head,'--repeats',str(repeats),'--json-out',str(path),'--md-out',str(md),'--fail-on','no-control')
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['repeats']==repeats
    assert data['summary']=={'fix_confirmed':1}
    for ref,status in [('base','failed'),('head','passed')]:
        attempts=data['runs'][ref]['attempts']
        assert len(attempts)==repeats
        assert [a['attempt'] for a in attempts]==list(range(1,repeats+1))
        assert all(a['outcomes'][0]['outcome']==status for a in attempts)
        assert all(a['source_verified'] for a in attempts)
    if repeats>1:
        assert 'Repeated attempts' in md.read_text()
        assert 'shared worktree' in md.read_text()

@pytest.mark.parametrize('side',['base','head'])
def test_observed_flip_is_inconclusive(make_repo,tmp_path,side):
    # File state survives fresh processes. The flip is deliberately deterministic.
    test='''import calc
from pathlib import Path
def test_fix():
 p=Path(".attempt")
 n=int(p.read_text())+1 if p.exists() else 1
 p.write_text(str(n))
 if calc.MODE == "FLIP":
  assert n % 2 == 0
 else:
  assert calc.MODE == "PASS"
'''
    base_code='MODE="FLIP"' if side=='base' else 'MODE="FAIL"'
    head_code='MODE="FLIP"' if side=='head' else 'MODE="PASS"'
    repo,base,head=make_repo({'calc.py':base_code,'test_fix.py':test},{'calc.py':head_code})
    path=tmp_path/'out.json'
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(path),'--fail-on','never')
    assert proc.returncode==1,proc.stderr
    data=json.loads(path.read_text())
    assert data['summary']=={'inconclusive':1}
    assert not data['suite_diff']['complete']
    assert 'unstable repeated' in data['collection_errors'][side]
    assert [a['outcomes'][0]['outcome'] for a in data['runs'][side]['attempts']]==['failed','passed']

@pytest.mark.parametrize('bad',[0,-1,21])
def test_repeat_bounds_checked_before_worktrees(make_repo,bad):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':FIX_TEST},{'calc.py':FIXED_CALC})
    proc=run_cli(repo,base,head,'--repeats',str(bad))
    assert proc.returncode==1
    assert '--repeats must be between 1 and 20' in proc.stderr
    from prcontrol.core import run_repeated_pytest
    with pytest.raises(PrControlError,match='between 1 and 20'):
        run_repeated_pytest(repo,repo,'base',['test_fix.py'],30,repeats=bad)


def test_later_collection_failure_kept(make_repo,tmp_path):
    conf='''from pathlib import Path
p=Path('.collect-count')
n=int(p.read_text())+1 if p.exists() else 1
p.write_text(str(n))
if n==2: raise RuntimeError('second attempt broken')
'''
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':FIX_TEST,'conftest.py':conf},{'calc.py':FIXED_CALC})
    path=tmp_path/'out.json'
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(path))
    assert proc.returncode==1
    data=json.loads(path.read_text())
    assert data['summary']=={'inconclusive':1}
    assert 'second attempt broken' in data['runs']['base']['attempts'][1]['collection_error']
    assert data['runs']['base']['attempts'][0]['failure_details']


def test_default_stays_one_run(make_repo,tmp_path):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':FIX_TEST},{'calc.py':FIXED_CALC})
    path=tmp_path/'out.json';proc=run_cli(repo,base,head,'--json-out',str(path))
    assert proc.returncode==0
    data=json.loads(path.read_text())
    assert data['repeats']==1
    assert len(data['runs']['base']['attempts'])==1

@pytest.mark.parametrize('change',['counts','source','collection-skip','runtime-skip'])
def test_every_attempt_policy_evidence_is_aggregated(monkeypatch,tmp_path,change):
    from prcontrol import core
    first=core.RunResult('base',0,[core.TestOutcome('test_a','passed')],source_verified=True,collected_counts={'test_a':1})
    second=core.RunResult('base',0,[core.TestOutcome('test_a','passed')],source_verified=True,collected_counts={'test_a':1})
    if change=='counts':second.collected_counts={'test_a':2}
    elif change=='source':second.source_verified=False
    elif change=='collection-skip':second.collection_skips=['test_optional']
    else:second.runtime_skips=['test_optional']
    runs=iter([first,second]);monkeypatch.setattr(core,'run_pytest',lambda *a,**k:next(runs))
    result=core.run_repeated_pytest(tmp_path,tmp_path,'base',['test_a'],30,repeats=2)
    assert len(result.attempts)==2
    if change in {'counts','source'}:assert 'unstable repeated' in result.collection_error
    elif change=='collection-skip':
        assert result.collection_skips==['test_optional']
        assert 'unstable repeated' in result.collection_error
    else:
        assert result.runtime_skips==['test_optional']
        assert 'unstable repeated' in result.collection_error

@pytest.mark.parametrize('strict',[False,True])
def test_repeat_skip_policy_checks_later_attempt(make_repo,tmp_path,strict):
    test='''import calc,pytest
from pathlib import Path
def test_fix():
 p=Path('.attempt')
 n=int(p.read_text())+1 if p.exists() else 1
 p.write_text(str(n))
 if n==2:pytest.skip('later skipped')
 assert calc.add(2,3)==5
'''
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':test},{'calc.py':FIXED_CALC})
    path=tmp_path/'out.json';flags=['--require-no-skips'] if strict else []
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(path),*flags)
    assert proc.returncode==1
    data=json.loads(path.read_text())
    assert data['runs']['base']['runtime_skips']
    assert data['summary']=={'inconclusive':1}

@pytest.mark.parametrize('parallel',[False,True])
def test_repeated_strict_fix_with_parallel_workers(make_repo,tmp_path,monkeypatch,parallel):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 2')
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,'test_fix.py':FIX_TEST})
    path=tmp_path/'out.json'
    proc=run_cli(repo,base,head,'--repeats','2','--require-no-skips','--fail-on','no-control','--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert all(len(r['attempts'])==2 for r in data['runs'].values())
    assert all(all(a['source_verified'] for a in r['attempts']) for r in data['runs'].values())


def test_later_failure_evidence_retained_in_json(make_repo,tmp_path):
    test='import calc\nfrom pathlib import Path\ndef test_fix():\n p=Path(".attempt")\n n=int(p.read_text())+1 if p.exists() else 1\n p.write_text(str(n))\n assert n==1, "later-assertion-witness"\n'
    repo,base,head=make_repo({'calc.py':BUGGY_CALC,'test_fix.py':test},{'calc.py':FIXED_CALC})
    path=tmp_path/'out.json'
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(path))
    assert proc.returncode==1
    data=json.loads(path.read_text())
    assert data['runs']['base']['attempts'][0]['failure_details']=={}
    assert 'later-assertion-witness' in json.dumps(data['runs']['base']['attempts'][1]['failure_details'])
