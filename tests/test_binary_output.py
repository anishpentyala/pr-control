"""A trusted test may write arbitrary bytes through a native extension."""

import json
import pytest
from test_cli import run_cli


@pytest.mark.parametrize("fd", [1, 2])
@pytest.mark.parametrize("result", ["pass", "fail", "no_report"])
def test_non_utf8_test_output_does_not_crash_verifier(
    make_repo, monkeypatch, fd, result
):
    trailer = {
        "pass": "assert sample.VALUE == 1",
        "fail": "assert False",
        "no_report": "os._exit(3)",
    }[result]
    test = f'import sample\ndef test_value():\n import os\n os.write({fd},b"\\xff\\xfe")\n {trailer}\n'
    repo, base, head = make_repo(
        {"sample.py": "VALUE=0\n"}, {"sample.py": "VALUE=1\n", "test_sample.py": test}
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-s")
    output = repo / "result.json"
    proc = run_cli(repo, base, head, "--json-out", str(output))
    assert "UnicodeDecodeError" not in proc.stderr
    assert output.exists(), proc.stderr
    report = json.loads(output.read_text())
    if result == "no_report":
        assert proc.returncode == 1
        assert not report["suite_diff"]["complete"]
        assert all(
            "no valid report" in error for error in report["collection_errors"].values()
        )
    else:
        assert proc.returncode == 0, proc.stderr
        assert report["suite_diff"]["complete"]
        assert report["summary"] == (
            {"fix_confirmed": 1} if result == "pass" else {"fails_both": 1}
        )


@pytest.mark.parametrize("fd", [1, 2])
def test_timeout_with_binary_output_stays_incomplete(make_repo, monkeypatch, fd):
    test = f'import sample\ndef test_value():\n import os,time\n os.write({fd},b"\\xff")\n time.sleep(10)\n'
    repo, base, head = make_repo(
        {"sample.py": "VALUE=0\n"}, {"sample.py": "VALUE=1\n", "test_sample.py": test}
    )
    monkeypatch.setenv("PYTEST_ADDOPTS", "-s")
    output = repo / "result.json"
    proc = run_cli(repo, base, head, "--timeout", "1", "--json-out", str(output))
    assert proc.returncode == 1, proc.stderr
    assert "UnicodeDecodeError" not in proc.stderr
    report = json.loads(output.read_text())
    assert not report["suite_diff"]["complete"]
    assert all("timed out" in error for error in report["collection_errors"].values())
