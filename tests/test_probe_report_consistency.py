"""Well-typed evidence must still describe the same selected test set."""
import pytest
from prcontrol.probe_report import valid_probe_report


def report():
    return {'exitstatus':0,'tests':{'test_a':{'call':'passed'},'test_b':{'call':'passed'}},
            'collected':['test_a','test_b'],'finished':['test_a','test_b'],
            'imports':{},'collection_errors':[]}


@pytest.mark.parametrize('change',[
    {'tests':{'test_a':{'call':'passed'}}},
    {'tests':{'test_c':{'call':'passed'},'test_b':{'call':'passed'}}},
    {'tests':{}},
    {'failure_details':{'test_c':[{'phase':'call','classification':'failed','traceback':'failure'}]}},
    {'test_source':{'test_c':['/tmp/calc.py']}},
])
def test_same_shape_cannot_drop_or_invent_test_identity(change):
    data=report();data.update(change)
    assert not valid_probe_report(data,0)


def test_valid_duplicate_occurrences_still_share_one_outcome_record():
    data=report();data['collected']+=['test_a'];data['finished']+=['test_a']
    assert valid_probe_report(data,0)


def test_setup_skip_and_failed_teardown_are_not_required_to_have_call_phase():
    data=report();data['tests']={'test_a':{'setup':'skipped'},'test_b':{'setup':'passed','call':'passed','teardown':'failed'}}
    data['exitstatus']=1
    assert valid_probe_report(data,1)


def test_empty_collection_error_report_remains_shape_valid():
    data=report();data.update(tests={},collected=[],finished=[],exitstatus=2,
                             collection_errors=[{'nodeid':'test_a.py','error':'cannot import'}])
    assert valid_probe_report(data,2)


def test_partial_run_is_left_for_existing_completeness_diagnostics():
    data=report();data['finished']=['test_a'];data['tests']={'test_a':{'call':'passed'}};data['exitstatus']=2
    assert valid_probe_report(data,2)


@pytest.mark.parametrize('mode',['drop','invent'])
def test_inconsistent_live_probe_cannot_verify_selected_suite(make_repo,tmp_path,mode):
    from prcontrol.core import run_pytest,make_worktree,cleanup
    conf='''import json,os,pytest
from pathlib import Path
@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_sessionfinish(session,exitstatus):
 yield
 path=Path(os.environ['PR_CONTROL_REPORT']);data=json.loads(path.read_text())
 node=next(iter(data['tests']))
 if MODE=='drop': data['tests'].pop(node)
 else: data['tests'][node+'invented']=data['tests'].pop(node)
 path.write_text(json.dumps(data))
'''.replace('MODE',repr(mode))
    repo,_,head=make_repo({'calc.py':'VALUE=0'},{'calc.py':'VALUE=1',
        'test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_control():assert calc.VALUE>=0',
        'conftest.py':conf})
    tree=make_worktree(repo,head,tmp_path/'head')
    try: result=run_pytest(tree,tree,'head',['test_fix.py'],30)
    finally: cleanup(repo,[tree])
    assert 'invalid probe report shape' in (result.collection_error or '')
    assert not result.source_verified and not result.outcomes


def test_nonzero_partial_phase_record_preserves_crash_diagnostics():
    data=report();data['finished']=['test_a'];data['tests']['test_b']={'setup':'passed'}
    data['exitstatus']=1
    assert valid_probe_report(data,1)
    data['exitstatus']=0
    assert not valid_probe_report(data,0)


@pytest.mark.parametrize('mode', ['drop', 'invent'])
def test_cli_inconsistent_identity_cannot_report_complete(make_repo, tmp_path, mode):
    import json
    import subprocess
    import sys
    conf = """import json,os,pytest
from pathlib import Path
@pytest.hookimpl(hookwrapper=True,tryfirst=True)
def pytest_sessionfinish(session,exitstatus):
 yield
 path=Path(os.environ['PR_CONTROL_REPORT']);data=json.loads(path.read_text())
 node=next(n for n in data['tests'] if n.endswith('::test_control'))
 if MODE=='drop': data['tests'].pop(node)
 else: data['tests'][node+'invented']=data['tests'].pop(node)
 path.write_text(json.dumps(data))
""".replace('MODE', repr(mode))
    tests = 'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_control():assert calc.VALUE>=0'
    repo, base, head = make_repo({'calc.py': 'VALUE=0', 'test_fix.py': tests, 'conftest.py': conf},
                                 {'calc.py': 'VALUE=1'})
    path = tmp_path / 'out.json'
    proc = subprocess.run([sys.executable, '-m', 'prcontrol.cli', str(repo),
                           '--base', base, '--head', head, '--targets', 'test_fix.py',
                           '--json-out', str(path), '--fail-on', 'no-control'],
                          capture_output=True, text=True)
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data['suite_diff']['complete']
    assert data['summary'] == {}
    assert all('invalid probe report shape' in text for text in data['collection_errors'].values())
    assert not any(run['source_verified'] for run in data['runs'].values())
