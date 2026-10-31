"""Internal pytest plugin. This is reporting, not a security boundary."""
import json
import os
import sys
import traceback
import pytest
from pathlib import Path

_records = {}
_collection_errors = []
_failure_details = {}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    # unittest setUp/tearDown run inside pytest's call phase. An unexpected
    # exception there is not a regression assertion. Conservatively classify
    # unexpected exceptions as execution errors. pytest.fail.Exception also
    # covers explicit pytest.fail and a missing pytest.raises exception.
    if report.when == "call" and report.failed and call.excinfo is None:
        # A strict XPASS is a failed report even though the test body passed.
        # Other report-only failures also lack assertion evidence. Never count
        # these as a failing regression assertion in the negative control.
        report.pr_control_execution_error = True
    elif report.when == "call" and report.failed:
        lifecycle_failure = any(
            frame.f_globals.get("__name__") in {"unittest.case", "unittest.async_case"}
            and frame.f_code.co_name in {"_callSetUp", "_callTearDown", "_callCleanup"}
            for frame, _ in traceback.walk_tb(call.excinfo.tb)
        )
        report.pr_control_execution_error = lifecycle_failure or not call.excinfo.errisinstance(
            (AssertionError, pytest.fail.Exception)
        )



def pytest_runtest_logreport(report):
    row = _records.setdefault(report.nodeid, {})
    status = "error" if getattr(report, "pr_control_execution_error", False) else report.outcome
    if report.failed and hasattr(report, "context"):
        # Subtest reports share the parent nodeid. Conservatively preserve
        # their failure as an error instead of allowing a later parent-pass
        # report to overwrite it. Detailed subtest attribution is unsupported.
        status = "error"
    if report.failed:
        _failure_details.setdefault(report.nodeid, []).append({
            "phase": report.when,
            "classification": status,
            "traceback": str(report.longrepr)[-8000:],
            "subtest": str(getattr(report, "context", "")) or None,
        })
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
        'tests': _records, 'failure_details': _failure_details, 'collection_errors': _collection_errors,
        'imports': imports, 'exitstatus': int(exitstatus),
    }))
