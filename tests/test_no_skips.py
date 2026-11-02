"""An explicit no-skips policy prevents missing optional tests from passing CI."""
import json
import pytest
from test_cli import run_cli

@pytest.mark.parametrize('parallel',[False,True])
@pytest.mark.parametrize('skip',['always','base','head','module'])
@pytest.mark.parametrize('strict',[False,True])
def test_mixed_control_and_skip_policy(make_repo,monkeypatch,parallel,skip,strict):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 2')
    condition={'always':'True','base':'calc.VALUE == 0','head':'calc.VALUE == 1'}
    extra='import calc,pytest\n'
    if skip=='module':extra+='pytest.skip("optional unavailable",allow_module_level=True)\n'
    else:extra+=f'@pytest.mark.skipif({condition[skip]},reason="optional unavailable")\ndef test_optional():assert True\n'
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},
        {'calc.py':'VALUE=1\n','tests/test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1\n',
         'tests/test_optional.py':extra})
    args=['--json-out',str(repo/'evidence.json'),'--md-out',str(repo/'evidence.md'),'--fail-on','no-control']
    if strict:args+=['--require-no-skips']
    proc=run_cli(repo,base,head,*args)
    assert proc.returncode==(1 if strict else 0),proc.stderr
    data=json.loads((repo/'evidence.json').read_text())
    assert any('skipped' in (r['base'],r['head']) for r in data['verdicts']) or skip=='module'
    assert data['require_no_skips'] is strict
    if skip=='module':
        assert all(r['collection_skips'] for r in data['runs'].values())
        assert 'skipped during collection' in (repo/'evidence.md').read_text()
    if strict:assert '--require-no-skips' in proc.stderr

@pytest.mark.parametrize('strict',[False,True])
def test_no_skips_keeps_real_fix_result(make_repo,strict):
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},
        {'calc.py':'VALUE=1\n','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1\n'})
    proc=run_cli(repo,base,head,*(['--require-no-skips'] if strict else []),'--fail-on','no-control')
    assert proc.returncode==0,proc.stderr

@pytest.mark.parametrize('phase',['setup','call','teardown','xfail','importorskip'])
@pytest.mark.parametrize('parallel',[False,True])
def test_no_skips_rejects_other_skip_forms(make_repo,monkeypatch,phase,parallel):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 2')
    code={
        'setup':'@pytest.fixture\ndef optional():pytest.skip("setup")\ndef test_optional(optional):pass\n',
        'call':'def test_optional():pytest.skip("call")\n',
        'teardown':'@pytest.fixture\ndef optional():\n yield\n pytest.skip("teardown")\ndef test_optional(optional):pass\n',
        'xfail':'@pytest.mark.xfail(reason="known")\ndef test_optional():assert False\n',
        'importorskip':'pytest.importorskip("not_installed_pr_control_optional_dependency")\n',
    }[phase]
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},
        {'calc.py':'VALUE=1\n','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1\n',
         'test_optional.py':'import pytest\n'+code})
    proc=run_cli(repo,base,head,'--require-no-skips','--fail-on','no-control')
    assert proc.returncode==1,proc.stderr
    assert '--require-no-skips' in proc.stderr

@pytest.mark.parametrize('strict',[False,True])
@pytest.mark.parametrize('parallel',[False,True])
def test_duplicate_skip_is_not_hidden_by_later_failure(make_repo,monkeypatch,strict,parallel):
    if parallel:
        pytest.importorskip('xdist')
        monkeypatch.setenv('PYTEST_ADDOPTS','-n 1')
    repo,base,head=make_repo({'calc.py':'VALUE=0\n'},
        {'calc.py':'VALUE=1\n','test_fix.py':'import calc\ndef test_fix():assert calc.VALUE==1\n',
         'conftest.py':'def pytest_collection_modifyitems(items):\n items.extend([i for i in items if i.name=="test_repeat"])\n',
         'test_repeat.py':'import pytest,calc\ndef test_repeat():\n calc.count=getattr(calc,"count",0)+1\n if calc.count==1:pytest.skip("first occurrence")\n assert False\n'})
    proc=run_cli(repo,base,head,'--targets','test_fix.py','test_repeat.py',
                 '--fail-on','no-control',*(['--require-no-skips'] if strict else []))
    assert proc.returncode==(1 if strict else 0),proc.stderr
    if strict:assert '--require-no-skips' in proc.stderr
