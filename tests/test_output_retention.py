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
    import tempfile
    from prcontrol.core import _output_tail
    with tempfile.TemporaryFile() as stream:
        stream.write(b'x'*20000+b'\xe2\x82\xac'+b'y'*16382)
        tail=_output_tail(stream)
    assert tail.startswith('\ufffd')
    assert len(tail)<=16384
    assert tail.endswith('y'*16382)
