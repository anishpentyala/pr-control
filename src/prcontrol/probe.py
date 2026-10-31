"""Internal pytest plugin. This is reporting, not a security boundary."""
import json
import os
import sys
import pytest
from pathlib import Path

_records = {}
_collection_errors = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # unittest setUp/tearDown run inside pytest's call phase. An unexpected
    # exception there is not a regression assertion. Conservatively classify
    # non-AssertionError failures as execution errors, including native tests.
    if report.when == "call" and report.failed and call.excinfo is not None:
        report.pr_control_execution_error = not call.excinfo.errisinstance(AssertionError)



def pytest_runtest_logreport(report):
    row = _records.setdefault(report.nodeid, {})
    status = "error" if getattr(report, "pr_control_execution_error", False) else report.outcome
    if report.failed and hasattr(report, "context"):
        # Subtest reports share the parent nodeid. Conservatively preserve
        # their failure as an error instead of allowing a later parent-pass
        # report to overwrite it. Detailed subtest attribution is unsupported.
        status = "error"
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    previous = row.get(report.when)
    if previous is None or rank[status] > rank[previous]:
        row[report.when] = status


def pytest_collectreport(report):
    if report.failed:
        _collection_errors.append({"nodeid": report.nodeid, "error": str(report.longrepr)[-2000:]})


def pytest_sessionfinish(session, exitstatus):
    candidates = set(json.loads(os.environ['PR_CONTROL_PACKAGES']))
    imports = {}
    for name, mod in list(sys.modules.items()):
        if name.split('.')[0] not in candidates:
            continue
        file = getattr(mod, '__file__', None)
        paths = [str(Path(file).resolve())] if file else []
        if not file:
            paths = [str(Path(p).resolve()) for p in getattr(mod, '__path__', [])]
        if paths:
            imports[name] = paths
    Path(os.environ['PR_CONTROL_REPORT']).write_text(json.dumps({
        'tests': _records, 'collection_errors': _collection_errors,
        'imports': imports, 'exitstatus': int(exitstatus),
    }))
