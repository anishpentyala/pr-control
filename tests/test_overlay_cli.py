import json
import pytest
from test_cli import run_cli
from conftest import git


def packaged(make_repo, fixture='import calc\n'):
    return make_repo({'src/calc/__init__.py':'VALUE=0\n',
                      'src/calc/conftest.py':fixture,
                      'src/calc/tests/__init__.py':''},
                     {'src/calc/__init__.py':'VALUE=1\n',
                      'src/calc/tests/test_value.py':'import calc\ndef test_value():\n assert calc.VALUE==1, "expected fixed value"\n'})


def test_packaged_negative_control_with_explicit_overlay(make_repo):
    repo,base,head=packaged(make_repo)
    p=run_cli(repo,base,head,'--overlay-tests','--repeats','2','--require-no-skips','--require-no-deselections','--json-out',str(repo/'r.json'),'--md-out',str(repo/'r.md'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    assert d['suite_diff']['complete']
    assert all(r['source_verified'] for r in d['runs'].values())
    assert len(d['test_overlay']['files']['base'])==1
    assert d['test_overlay']['files']['base'][0]['previous_sha256'] is None
    for ref in ['base','head']:
        assert f'/{ref}/src/calc/__init__.py' in d['runs'][ref]['imports']['calc'][0]
    assert 'not an untouched baseline' in (repo/'r.md').read_text()
    assert len(git(repo,'worktree','list').splitlines())==1


def test_default_packaged_behavior_remains_fail_closed(make_repo):
    repo,base,head=packaged(make_repo)
    p=run_cli(repo,base,head,'--json-out',str(repo/'r.json'))
    assert p.returncode==1
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'inconclusive':1}
    assert not d['runs']['base']['source_verified']


@pytest.mark.parametrize('name',['pytest.ini','.pytest.ini','pyproject.toml','tox.ini','setup.cfg'])
def test_changed_pytest_candidate_config_rejected(make_repo,name):
    repo,base,head=packaged(make_repo)
    (repo/name).write_text('# new config\n');git(repo,'add',name);git(repo,'commit','-qm','config');head=git(repo,'rev-parse','HEAD')
    p=run_cli(repo,base,head,'--overlay-tests')
    assert p.returncode==1 and 'overlay context differs' in p.stderr
    assert len(git(repo,'worktree','list').splitlines())==1


def test_changed_fixture_requires_explicit_opt_in(make_repo):
    repo,base,head=packaged(make_repo)
    (repo/'src/calc/conftest.py').write_text('import calc\n# new fixture\n');git(repo,'add','.');git(repo,'commit','-qm','fixture');head=git(repo,'rev-parse','HEAD')
    p=run_cli(repo,base,head,'--overlay-tests')
    assert p.returncode==1 and 'overlay context differs' in p.stderr
    p=run_cli(repo,base,head,'--overlay-tests','--overlay-fixture','src/calc/conftest.py','--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    assert len(d['test_overlay']['files']['base'])==2


@pytest.mark.parametrize('args',[['--overlay-fixture','conftest.py'],['--overlay-tests','--test-support','src/calc/tests']])
def test_incompatible_options_fail_closed(make_repo,args):
    repo,base,head=packaged(make_repo)
    p=run_cli(repo,base,head,*args)
    assert p.returncode==1


def test_production_helper_is_not_copied_from_head(make_repo):
    repo,base,head=packaged(make_repo)
    (repo/'src/calc/new_helper.py').write_text('VALUE=1\n')
    (repo/'src/calc/tests/test_value.py').write_text('from calc.new_helper import VALUE\ndef test_value(): assert VALUE==1\n')
    git(repo,'add','.');git(repo,'commit','-qm','helper');head=git(repo,'rev-parse','HEAD')
    p=run_cli(repo,base,head,'--overlay-tests','--json-out',str(repo/'r.json'))
    assert p.returncode==1
    d=json.loads((repo/'r.json').read_text())
    assert not d['suite_diff']['complete']
    assert not d['summary'].get('fix_confirmed')


@pytest.mark.parametrize('mode',['importlib','prepend','append'])
def test_overlay_keeps_all_supported_import_modes_on_selected_source(make_repo,mode):
    repo,base,head=packaged(make_repo)
    p=run_cli(repo,base,head,'--overlay-tests','--import-mode',mode,'--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    assert d['runs']['base']['source_verified']


def test_flat_test_file_overlay_also_retains_selected_code(make_repo):
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},{'calc.py':'VALUE=1\n','tests/test_value.py':'import calc\ndef test_value(): assert calc.VALUE==1\n'})
    p=run_cli(repo,base,head,'--overlay-tests','--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}


def test_explicit_fixture_manifest_exposes_head_fixture_effect(make_repo):
    repo,base,head=packaged(make_repo)
    (repo/'src/calc/conftest.py').write_text('import calc\ncalc.VALUE=1\n')
    git(repo,'add','.');git(repo,'commit','-qm','fixture changes behavior');head=git(repo,'rev-parse','HEAD')
    p=run_cli(repo,base,head,'--overlay-tests','--overlay-fixture','src/calc/conftest.py','--fail-on','no-control','--json-out',str(repo/'r.json'))
    assert p.returncode==2,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'passes_both':1}
    fixture=next(x for x in d['test_overlay']['files']['base'] if x['path'].endswith('conftest.py'))
    assert fixture['previous_sha256'] != fixture['head_sha256']


def test_packaged_overlay_with_xdist_worker_origins(make_repo):
    pytest.importorskip('xdist')
    repo,base,head=packaged(make_repo)
    (repo/'pytest.ini').write_text('[pytest]\naddopts=-n 2\n');git(repo,'add','.');git(repo,'commit','-qm','parallel config');head=git(repo,'rev-parse','HEAD')
    # The same config must also exist on base for this bounded mode.
    git(repo,'checkout',base)
    (repo/'pytest.ini').write_text('[pytest]\naddopts=-n 2\n');git(repo,'add','.');git(repo,'commit','-qm','same parallel config');base=git(repo,'rev-parse','HEAD')
    p=run_cli(repo,base,head,'--overlay-tests','--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    assert all(r['source_verified'] for r in d['runs'].values())


def test_overlay_rejects_directory_targets_before_running(make_repo):
    repo,base,head=packaged(make_repo)
    p=run_cli(repo,base,head,'--overlay-tests','--targets','src/calc/tests')
    assert p.returncode==1 and 'explicit Python test files' in p.stderr


def test_new_fixture_missing_on_base_can_be_explicit(make_repo):
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'}, {'calc.py':'VALUE=1\n','tests/conftest.py':'import pytest\n@pytest.fixture\ndef expected(): return 1\n','tests/test_value.py':'import calc\ndef test_value(expected): assert calc.VALUE==expected\n'})
    assert run_cli(repo,base,head,'--overlay-tests').returncode==1
    p=run_cli(repo,base,head,'--overlay-tests','--overlay-fixture','tests/conftest.py','--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    f=next(x for x in d['test_overlay']['files']['base'] if x['path'].endswith('conftest.py'))
    assert f['previous_sha256'] is None


def test_root_fixture_overlay_and_existing_packaged_helper(make_repo):
    repo,base,head=make_repo({'src/calc/__init__.py':'VALUE=0\n','src/calc/tests/__init__.py':'','src/calc/tests/helper.py':'EXPECTED=1\n'}, {'src/calc/__init__.py':'VALUE=1\n','conftest.py':'import pytest\n@pytest.fixture\ndef expected(): return 1\n','src/calc/tests/test_value.py':'import calc\nfrom .helper import EXPECTED\ndef test_value(expected): assert calc.VALUE==expected==EXPECTED\n'})
    p=run_cli(repo,base,head,'--overlay-tests','--overlay-fixture','conftest.py','--json-out',str(repo/'r.json'))
    assert p.returncode==0,p.stderr
    d=json.loads((repo/'r.json').read_text())
    assert d['summary']=={'fix_confirmed':1}
    assert d['runs']['base']['source_verified']
