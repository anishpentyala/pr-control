import pytest
import json
import subprocess
import sys
from pathlib import Path

from conftest import BUGGY_CALC, FIXED_CALC, FIX_TEST, git


def run_cli(repo: Path, base: str, head: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "prcontrol.cli", str(repo), "--base", base, "--head", head, *extra],
        capture_output=True, text=True,
    )


def test_fix_confirmed_verdict(make_repo):
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC, "tests/test_calc.py": FIX_TEST},
    )
    proc = run_cli(repo, base, head, "--json-out", str(repo / "r.json"), "--md-out", str(repo / "r.md"))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((repo / "r.json").read_text())
    assert report["base"] == base and report["head"] == head
    rows = {r["test"]: r for r in report["verdicts"]}
    assert len(rows) == 1
    row = next(iter(rows.values()))
    assert row["base"] == "failed" and row["head"] == "passed"
    assert row["verdict"] == "fix_confirmed"
    assert report["summary"]["fix_confirmed"] == 1
    md = (repo / "r.md").read_text()
    assert "fix_confirmed" in md and base[:7] in md


def test_toothless_test_passes_both_and_fail_on_no_control(make_repo):
    repo, base, head = make_repo(
        {"calc.py": FIXED_CALC},
        {"calc.py": FIXED_CALC + "\n", "tests/test_calc.py": FIX_TEST},
    )
    proc = run_cli(repo, base, head, "--fail-on", "no-control")
    assert proc.returncode == 2, proc.stderr
    assert "passes_both" in proc.stdout


def test_regression_introduced_fails_when_requested(make_repo):
    repo, base, head = make_repo(
        {"calc.py": FIXED_CALC, "tests/test_calc.py": FIX_TEST},
        {"calc.py": BUGGY_CALC, "tests/test_calc.py": FIX_TEST},
    )
    proc = run_cli(repo, base, head, "--targets", "tests/test_calc.py", "--fail-on", "regression")
    assert proc.returncode == 2
    assert "regression_introduced" in proc.stdout


def test_no_test_changes_requires_explicit_targets(make_repo):
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC},
    )
    proc = run_cli(repo, base, head)
    assert proc.returncode == 1
    assert "no test files changed" in proc.stderr


def test_same_commit_fails_closed(make_repo):
    repo, base, head = make_repo({"calc.py": FIXED_CALC}, {"calc.py": FIXED_CALC})
    proc = run_cli(repo, base, base)
    assert proc.returncode == 1
    assert "same commit" in proc.stderr


def test_missing_ref_fails_closed(make_repo):
    repo, base, head = make_repo({"calc.py": FIXED_CALC}, {"calc.py": FIXED_CALC})
    proc = run_cli(repo, base, "deadbeefdeadbeef")
    assert proc.returncode == 1
    assert "error" in proc.stderr


def test_worktrees_cleaned_up(make_repo):
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC, "tests/test_calc.py": FIX_TEST},
    )
    proc = run_cli(repo, base, head)
    assert proc.returncode == 0, proc.stderr
    assert len(git(repo, "worktree", "list").splitlines()) == 1  # only the main checkout


def test_explicit_targets_override_detection(make_repo):
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC, "tests/test_calc.py": FIX_TEST},
        {"calc.py": FIXED_CALC, "tests/test_calc.py": FIX_TEST},
    )
    # no test files changed in the diff, so without --targets this errors
    proc = run_cli(repo, base, head)
    assert proc.returncode == 1
    proc = run_cli(repo, base, head, "--targets", "tests/test_calc.py")
    assert proc.returncode == 0, proc.stderr
    assert "fix_confirmed" in proc.stdout


def test_src_layout_records_intended_imports(make_repo):
    repo, base, head = make_repo(
        {"src/calc/__init__.py": BUGGY_CALC},
        {"src/calc/__init__.py": FIXED_CALC, "tests/test_calc.py": FIX_TEST},
    )
    proc = run_cli(repo, base, head, "--json-out", str(repo / "r.json"))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((repo / "r.json").read_text())
    assert report['summary']['fix_confirmed'] == 1
    for ref in ('base', 'head'):
        assert report['runs'][ref]['source_verified']
        assert f'/{ref}/src/calc/__init__.py' in report['runs'][ref]['imports']['calc'][0]


def test_setup_error_never_confirms_fix(make_repo):
    test = "import pytest\nimport calc\n@pytest.fixture(autouse=True)\ndef setup():\n    assert calc.add(2,3)==5\ndef test_x():\n    assert True\n"
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary'].get('fix_confirmed', 0) == 0
    assert data['verdicts'][0]['verdict'] == 'unrunnable'


