"""An optional call-frame check cannot borrow a sibling's source observations."""
import json
import os
import subprocess
import sys

import pytest


def run(repo, base, head, tmp_path, enabled=True, repeats=1):
    out = tmp_path / 'report.json'
    args = ['--per-test-source'] if enabled else []
    proc = subprocess.run([sys.executable, '-m', 'prcontrol.cli', str(repo), '--base', base,
        '--head', head, '--targets', 'test_calc.py', '--json-out', str(out),
        '--fail-on', 'no-control', '--repeats', str(repeats), *args],
        capture_output=True, text=True, timeout=30)
    assert out.exists(), proc.stderr
    return proc, json.loads(out.read_text())


def cases(make_repo, code, conf=''):
    return make_repo({'calc.py': 'def value():return 0\n', 'marker': 'base'}, {
        'calc.py': 'def value():return 1\n', 'marker': 'head',
        'test_calc.py': code, 'conftest.py': conf})


@pytest.mark.parametrize('parallel', [False, True])
@pytest.mark.parametrize('enabled', [False, True])
def test_sibling_import_cannot_lend_per_node_evidence(make_repo, tmp_path, monkeypatch, parallel, enabled):
    if parallel:
        pytest.importorskip('xdist'); monkeypatch.setenv('PYTEST_ADDOPTS', '-n 2')
    repo, base, head = cases(make_repo, '''from pathlib import Path

def test_a_source():
 import calc
 assert calc.value() in (0,1)

def test_b_marker():
 assert Path('marker').read_text() == 'head'
''')
    proc, data = run(repo, base, head, tmp_path, enabled)
    row = next(row for row in data['verdicts'] if row['test'].endswith('test_b_marker'))
    assert row['verdict'] == ('transition_unverified_source' if enabled else 'fix_confirmed')
    assert proc.returncode == (2 if enabled else 0)
    if enabled:
        assert data['runs']['base']['test_source']['test_calc.py::test_b_marker'] == []


@pytest.mark.parametrize('parallel', [False, True])
def test_actual_local_function_calls_record_exact_selected_paths(make_repo, tmp_path, monkeypatch, parallel):
    if parallel:
        pytest.importorskip('xdist'); monkeypatch.setenv('PYTEST_ADDOPTS', '-n 2')
    repo, base, head = cases(make_repo, 'import calc\ndef test_fix():assert calc.value()==1\n')
    proc, data = run(repo, base, head, tmp_path, repeats=2)
    assert proc.returncode == 0, proc.stderr
    assert data['summary'] == {'fix_confirmed': 1}
    for ref in ('base', 'head'):
        paths = data['runs'][ref]['test_source']['test_calc.py::test_fix']
        assert len(paths) == 1 and paths[0].endswith('/' + ref + '/calc.py')
        assert all(a['test_source']['test_calc.py::test_fix'] for a in data['runs'][ref]['attempts'])


def test_setup_only_local_calls_do_not_prove_call_phase(make_repo, tmp_path):
    code = "from pathlib import Path\ndef test_marker():assert Path('marker').read_text()=='head'"
    conf = 'import pytest,calc\n@pytest.fixture(autouse=True)\ndef setup():calc.value()\n'
    repo, base, head = cases(make_repo, code, conf)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 2
    assert data['summary'] == {'transition_unverified_source': 1}


@pytest.mark.parametrize('behavior', ['constant', 'subprocess', 'thread', 'disable-profiler'])
def test_no_observed_local_call_is_not_confirmed(make_repo, tmp_path, behavior):
    if behavior == 'constant':
        code = 'import calc\ndef test_fix():assert calc.VALUE==1\n'
        repo, base, head = make_repo({'calc.py': 'VALUE=0'}, {'calc.py': 'VALUE=1', 'test_calc.py': code})
    else:
        bodies = {
            'subprocess': "import subprocess,sys\ndef test_fix():assert subprocess.check_output([sys.executable,'-c','import calc; print(calc.value())']).strip()==b'1'",
            'thread': 'import threading,calc\ndef test_fix():\n result=[]\n t=threading.Thread(target=lambda:result.append(calc.value()))\n t.start();t.join()\n assert result==[1]',
            'disable-profiler': 'import sys,calc\ndef test_fix():\n sys.setprofile(None)\n assert calc.value()==1',
        }
        repo, base, head = cases(make_repo, bodies[behavior])
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 2
    assert data['summary'] == {'transition_unverified_source': 1}


def test_existing_profiler_is_not_replaced(make_repo, tmp_path):
    conf = 'import sys\ndef pytest_sessionstart(session):sys.setprofile(lambda *args:None)\n'
    repo, base, head = cases(make_repo, 'import calc\ndef test_fix():assert calc.value()==1', conf)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 2
    assert data['summary'] == {'transition_unverified_source': 1}


