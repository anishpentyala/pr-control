"""Overlay controls must not silently run with different native pytest configs."""
from pathlib import Path
import json
import subprocess
import sys
import pytest
from prcontrol.overlay import check_overlay_context

@pytest.mark.parametrize('name',['pytest.toml','.pytest.toml'])
@pytest.mark.parametrize('parent',['.','tests'])
@pytest.mark.parametrize('change',['changed','added','removed','empty-added'])
def test_native_config_differences_rejected_before_overlay(tmp_path,name,parent,change):
    head,base=tmp_path/'head',tmp_path/'base'
    for root in [head,base]:
        (root/'tests').mkdir(parents=True)
        (root/'tests/test_one.py').write_text('def test_one():pass\n')
    content='[pytest]\nlog_level="INFO"\n'
    if change in ['changed','added','empty-added']:
        (head/parent/name).write_text('' if change=='empty-added' else content)
    if change in ['changed','removed']:
        (base/parent/name).write_text('[pytest]\nlog_level="WARNING"\n')
    with pytest.raises(ValueError,match='overlay context differs'):
        check_overlay_context(head,base,['tests/test_one.py'])

@pytest.mark.parametrize('name',['pytest.toml','.pytest.toml'])
@pytest.mark.parametrize('content',['','[pytest]\nlog_level="INFO"\n'])
def test_identical_native_configs_recorded_as_context(tmp_path,name,content):
    roots=[tmp_path/'head',tmp_path/'base']
    for root in roots:
        (root/'tests').mkdir(parents=True)
        (root/'tests/test_one.py').write_text('def test_one():pass\n')
        (root/name).write_text(content)
    rows=check_overlay_context(*roots,['tests/test_one.py'])
    assert [r['path'] for r in rows]==[name]
    assert rows[0]['head_sha256']==rows[0]['selected_sha256']

@pytest.mark.skipif(int(pytest.__version__.split(".")[0]) < 9, reason="native TOML runtime selection requires pytest9")
def test_native_config_change_rejects_control_comparison(make_repo,tmp_path):
    repo,base,head=make_repo(
        {'calc.py':'VALUE=1\n','pytest.toml':'[pytest]\nlog_level="WARNING"\n'},
        {'pytest.toml':'[pytest]\nlog_level="INFO"\n','test_one.py':
         'import calc\ndef test_one(pytestconfig):\n assert calc.VALUE==1\n assert pytestconfig.getini("log_level")=="INFO"\n'})
    output=tmp_path/'report.json'
    proc=subprocess.run([sys.executable,'-m','prcontrol.cli',str(repo),'--base',base,'--head',head,
        '--targets','test_one.py','--overlay-tests','--json-out',str(output),'--fail-on','no-control'],
        capture_output=True,text=True)
    assert proc.returncode==1 and 'overlay context differs' in proc.stderr, 'native pytest config differences must not qualify as a source fix'
    assert not output.exists()

@pytest.mark.parametrize('name',['pytest.toml','.pytest.toml'])
def test_native_config_context_has_literal_rejection_witness(tmp_path,name):
    head,base=tmp_path/'head',tmp_path/'base'
    for root in [head,base]:
        (root/'tests').mkdir(parents=True)
        (root/'tests/test_one.py').write_text('def test_one():pass\n')
    (head/name).write_text('[pytest]\nlog_level="INFO"\n')
    (base/name).write_text('[pytest]\nlog_level="WARNING"\n')
    try:check_overlay_context(head,base,['tests/test_one.py'])
    except ValueError:return
    pytest.fail('native pytest config changes must not bypass overlay context checks')
