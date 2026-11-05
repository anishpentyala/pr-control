"""Origin caching must not turn repeated or generated calls into fake evidence."""
import json
import subprocess
import sys

from test_per_test_source import cases, run


def test_distinct_code_identity_with_changed_filename_still_rejected(make_repo, tmp_path):
    code = '''import calc,types
def test_fix():
 calc.value()
 other=types.FunctionType(calc.value.__code__.replace(co_filename='/outside/calc.py'),calc.__dict__)
 assert other()==1
'''
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 1
    assert 'per-test source provenance mismatch' in data['collection_errors']['base']


def test_same_code_new_module_name_cannot_borrow_prior_decision(make_repo, tmp_path):
    code = '''import calc,types
def test_fix():
 globals_other={'__name__':'unrelated'}
 other=types.FunctionType(calc.value.__code__,globals_other)
 other()
 assert calc.value()==1
'''
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 0
    assert data['summary'] == {'fix_confirmed': 1}


def test_repeated_calls_do_not_repeat_path_resolution(make_repo, tmp_path):
    code = '''import calc
from pathlib import Path
COUNT=0
resolve=Path.resolve
def counted(self,*args,**kwargs):
 global COUNT
 COUNT+=1
 return resolve(self,*args,**kwargs)
def test_fix():
 Path.resolve=counted
 try:
  values=[calc.value() for _ in range(1000)]
 finally:Path.resolve=resolve
 assert COUNT<10, 'same production origin must not be resolved for every call'
 assert sum(values)==1000
'''
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 0
    assert data['summary'] == {'fix_confirmed': 1}


def test_over_cache_limit_keeps_observing_new_origins(make_repo, tmp_path):
    code = '''import calc,types
def test_fix():
 funcs=[types.FunctionType(calc.value.__code__.replace(co_name='generated'+str(i)),calc.__dict__) for i in range(4200)]
 for fn in funcs:fn()
 outside=types.FunctionType(calc.value.__code__.replace(co_filename='/outside/calc.py'),calc.__dict__)
 assert outside()==1
'''
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 1
    assert 'per-test source provenance mismatch' in data['collection_errors']['base']