def test_new_api_collection_error_is_inconclusive(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC},
        {'calc.py': FIXED_CALC + '\nnew_api = 1\n', 'tests/test_calc.py': 'from calc import new_api\ndef test_api():\n    assert new_api == 1\n'})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'), '--fail-on', 'regression')
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert 'base' in data['collection_errors']
    assert data['summary'].get('fix_confirmed', 0) == 0
    assert all(r['verdict'] == 'inconclusive' for r in data['verdicts'])


def test_import_override_is_rejected(make_repo):
    test = 'import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parents[1]))\n' + FIX_TEST
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert 'source provenance mismatch' in data['collection_errors']['base']
    assert not data['runs']['base']['source_verified']


def test_skip_is_not_a_control(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC,
        'tests/test_calc.py': 'import pytest\n@pytest.mark.skip\ndef test_skip():\n    assert False\n'})
    proc = run_cli(repo, base, head, '--fail-on', 'no-control')
    assert proc.returncode == 2


def test_no_tests_is_environment_error(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': '# empty\n'})
    proc = run_cli(repo, base, head)
    assert proc.returncode == 1


def test_timeout_is_environment_error(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC,
        'tests/test_calc.py': 'import time\ndef test_slow():\n    time.sleep(10)\n'})
    proc = run_cli(repo, base, head, '--timeout', '1')
    assert proc.returncode == 1
    assert 'timed out' in proc.stdout


def test_node_selector_supported(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': FIX_TEST})
    proc = run_cli(repo, base, head, '--targets', 'tests/test_calc.py::test_add')
    assert proc.returncode == 0, proc.stderr
    assert 'fix_confirmed' in proc.stdout


def test_target_outside_repo_rejected(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC})
    proc = run_cli(repo, base, head, '--targets', '../external.py')
    assert proc.returncode == 1
    assert 'inside' in proc.stderr


def test_transition_without_import_provenance_is_not_confirmed(make_repo):
    test = 'from pathlib import Path\ndef test_file():\n    assert "a + b" in Path("calc.py").read_text()\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--fail-on', 'no-control')
    assert proc.returncode == 2
    assert 'transition_unverified_source' in proc.stdout


def test_teardown_error_is_not_confirmation(make_repo):
    test = 'import pytest\nimport calc\n@pytest.fixture(autouse=True)\ndef cleanup():\n    yield\n    assert calc.add(2,3)==5\ndef test_x():\n    assert True\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary'].get('fix_confirmed', 0) == 0


def test_parametrized_class_nodeids_are_exact(make_repo):
    test = 'import pytest\nfrom calc import add\nclass TestCalc:\n    @pytest.mark.parametrize("a,b", [(2,3),(1,2)])\n    def test_add(self,a,b):\n        assert add(a,b)==a+b\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 0, proc.stderr
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary']['fix_confirmed'] == 2
    assert all(r['test'].startswith('tests/test_calc.py::TestCalc::test_add[') for r in data['verdicts'])


def test_nonpositive_timeout_rejected(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': FIX_TEST})
    assert run_cli(repo, base, head, '--timeout', '0').returncode == 1


def test_empty_target_list_rejected(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': FIX_TEST})
    assert run_cli(repo, base, head, '--targets').returncode == 1


def test_timeout_stops_child_processes(make_repo, tmp_path):
    import os
    import time
    import pytest
    if os.name != 'posix':
        pytest.skip('POSIX process-group cleanup')
    sentinel = tmp_path / 'child-survived'
    child = f'from pathlib import Path; import time; time.sleep(2); Path({str(sentinel)!r}).write_text("bad")'
    test = f'import subprocess,sys,time\ndef test_spawn():\n    subprocess.Popen([sys.executable,"-c",{child!r}])\n    time.sleep(10)\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--timeout', '1')
    assert proc.returncode == 1
    time.sleep(2)
    assert not sentinel.exists()


def test_root_level_tests_are_not_production_sources(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': FIX_TEST})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary']['fix_confirmed'] == 1
    assert 'test_calc' not in data['runs']['base']['imports']


def test_unittest_setup_exception_not_fix_evidence(make_repo):
    test = 'import unittest\nimport calc\nclass TestCalc(unittest.TestCase):\n    def setUp(self):\n        self.value = calc.new_api\n    def test_value(self):\n        self.assertEqual(self.value,1)\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC+'\nnew_api=1\n', 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary'].get('fix_confirmed', 0) == 0
    assert data['verdicts'][0]['base'] == 'error'


