"""A coincidental base assertion must not satisfy an explicit witness policy."""
import json
import subprocess
import sys
import pytest
from prcontrol.core import load_failure_witnesses, PrControlError


def run(repo, base, head, tmp_path, expected=None, repeats=1):
    out = tmp_path/'out.json'
    md = tmp_path/'out.md'
    flags = []
    if expected is not None:
        witness = tmp_path/'witness.json'
        witness.write_text(json.dumps(expected))
        flags = ['--base-failure-witness',str(witness)]
    proc = subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,
        '--head',head,'--targets','test_fix.py','--json-out',str(out),'--md-out',str(md),
        '--repeats',str(repeats),'--fail-on','no-control',*flags],capture_output=True,text=True)
    return proc,json.loads(out.read_text()),md.read_text()


@pytest.mark.parametrize('parallel',[False,True])
@pytest.mark.parametrize('match',[False,True])
def test_message_gate(make_repo,tmp_path,monkeypatch,parallel,match):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 2')
    repo,base,head=make_repo({'calc.py':'VALUE=0'}, {'calc.py':'VALUE=1',
        'test_fix.py':'import calc\ndef test_fix():\n assert calc.VALUE==1, "expected-addition-witness"\n'})
    proc,data,md=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'expected-addition-witness' if match else 'unrelated-witness'})
    assert proc.returncode==(0 if match else 1),proc.stderr
    assert data['summary']=={'fix_confirmed':1}
    assert data['base_failure_witness']['passed'] is match
    assert 'Base failure witness policy' in md
    assert data['verdicts'][0]['base_failure_details'][0]['exception_message']


def test_source_context_is_not_message_evidence(make_repo,tmp_path):
    test='import calc\ndef test_fix():\n # expected-witness\n assert calc.VALUE==1, "different-assertion" # expected-witness\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':test})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'expected-witness'})
    assert proc.returncode==1
    assert 'expected-witness' in data['verdicts'][0]['base_failure_details'][0]['traceback']
    assert not data['base_failure_witness']['passed']


@pytest.mark.parametrize('expected',[
    {'test_fix.py::absent':'witness'},
    {'test_fix.py::test_fix':'witness','test_fix.py::absent':'witness'},
])
def test_missing_expected_node_fails(make_repo,tmp_path,expected):
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1,"witness"'})
    proc,data,_=run(repo,base,head,tmp_path,expected)
    assert proc.returncode==1
    assert not data['base_failure_witness']['passed']


@pytest.mark.parametrize('code',[
    'def test_fix():assert True',
    'def test_fix():raise RuntimeError("witness")',
    'import pytest\n@pytest.mark.skip(reason="witness")\ndef test_fix():assert False',
])
def test_non_control_cannot_match(make_repo,tmp_path,code):
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\n'+code})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'witness'})
    assert proc.returncode==1
    assert not data['base_failure_witness']['passed']


@pytest.mark.parametrize('message',["'witness'", "'witness' if n==1 else 'different'"])
def test_all_repeated_base_failure_messages_match(make_repo,tmp_path,message):
    test='import calc\nfrom pathlib import Path\ndef test_fix():\n p=Path(".count")\n n=int(p.read_text())+1 if p.exists() else 1\n p.write_text(str(n))\n assert calc.VALUE==1, '+message+'\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':test})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'witness'},repeats=2)
    match=message=="'witness'"
    assert proc.returncode==(0 if match else 1),proc.stderr
    assert data['base_failure_witness']['passed'] is match
    assert data['summary']=={'fix_confirmed':1}


@pytest.mark.parametrize('raw',['{}','[]','null','{"node":true}','{"node":" "}','{"":"a"}','{"node":"a","node":"b"}','broken'])
def test_malformed_policy_rejected(tmp_path,raw):
    path=tmp_path/'w.json';path.write_text(raw)
    with pytest.raises(PrControlError):load_failure_witnesses(path)


