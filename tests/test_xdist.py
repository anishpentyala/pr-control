"""Real xdist subprocesses return collection and source evidence to controller."""
import json
import pytest
from test_cli import run_cli

pytest.importorskip("xdist")


@pytest.mark.parametrize("workers", [1, 2])
@pytest.mark.parametrize("result", ["fix", "runtime", "setup", "strict-xpass", "shadow"])
def test_worker_outcomes_and_source_provenance(make_repo, monkeypatch, workers, result):
    bodies = {
        "fix": "import calc\ndef test_value():assert calc.VALUE==1\n",
        "runtime": "import calc\ndef test_value():\n if calc.VALUE==0:raise RuntimeError('not an assertion')\n",
        "setup": "import calc,pytest\n@pytest.fixture(autouse=True)\ndef setup():assert calc.VALUE==1\ndef test_value():pass\n",
        "strict-xpass": "import calc,pytest\n@pytest.mark.xfail(calc.VALUE==0,strict=True)\ndef test_value():assert True\n",
        "shadow": "import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).resolve().parent))\nimport calc\ndef test_value():assert calc.VALUE==1\n",
    }
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, {"calc.py": "VALUE=1\n", "test_calc.py": bodies[result]})
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-n {workers}")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == (0 if result == "fix" else 1), (proc.stderr, data)
    assert data["suite_diff"]["complete"] == (result == "fix")
    if result == "fix":
        assert data["summary"] == {"fix_confirmed": 1}
        for ref in ("base", "head"):
            assert data["runs"][ref]["source_verified"]
            assert data["runs"][ref]["collected_counts"] == {"test_calc.py::test_value": 1}
            assert all(f"/{ref}/calc.py" in p for p in data["runs"][ref]["imports"]["calc"])
    else:
        assert data["summary"].get("fix_confirmed", 0) == 0
        if result == "shadow":
            assert "source provenance mismatch" in data["collection_errors"]["base"]
        else:
            assert data["verdicts"][0]["base_failure_details"]


@pytest.mark.parametrize("copies", [1, 2, 3])
def test_parallel_collection_is_not_multiplied_by_worker_count(make_repo, monkeypatch, copies):
    hook = f"def pytest_collection_modifyitems(items):\n items.extend(items[:] * {copies-1})\n"
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, {"calc.py": "VALUE=1\n", "conftest.py": hook, "test_calc.py": "import calc\ndef test_value():assert calc.VALUE==1\n"})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 0, (proc.stderr, data)
    assert data["suite_diff"]["complete"]
    assert data["summary"] == {"fix_confirmed": 1}
    for ref in ("base", "head"):
        assert data["runs"][ref]["collected_counts"] == {"test_calc.py::test_value": copies}


def test_worker_crash_stays_incomplete(make_repo, monkeypatch):
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, {"calc.py": "VALUE=1\n", "test_calc.py": "import os,calc\ndef test_value():os._exit(3)\n"})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2 --max-worker-restart=0")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert data["summary"].get("fix_confirmed", 0) == 0
    assert "xdist worker failed" in data["collection_errors"]["base"]


@pytest.mark.parametrize("mode", ["load", "loadscope", "loadfile", "worksteal"])
def test_multiple_modules_use_worker_imports(make_repo, monkeypatch, mode):
    files = {"calc.py": "VALUE=1\n"}
    for i in range(4):
        files[f"tests/test_{i}.py"] = f"import calc\ndef test_value_{i}():assert calc.VALUE==1\n"
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, files)
    monkeypatch.setenv("PYTEST_ADDOPTS", f"-n 2 --dist {mode}")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 0, (proc.stderr, data)
    assert data["suite_diff"]["complete"]
    assert data["summary"] == {"fix_confirmed": 4}
    for ref in ("base", "head"):
        assert len(data["runs"][ref]["collected_counts"]) == 4
        assert set(data["runs"][ref]["collected_counts"].values()) == {1}
        assert data["runs"][ref]["source_verified"]


def test_parallel_early_stop_does_not_verify(make_repo, monkeypatch):
    files = {"calc.py": "VALUE=1\n", "test_calc.py": "import calc\n" + "\n".join(f"def test_value_{i}():assert calc.VALUE==1" for i in range(8))}
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, files)
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2 -x")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert "not all collected tests ran" in data["collection_errors"]["base"]


def test_parallel_execution_without_observed_source_never_confirms(make_repo, monkeypatch):
    test = "def test_value():\n import pathlib\n assert pathlib.Path('value.txt').read_text()=='1'\n"
    repo, base, head = make_repo({"value.txt": "0"}, {"value.txt": "1", "test_calc.py": test})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 0
    assert data["suite_diff"]["complete"]
    assert data["summary"] == {"transition_unverified_source": 1}
    assert not data["runs"]["base"]["source_verified"]


def test_worker_collection_mismatch_fails_closed(make_repo, monkeypatch):
    hook = '''import os

def pytest_collection_modifyitems(items):
    if os.environ.get("PYTEST_XDIST_WORKER") == "gw1":
        items[:] = []
'''
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, {"calc.py": "VALUE=1\n", "conftest.py": hook, "test_calc.py": "import calc\ndef test_value():assert calc.VALUE==1\n"})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert data["summary"].get("fix_confirmed", 0) == 0
    assert data["collection_errors"]


def test_missing_worker_evidence_stays_incomplete(make_repo, monkeypatch):
    hook = '''import pytest

@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session):
    if hasattr(session.config, "workeroutput"):
        session.config.workeroutput.pop("pr_control_evidence", None)
'''
    repo, base, head = make_repo({"calc.py": "VALUE=0\n"}, {"calc.py": "VALUE=1\n", "conftest.py": hook, "test_calc.py": "import calc\ndef test_value():assert calc.VALUE==1\n"})
    monkeypatch.setenv("PYTEST_ADDOPTS", "-n 2")
    path = repo / "report.json"
    proc = run_cli(repo, base, head, "--json-out", str(path))
    data = json.loads(path.read_text())
    assert proc.returncode == 1
    assert not data["suite_diff"]["complete"]
    assert data["summary"].get("fix_confirmed", 0) == 0
    assert "no source evidence" in data["collection_errors"]["base"]