def test_unexpected_call_exception_is_unrunnable(make_repo):
    test = 'import calc\ndef test_value():\n    if calc.add(2,3) != 5:\n        raise ValueError("environment or API mismatch")\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert data['summary'].get('fix_confirmed', 0) == 0


def test_unittest_subtest_failure_not_overwritten_by_pass(make_repo):
    test = 'import unittest\nfrom calc import add\nclass TestCalc(unittest.TestCase):\n    def test_values(self):\n        for a,b in [(2,3),(0,0)]:\n            with self.subTest(a=a,b=b):\n                self.assertEqual(add(a,b),a+b)\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    # pytest <9 reports the first subtest assertion as an ordinary failure;
    # pytest >=9 emits separate subtest reports, conservatively unrunnable.
    assert proc.returncode in (0, 1)
    assert data['verdicts'][0]['base'] in ('failed', 'error')
    assert data['verdicts'][0]['head'] == 'passed'
    assert data['verdicts'][0]['verdict'] in ('fix_confirmed', 'unrunnable')


def test_failure_evidence_kept_for_manual_interpretation(make_repo):
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': FIX_TEST})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'), '--md-out', str(repo / 'r.md'))
    assert proc.returncode == 0
    row = json.loads((repo / 'r.json').read_text())['verdicts'][0]
    assert row['base_failure_details'][0]['phase'] == 'call'
    assert row['base_failure_details'][0]['classification'] == 'failed'
    assert 'AssertionError' in row['base_failure_details'][0]['traceback']
    assert row['head_failure_details'] == []
    assert 'Failure evidence' in (repo / 'r.md').read_text()


def test_unexpected_exception_traceback_explains_unrunnable(make_repo):
    test = 'import calc\ndef test_x():\n    if calc.add(2,3)!=5:\n        raise RuntimeError("synthetic dependency mismatch")\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    row = json.loads((repo / 'r.json').read_text())['verdicts'][0]
    assert row['verdict'] == 'unrunnable'
    assert 'synthetic dependency mismatch' in row['base_failure_details'][0]['traceback']
    assert row['base_failure_details'][0]['classification'] == 'error'


def test_conditional_collection_missing_on_head_fails_closed(make_repo):
    test = 'from calc import add\nif add(2,3) != 5:\n    def test_only_on_base():\n        assert True\nelse:\n    def test_only_on_head():\n        assert True\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--fail-on', 'regression', '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert not data['suite_diff']['complete']


def test_setup_error_marks_suite_incomplete(make_repo):
    test = 'import pytest\n@pytest.fixture(autouse=True)\ndef setup():\n    raise RuntimeError("cannot prepare fixture")\ndef test_x():\n    assert True\n'
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 1
    data = json.loads((repo / 'r.json').read_text())
    assert not data['suite_diff']['complete']
    assert data['suite_diff']['execution_errors']['base'] == ['test_calc.py::test_x']


def test_pytest_raises_missing_exception_is_negative_control(make_repo):
    repo, base, head = make_repo(
        {"calc.py": "def validate():\n    return None\n"},
        {"calc.py": "def validate():\n    raise ValueError('invalid')\n",
         "tests/test_calc.py": "import pytest\nfrom calc import validate\ndef test_validate():\n    with pytest.raises(ValueError):\n        validate()\n"},
    )
    proc = run_cli(repo, base, head, "--json-out", str(repo / "r.json"))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((repo / "r.json").read_text())
    assert report["summary"].get("fix_confirmed") == 1


def test_explicit_pytest_fail_is_negative_control(make_repo):
    repo, base, head = make_repo(
        {"calc.py": "valid = False\n"},
        {"calc.py": "valid = True\n",
         "tests/test_calc.py": "import pytest\nfrom calc import valid\ndef test_valid():\n    if not valid:\n        pytest.fail('not valid')\n"},
    )
    proc = run_cli(repo, base, head, "--json-out", str(repo / "r.json"))
    assert proc.returncode == 0, proc.stderr
    report = json.loads((repo / "r.json").read_text())
    assert report["summary"].get("fix_confirmed") == 1


