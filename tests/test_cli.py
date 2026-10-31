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
    assert 'timed out' in proc.stderr


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
