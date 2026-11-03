"""Selection filtering is evidence, distinct from runtime skips/completeness."""
import json
import pytest
from test_cli import run_cli


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('strict', [False, True])
@pytest.mark.parametrize('selection', ['-k test_fix', '-m selected'])
def test_deselection_is_reported_and_optionally_rejected(make_repo, monkeypatch, parallel, strict, selection):
    if parallel:
        pytest.importorskip('xdist')
    monkeypatch.setenv('PYTEST_ADDOPTS', selection + (' -n 2' if parallel else ''))
    repo, base, head = make_repo({'calc.py': 'VALUE=0\n'}, {
        'calc.py': 'VALUE=1\n',
        'pytest.ini': '[pytest]\nmarkers = selected: control\n',
        'test_controls.py': 'import pytest,calc\n@pytest.mark.selected\ndef test_fix():assert calc.VALUE==1\ndef test_other():assert False\n',
    })
    proc = run_cli(repo, base, head, '--json-out', str(repo/'r.json'), '--md-out', str(repo/'r.md'),
                   '--fail-on', 'no-control', *(['--require-no-deselections'] if strict else []))
    assert proc.returncode == (1 if strict else 0), proc.stderr
    report=json.loads((repo/'r.json').read_text())
    assert report['suite_diff']['complete']
    assert report['summary']=={'fix_confirmed':1}
    for run in report['runs'].values():
        assert run.get('deselected')==['test_controls.py::test_other'], 'reported deselection evidence must be retained'
        assert not run['runtime_skips'] and not run['collection_skips']
        assert run['attempts'][0]['deselected']==run['deselected']
    assert 'deselected tests' in (repo/'r.md').read_text()
    if strict:assert '--require-no-deselections' in proc.stderr


@pytest.mark.parametrize('strict', [False, True])
def test_explicit_node_target_is_not_reported_as_deselection(make_repo, strict):
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{'calc.py':'VALUE=1\n',
        'test_controls.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_other():assert False\n'})
    proc=run_cli(repo,base,head,'--targets','test_controls.py::test_fix','--json-out',str(repo/'r.json'),
                 *(['--require-no-deselections'] if strict else []))
    assert proc.returncode==0,proc.stderr
    report=json.loads((repo/'r.json').read_text())
    assert all(not r['deselected'] for r in report['runs'].values())


def test_changing_deselection_evidence_between_attempts_fails_closed(make_repo):
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{
        'calc.py':'VALUE=1\n',
        'conftest.py': 'from pathlib import Path\n'
            'def pytest_collection_modifyitems(config,items):\n'
            ' p=Path("selection-count");n=int(p.read_text())+1 if p.exists() else 1;p.write_text(str(n))\n'
            ' removed=[i for i in items if i.name.startswith("test_hidden")]\n'
            ' items[:]=[i for i in items if not i.name.startswith("test_hidden")]\n'
            ' config.hook.pytest_deselected(items=removed[:1] if n==1 else removed[1:])\n',
        'test_controls.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_hidden_a():pass\ndef test_hidden_b():pass\n'})
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(repo/'r.json'),'--fail-on','never')
    assert proc.returncode==1,proc.stderr
    report=json.loads((repo/'r.json').read_text())
    assert report['summary']=={'inconclusive':1}
    assert all('deselection' in error for error in report['collection_errors'].values())
    for run in report['runs'].values():
        assert run['deselected']==['test_controls.py::test_hidden_a','test_controls.py::test_hidden_b']
        assert run['attempts'][0]['deselected']!=run['attempts'][1]['deselected']


def test_no_deselection_policy_cannot_be_bypassed_with_fail_on_never(make_repo,monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS','-k test_fix')
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{'calc.py':'VALUE=1\n',
        'test_controls.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_other():pass\n'})
    proc=run_cli(repo,base,head,'--require-no-deselections','--fail-on','never')
    assert proc.returncode==1
    assert '--require-no-deselections' in proc.stderr


@pytest.mark.parametrize('parallel',[False,True])
def test_ref_specific_deselection_is_not_a_comparable_suite(make_repo,monkeypatch,parallel):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 2')
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{'calc.py':'VALUE=1\n',
        'conftest.py':'import calc\ndef pytest_collection_modifyitems(config,items):\n'
            ' if calc.VALUE==0:\n'
            '  removed=[i for i in items if i.name=="test_other"]\n'
            '  items[:]=[i for i in items if i.name!="test_other"]\n'
            '  config.hook.pytest_deselected(items=removed)\n',
        'test_controls.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_other():pass\n'})
    proc=run_cli(repo,base,head,'--json-out',str(repo/'r.json'),'--fail-on','never')
    assert proc.returncode==1,proc.stderr
    report=json.loads((repo/'r.json').read_text())
    assert not report['suite_diff']['complete']
    assert report['runs']['base']['deselected']==['test_controls.py::test_other']
    assert not report['runs']['head']['deselected']
    assert set(report['summary'])=={'inconclusive'}


def test_stable_filtered_repeats_remain_selected_suite_evidence(make_repo,monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS','-k test_fix')
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{'calc.py':'VALUE=1\n',
        'test_controls.py':'import calc\ndef test_fix():assert calc.VALUE==1\ndef test_other():pass\n'})
    proc=run_cli(repo,base,head,'--repeats','2','--json-out',str(repo/'r.json'),'--fail-on','no-control')
    assert proc.returncode==0,proc.stderr
    report=json.loads((repo/'r.json').read_text())
    assert report['summary']=={'fix_confirmed':1}
    assert all(len(run['attempts'])==2 for run in report['runs'].values())
