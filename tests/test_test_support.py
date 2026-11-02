"""Explicit head helper imports without putting head source before base code."""
import json
from conftest import BUGGY_CALC, FIXED_CALC
from test_cli import run_cli


def test_head_helpers_are_available_but_source_stays_on_selected_ref(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC}, {
        'calc.py':FIXED_CALC,
        'tests/helpers.py':'EXPECTED=5\n',
        'tests/test_calc.py':'from calc import add\nfrom helpers import EXPECTED\ndef test_add():\n    assert add(2,3)==EXPECTED\n',
    })
    path=repo/'result.json'
    proc=run_cli(repo,base,head,'--test-support','tests','--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert data['suite_diff']['complete']
    assert all(run['source_verified'] for run in data['runs'].values())
    assert data['test_support']==['tests']


def test_test_support_must_stay_inside_head_repo(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,'tests/test_calc.py':'def test_ok(): pass\n'})
    proc=run_cli(repo,base,head,'--test-support','../')
    assert proc.returncode==1
    assert 'test support must stay inside' in proc.stderr


def test_default_still_fails_closed_without_explicit_helpers(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,
        'tests/helpers.py':'EXPECTED=5\n',
        'tests/test_calc.py':'from helpers import EXPECTED\nfrom calc import add\ndef test_add():\n    assert add(2,3)==EXPECTED\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--json-out',str(path)).returncode==1
    data=json.loads(path.read_text())
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')


def test_helper_path_cannot_shadow_selected_source(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,
        'tests/calc.py':FIXED_CALC,
        'tests/helpers.py':'EXPECTED=5\n',
        'tests/test_calc.py':'from helpers import EXPECTED\nfrom calc import add\ndef test_add():\n    assert add(2,3)==EXPECTED\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--test-support','tests','--json-out',str(path)).returncode==0
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert '/base/calc.py' in data['runs']['base']['imports']['calc'][0]
    assert '/head/calc.py' in data['runs']['head']['imports']['calc'][0]


def test_new_head_source_fallback_is_rejected(make_repo):
    repo,base,head=make_repo({'old.py':'x=1\n'},{'calc.py':FIXED_CALC,
        'tests/test_calc.py':'from calc import add\ndef test_add():\n    assert add(2,3)==5\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--test-support','.','--json-out',str(path)).returncode==1
    data=json.loads(path.read_text())
    assert 'source provenance mismatch' in data['collection_errors']['base']
    assert data['summary']=={'inconclusive':1}


def test_missing_support_directory_is_not_silently_ignored(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,'tests/test_calc.py':'def test_ok(): pass\n'})
    proc=run_cli(repo,base,head,'--test-support','missing')
    assert proc.returncode==1
    assert 'does not exist on head' in proc.stderr


def test_repeated_support_directories_and_selectors(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,
        'test_helpers/first.py':'A=2\n','tests/second.py':'B=3\n',
        'tests/test_calc.py':'from first import A\nfrom second import B\nfrom calc import add\ndef test_add():\n    assert add(A,B)==5\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--test-support','test_helpers','--test-support','tests',
        '--targets','tests/test_calc.py::test_add','--json-out',str(path)).returncode==0
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert data['test_support']==['test_helpers','tests']


import pytest


@pytest.mark.parametrize('layout', ['', 'src/'])
@pytest.mark.parametrize('helper', ['helpers', 'conftest'])
def test_shared_helpers_across_flat_and_src_layouts(make_repo, layout, helper):
    repo,base,head=make_repo({layout+'calc.py':BUGGY_CALC},{layout+'calc.py':FIXED_CALC,
        f'tests/{helper}.py':'EXPECTED=5\n',
        'tests/test_calc.py':f'from {helper} import EXPECTED\nfrom calc import add\ndef test_add():\n    assert add(2,3)==EXPECTED\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--test-support','tests','--json-out',str(path)).returncode==0
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert data['suite_diff']['complete']


def test_observed_head_source_shadowing_still_rejected_with_prepend(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,
        'tests/calc.py':FIXED_CALC,
        'tests/test_calc.py':'from calc import add\ndef test_add():\n    assert add(2,3)==5\n'})
    path=repo/'result.json'
    assert run_cli(repo,base,head,'--test-support','tests','--import-mode','prepend','--json-out',str(path)).returncode==1
    data=json.loads(path.read_text())
    assert not data['suite_diff']['complete']
    assert not data['summary'].get('fix_confirmed')
    assert 'source provenance mismatch' in data['collection_errors']['base']


def test_symlink_support_escape_is_rejected(make_repo, tmp_path):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,'tests/test_calc.py':'def test_ok(): pass\n'})
    from conftest import git
    outside=tmp_path/'outside';outside.mkdir()
    (repo/'helper-link').symlink_to(outside,target_is_directory=True)
    git(repo,'add','helper-link');git(repo,'commit','-m','symlink helper')
    head=git(repo,'rev-parse','HEAD')
    proc=run_cli(repo,base,head,'--test-support','helper-link')
    assert proc.returncode==1
    assert 'test support must stay inside' in proc.stderr
