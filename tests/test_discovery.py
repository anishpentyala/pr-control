"""Automatic head-test discovery includes renames and literal Git filenames."""
import json
from conftest import BUGGY_CALC, FIXED_CALC, FIX_TEST, git
from test_cli import run_cli
from prcontrol.core import changed_test_files


def test_renamed_regression_test_is_selected(make_repo):
    body=FIX_TEST+'\n'+''.join(f'# stable context {i}\n' for i in range(20))
    repo,base,_=make_repo({'calc.py':BUGGY_CALC,'tests/test_before.py':body},{'calc.py':FIXED_CALC})
    git(repo,'mv','tests/test_before.py','tests/test_after.py')
    git(repo,'commit','-m','relocate test')
    head=git(repo,'rev-parse','HEAD')
    assert changed_test_files(repo,base,head)==['tests/test_after.py']
    path=repo/'report.json'
    proc=run_cli(repo,base,head,'--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    data=json.loads(path.read_text())
    assert data['summary']=={'fix_confirmed':1}
    assert data['targets']==['tests/test_after.py']


def test_unicode_test_filename_is_not_git_quoted(make_repo):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,'tests/test_café.py':FIX_TEST})
    assert changed_test_files(repo,base,head)==['tests/test_café.py']
    path=repo/'report.json'
    proc=run_cli(repo,base,head,'--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    assert json.loads(path.read_text())['summary']=={'fix_confirmed':1}


import pytest


@pytest.mark.parametrize('name', ['tests/test_quoted"name.py','tests/test_tab\tname.py','tests/test_line\nname.py','tests/test_space name.py'])
def test_literal_special_character_paths_are_preserved(make_repo,name):
    repo,base,head=make_repo({'calc.py':BUGGY_CALC},{'calc.py':FIXED_CALC,name:FIX_TEST})
    assert changed_test_files(repo,base,head)==[name]
    path=repo/'report.json'
    proc=run_cli(repo,base,head,'--json-out',str(path))
    assert proc.returncode==0,proc.stderr
    assert json.loads(path.read_text())['summary']=={'fix_confirmed':1}


def test_renamed_non_test_and_conftest_are_excluded(make_repo):
    repo,base,_=make_repo({'calc.py':BUGGY_CALC,'before.py':'X=1\n','tests/conftest.py':'# old\n'}, {'calc.py':FIXED_CALC})
    git(repo,'mv','before.py','after.py');git(repo,'mv','tests/conftest.py','conftest.py')
    git(repo,'commit','-m','non-test moves');head=git(repo,'rev-parse','HEAD')
    assert changed_test_files(repo,base,head)==[]


def test_rename_into_test_path_is_selected_and_out_of_test_is_not(make_repo):
    repo,base,_=make_repo({'calc.py':BUGGY_CALC,'candidate.py':FIX_TEST,'tests/test_old.py':'def test_old(): pass\n'},{'calc.py':FIXED_CALC})
    git(repo,'mv','candidate.py','tests/test_new.py');git(repo,'mv','tests/test_old.py','retired.py')
    git(repo,'commit','-m','move into and out of tests');head=git(repo,'rev-parse','HEAD')
    assert changed_test_files(repo,base,head)==['tests/test_new.py']
