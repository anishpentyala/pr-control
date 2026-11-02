import json

import pytest

from conftest import BUGGY_CALC, FIXED_CALC
from test_cli import run_cli


@pytest.mark.parametrize("duplicate_on", ["base", "head"])
@pytest.mark.parametrize("copies", [2, 3])
@pytest.mark.parametrize("fail_on", ["never", "no-control", "regression"])
def test_changed_test_occurrence_counts_do_not_verify(make_repo, duplicate_on, copies, fail_on):
    conftest = '''import calc

def pytest_collection_modifyitems(session, config, items):
    duplicate = calc.add(2,3) == 5
    if duplicate == DUPLICATE_ON_HEAD:
        items.extend(items[:] * COPIES)
'''.replace("DUPLICATE_ON_HEAD", repr(duplicate_on == "head")).replace("COPIES", str(copies - 1))
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC, "conftest.py": conftest,
         "tests/test_calc.py": "from calc import add\ndef test_add():\n    assert add(2,3)==5\n"},
    )
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path), "--md-out", str(repo / "report.md"), "--fail-on", fail_on)
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert data["summary"].get("fix_confirmed", 0) == 0
    node = "tests/test_calc.py::test_add"
    assert data["suite_diff"]["occurrence_mismatches"][node] == {
        "base": copies if duplicate_on == "base" else 1,
        "head": copies if duplicate_on == "head" else 1,
    }
    assert data["summary"] == {"inconclusive": 1}
    markdown = (repo / "report.md").read_text()
    assert "Test occurrence count mismatches" in markdown
    assert f"base {copies if duplicate_on == 'base' else 1}, head {copies if duplicate_on == 'head' else 1}" in markdown


@pytest.mark.parametrize("copies", [2, 3])
def test_equal_duplicate_occurrences_remain_complete(make_repo, copies):
    conftest = f"def pytest_collection_modifyitems(items):\n    items.extend(items[:] * {copies-1})\n"
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC, "conftest.py": conftest,
         "tests/test_calc.py": "from calc import add\ndef test_add():\n    assert add(2,3)==5\n"},
    )
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 0, proc.stderr
    assert data["suite_diff"]["complete"]
    assert data["suite_diff"].get("occurrence_mismatches", {}) == {}
    assert data["summary"] == {"fix_confirmed": 1}


@pytest.mark.parametrize("outcome", ["pass", "skip"])
def test_occurrence_mismatch_is_incomplete_even_without_assertion_failures(make_repo, outcome):
    conftest = "import calc\ndef pytest_collection_modifyitems(items):\n    if calc.add(2,3)==5:\n        items.extend(items[:])\n"
    body = "import pytest\nfrom calc import add\ndef test_add():\n    add(1,2)\n"
    body += "    pytest.skip('not supported')\n" if outcome == "skip" else "    assert True\n"
    repo, base, head = make_repo(
        {"calc.py": BUGGY_CALC},
        {"calc.py": FIXED_CALC, "conftest.py": conftest, "tests/test_calc.py": body},
    )
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert data["summary"] == {"inconclusive": 1}