def test_unittest_setup_assertion_never_confirms_fix(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.TestCase):\n'
            '    def setUp(self):\n        self.assertEqual(calc.add(2,3),5)\n'
            '    def test_x(self):\n        self.assertTrue(True)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['verdicts'][0]['verdict'] == 'unrunnable'
    assert not data['suite_diff']['complete']


def test_unittest_teardown_assertion_never_confirms_fix(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.TestCase):\n'
            '    def tearDown(self):\n        self.assertEqual(calc.add(2,3),5)\n'
            '    def test_x(self):\n        self.assertTrue(True)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['verdicts'][0]['verdict'] == 'unrunnable'
    assert not data['suite_diff']['complete']


def test_unittest_cleanup_assertion_never_confirms_fix(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.TestCase):\n'
            '    def test_x(self):\n        self.addCleanup(self.assertEqual,calc.add(2,3),5)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['verdicts'][0]['verdict'] == 'unrunnable'
    assert not data['suite_diff']['complete']


def test_unittest_body_assertion_remains_negative_control(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.TestCase):\n'
            '    def test_x(self):\n        self.assertEqual(calc.add(2,3),5)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 0
    assert data['summary']['fix_confirmed'] == 1


def test_async_unittest_setup_assertion_never_confirms_fix(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.IsolatedAsyncioTestCase):\n'
            '    async def asyncSetUp(self):\n        self.assertEqual(calc.add(2,3),5)\n'
            '    async def test_x(self):\n        self.assertTrue(True)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['verdicts'][0]['verdict'] == 'unrunnable'


def test_async_unittest_teardown_assertion_never_confirms_fix(make_repo):
    test = ('import unittest\nimport calc\nclass TestCalc(unittest.IsolatedAsyncioTestCase):\n'
            '    async def asyncTearDown(self):\n        self.assertEqual(calc.add(2,3),5)\n'
            '    async def test_x(self):\n        self.assertTrue(True)\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'test_calc.py': test})
    proc = run_cli(repo, base, head, '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['verdicts'][0]['verdict'] == 'unrunnable'


@pytest.mark.parametrize("base_marked", [True, False])
def test_strict_xpass_never_confirms_a_fix_or_regression(make_repo, base_marked):
    test = ('import pytest, calc\n'
            '@pytest.mark.xfail(calc.marked, strict=True, reason="legacy condition")\n'
            'def test_add():\n    assert calc.add(2,3)==5\n')
    repo, base, head = make_repo(
        {'calc.py': FIXED_CALC + f'\nmarked = {base_marked}\n'},
        {'calc.py': FIXED_CALC + f'\nmarked = {not base_marked}\n', 'tests/test_calc.py': test},
    )
    proc = run_cli(repo, base, head, '--fail-on', 'no-control', '--json-out', str(repo / 'r.json'))
    data = json.loads((repo / 'r.json').read_text())
    assert proc.returncode == 1
    assert data['summary'] == {'unrunnable': 1}
    assert not data['suite_diff']['complete']
    details = data['verdicts'][0]['base_failure_details' if base_marked else 'head_failure_details']
    assert details[0]['classification'] == 'error'
    assert 'XPASS(strict)' in details[0]['traceback']


def test_xfail_condition_false_keeps_real_assertion_control(make_repo):
    test = ('import pytest, calc\n@pytest.mark.xfail(False, strict=True, reason="inactive")\n'
            'def test_add():\n    assert calc.add(2,3)==5\n')
    repo, base, head = make_repo({'calc.py': BUGGY_CALC}, {'calc.py': FIXED_CALC, 'tests/test_calc.py': test})
    proc = run_cli(repo, base, head, '--fail-on', 'no-control', '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 0
    assert json.loads((repo / 'r.json').read_text())['summary'] == {'fix_confirmed': 1}


@pytest.mark.parametrize("strict", [False, True])
def test_expected_xfail_is_not_assertion_failure_evidence(make_repo, strict):
    test = ('import pytest, calc\n'
            f'@pytest.mark.xfail(calc.marked, strict={strict}, reason="known bug")\n'
            'def test_add():\n    assert calc.add(2,3)==5\n')
    repo, base, head = make_repo(
        {'calc.py': BUGGY_CALC + '\nmarked = True\n'},
        {'calc.py': FIXED_CALC + '\nmarked = False\n', 'tests/test_calc.py': test},
    )
    proc = run_cli(repo, base, head, '--fail-on', 'no-control', '--json-out', str(repo / 'r.json'))
    assert proc.returncode == 2
    assert json.loads((repo / 'r.json').read_text())['summary'].get('fix_confirmed', 0) == 0