def test_duplicate_occurrence_needs_evidence_every_time(make_repo, tmp_path):
    code = '''import calc
from pathlib import Path
COUNT=0
def test_fix():
 global COUNT
 COUNT+=1
 if COUNT==1:calc.value()
 assert Path('marker').read_text()=='head'
'''
    conf = 'def pytest_collection_modifyitems(items):items.extend(list(items))\n'
    repo, base, head = cases(make_repo, code, conf)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 2
    assert data['summary'] == {'transition_unverified_source': 1}


def test_package_contained_test_call_is_not_production_source(make_repo, tmp_path):
    code = "from pathlib import Path\ndef test_marker():assert Path('marker').read_text()=='head'"
    repo, base, head = make_repo({'src/calc/__init__.py': '', 'marker': 'base'}, {
        'marker': 'head', 'src/calc/tests/test_marker.py': code})
    out = tmp_path / 'report.json'
    proc = subprocess.run([sys.executable, '-m', 'prcontrol.cli', str(repo), '--base', base,
        '--head', head, '--overlay-tests', '--targets', 'src/calc/tests/test_marker.py', '--per-test-source',
        '--json-out', str(out), '--fail-on', 'no-control'], capture_output=True, text=True)
    assert out.exists(), proc.stderr
    assert proc.returncode == 2
    assert json.loads(out.read_text())['summary'] == {'transition_unverified_source': 1}


@pytest.mark.parametrize('parallel', [False, True])
def test_wrong_worktree_call_paths_fail_closed(make_repo, tmp_path, monkeypatch, parallel):
    if parallel:
        pytest.importorskip('xdist'); monkeypatch.setenv('PYTEST_ADDOPTS', '-n 2')
    code = '''import calc
from pathlib import Path
import types
# Preserve selected imports but use a forged non-selected frame filename.
calc.value=types.FunctionType(calc.value.__code__.replace(co_filename='/outside/calc.py'),calc.__dict__)
def test_fix():assert calc.value()==1
'''
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 1
    assert 'per-test source provenance mismatch' in data['collection_errors']['base']
    assert data['summary'] == {}
    assert data['suite_diff']['complete'] is False


def test_optional_paths_are_shape_checked():
    from prcontrol.probe_report import valid_probe_report
    data = {'tests': {}, 'exitstatus': 0, 'imports': {}, 'collection_errors': [],
            'collected': [], 'finished': [], 'test_source': {'test_a': [False]}}
    assert not valid_probe_report(data, 0)
    data['test_source'] = {'test_a': ['/tmp/calc.py']}
    assert not valid_probe_report(data, 0)  # no outcome exists for this source key
    data.update(tests={'test_a': {'call': 'passed'}},
                collected=['test_a'], finished=['test_a'])
    assert valid_probe_report(data, 0)


def test_markdown_names_enabled_gate():
    from prcontrol.report import render_markdown
    payload = {'repo': '.', 'base': 'base', 'head': 'head', 'per_test_source': True,
        'env': {'python': '3', 'pytest': '9', 'platform': 'test'}, 'verdicts': [], 'summary': {},
        'suite_diff': {'complete': True, 'only_on_base': [], 'only_on_head': [],
                      'base_failed': [], 'head_failed': [], 'new_failures_on_head': [], 'fixed_on_head': []}}
    assert 'per-test source gate' in render_markdown(payload)


@pytest.mark.parametrize('filename', ['<string>', '<generated>', '<stdin>'])
def test_generated_pseudo_filename_cannot_supply_local_file_evidence(make_repo, tmp_path, filename):
    code = "import calc\nfrom pathlib import Path\nexec(compile('def generated():return 1'," + repr(filename) + ", 'exec'),calc.__dict__)\ndef test_fix():\n calc.generated()\n assert Path('marker').read_text()=='head'\n"
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 2
    assert data['summary'] == {'transition_unverified_source': 1}
    assert data['runs']['base']['test_source']['test_calc.py::test_fix'] == []


@pytest.mark.parametrize('method', ['setUp', 'tearDown'])
def test_unittest_lifecycle_calls_count_inside_pytest_call_phase(make_repo, tmp_path, method):
    code = '''import unittest,calc
from pathlib import Path
class TestMarker(unittest.TestCase):
 def METHOD(self):calc.value()
 def test_marker(self):self.assertEqual(Path('marker').read_text(),'head')
'''.replace('METHOD', method)
    repo, base, head = cases(make_repo, code)
    proc, data = run(repo, base, head, tmp_path)
    assert proc.returncode == 0
    assert data['summary'] == {'fix_confirmed': 1}
    assert data['runs']['base']['test_source']['test_calc.py::TestMarker::test_marker']
