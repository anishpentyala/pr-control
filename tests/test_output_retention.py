"""Noisy trusted tests must not fill the verifier's own heap."""
import gc
import tracemalloc
import pytest
from prcontrol.core import make_worktree, cleanup, run_pytest


@pytest.mark.parametrize('fd',[1,2])
def test_large_native_output_does_not_enter_verifier_heap(make_repo,tmp_path,monkeypatch,fd):
    test=f'''import os
import sample

def test_noisy():
    chunk=b"x"*65536
    for i in range(512): os.write({fd},chunk)
    assert sample.VALUE==1
'''
    repo,base,head=make_repo({'sample.py':'VALUE=0\n'}, {'sample.py':'VALUE=1\n','test_noisy.py':test})
    code=make_worktree(repo,head,tmp_path/'head')
    monkeypatch.setenv('PYTEST_ADDOPTS','-s')
    gc.collect();tracemalloc.start()
    try:
        result=run_pytest(code,code,'head',['test_noisy.py'],30)
        _,peak=tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop();cleanup(repo,[code])
    assert result.collection_error is None
    assert result.returncode==0 and result.source_verified
    assert peak<6*1024*1024,f'full output retained: peak={peak}'


@pytest.mark.parametrize('fd',[1,2])
def test_missing_report_keeps_final_diagnostic_tail(make_repo,tmp_path,monkeypatch,fd):
    test=f'''import os

def test_exit():
    os.write({fd},b'x'*100000+b'\\xffFINAL-DIAGNOSTIC')
    os._exit(3)
'''
    repo,base,head=make_repo({'sample.py':'VALUE=0\n'}, {'sample.py':'VALUE=1\n','test_tail.py':test})
    code=make_worktree(repo,head,tmp_path/'head')
    monkeypatch.setenv('PYTEST_ADDOPTS','-s')
    try: result=run_pytest(code,code,'head',['test_tail.py'],30)
    finally: cleanup(repo,[code])
    assert 'FINAL-DIAGNOSTIC' in result.collection_error
    assert '\ufffd' in result.collection_error
    assert len(result.collection_error)<600


def test_diagnostic_tail_is_bounded_even_at_split_utf8():
    import subprocess,sys
    from prcontrol.core import _capture_output
    code="import os;os.write(1,b'x'*20000+b'\\xe2\\x82\\xac'+b'y'*16382)"
    proc=subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    tail,stderr=_capture_output(proc,10)
    assert tail.startswith('\ufffd')
    assert len(tail)<=16384
    assert tail.endswith('y'*16382)
    assert stderr==''


@pytest.mark.skipif(__import__('os').name!='posix',reason='process-group cleanup is POSIX')
@pytest.mark.parametrize('fd',[1,2])
def test_child_inherited_output_stays_inside_timeout(make_repo,tmp_path,monkeypatch,fd):
    import time
    sentinel=tmp_path/'surviving-child'
    child=f'import time;from pathlib import Path;time.sleep(2);Path({str(sentinel)!r}).write_text("survived")'
    test=f'import subprocess,sys\nimport sample\ndef test_spawn():\n subprocess.Popen([sys.executable,"-c",{child!r}],stdout=None if {fd}==1 else subprocess.DEVNULL,stderr=None if {fd}==2 else subprocess.DEVNULL)\n assert sample.VALUE==1\n'
    repo,base,head=make_repo({'sample.py':'VALUE=0\n'},{'sample.py':'VALUE=1\n','test_child.py':test})
    code=make_worktree(repo,head,tmp_path/'head')
    monkeypatch.setenv('PYTEST_ADDOPTS','-s')
    try: result=run_pytest(code,code,'head',['test_child.py'],1)
    finally: cleanup(repo,[code])
    time.sleep(2.2)
    assert result.returncode==-1 and 'timed out' in (result.collection_error or '')
    assert not sentinel.exists(),'inherited-output child survived timeout'