def test_default_preserves_existing_verdicts(make_repo,tmp_path):
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1'})
    proc,data,_=run(repo,base,head,tmp_path)
    assert proc.returncode==0
    assert 'base_failure_witness' not in data
    assert data['summary']=={'fix_confirmed':1}


@pytest.mark.parametrize('both',[False,True])
def test_every_duplicate_occurrence_must_match(make_repo,tmp_path,both):
    msg='"witness"' if both else '"witness" if calc.COUNT==1 else "different"'
    test='import calc\ndef test_fix():\n calc.COUNT=getattr(calc,"COUNT",0)+1\n assert calc.VALUE==1, '+msg+'\n'
    conf='def pytest_collection_modifyitems(items):items.extend(list(items))\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':test,'conftest.py':conf})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'witness'})
    assert proc.returncode==(0 if both else 1),proc.stderr
    assert data['base_failure_witness']['passed'] is both


def test_explicit_pytest_fail_supported(make_repo,tmp_path):
    test='import calc,pytest\ndef test_fix():\n if calc.VALUE==0:pytest.fail("literal.*[witness]")\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1','test_fix.py':test})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'literal.*[witness]'})
    assert proc.returncode==0,proc.stderr
    assert data['base_failure_witness']['passed']


def test_missing_exception_message_is_not_a_match():
    from prcontrol.core import RunResult,check_failure_witnesses
    run=RunResult('base',1,failure_details={'test_a':[{'phase':'call','classification':'failed','traceback':'witness'}]},collected_counts={'test_a':1})
    result=check_failure_witnesses({'test_a':'witness'},[{'test':'test_a','verdict':'fix_confirmed'}],run)
    assert not result['passed']


@pytest.mark.parametrize('kind',['unverified','missing','duplicate-shortfall','wrong-phase'])
def test_incomplete_evidence_fails_closed(kind):
    from prcontrol.core import RunResult,check_failure_witnesses
    detail={'phase':'call','classification':'failed','exception_message':'witness'}
    count=1
    verdict='fix_confirmed'
    if kind=='unverified':verdict='transition_unverified_source'
    if kind=='missing':detail.pop('exception_message')
    if kind=='duplicate-shortfall':count=2
    if kind=='wrong-phase':detail['phase']='setup'
    result=check_failure_witnesses({'test_a':'witness'},[{'test':'test_a','verdict':verdict}],
        RunResult('base',1,failure_details={'test_a':[detail]},collected_counts={'test_a':count}))
    assert not result['passed']


def test_malformed_policy_before_worktrees(make_repo,tmp_path):
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1'})
    witness=tmp_path/'bad.json';witness.write_text('{}')
    proc=subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
        '--base-failure-witness',str(witness)],capture_output=True,text=True)
    assert proc.returncode==1
    assert 'nonempty JSON object' in proc.stderr
    state=subprocess.run(['git','worktree','list','--porcelain'],cwd=repo,capture_output=True,text=True)
    assert state.stdout.count('worktree ')==1


def test_policy_fails_even_with_never(make_repo,tmp_path):
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1',
        'test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1,"actual-message"'})
    witness=tmp_path/'w.json';witness.write_text('{"test_fix.py::test_fix":"wrong-message"}')
    proc=subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
        '--base-failure-witness',str(witness),'--fail-on','never'],capture_output=True,text=True)
    assert proc.returncode==1
    assert 'base failure witness policy not satisfied' in proc.stderr


@pytest.mark.parametrize('early',[False,True])
def test_message_evidence_is_bounded(make_repo,tmp_path,early):
    message='witness'+('x'*9000) if early else ('x'*9000)+'witness'
    repo,base,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1',
        'test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1,'+repr(message)})
    proc,data,_=run(repo,base,head,tmp_path,{'test_fix.py::test_fix':'witness'})
    assert proc.returncode==(0 if early else 1),proc.stderr
    evidence=data['verdicts'][0]['base_failure_details'][0]['exception_message']
    assert len(evidence)==8000
