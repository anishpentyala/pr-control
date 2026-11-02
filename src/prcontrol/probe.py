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
_collected = []
_finished = []
_worker_collections = []
_worker_imports = {}
_worker_errors = []
_collection_skips = []
_worker_skips = []
_runtime_skips = []


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
        report.pr_control_exception_message = str(call.excinfo.value)[:8000]
        lifecycle_failure = any(
            frame.f_globals.get("__name__") in {"unittest.case", "unittest.async_case"}
            and frame.f_code.co_name in {"_callSetUp", "_callTearDown", "_callCleanup"}
            for frame, _ in traceback.walk_tb(call.excinfo.tb)
        )
        report.pr_control_execution_error = lifecycle_failure or not call.excinfo.errisinstance(
            (AssertionError, pytest.fail.Exception)
        )



def pytest_runtest_logreport(report):
    if report.skipped:
        _runtime_skips.append(report.nodeid)
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
            "exception_message": getattr(report, "pr_control_exception_message", None),
            "traceback": str(report.longrepr)[-8000:],
            "subtest": str(getattr(report, "context", "")) or None,
        })
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    previous = row.get(report.when)
    if previous is None or rank[status] > rank[previous]:
        row[report.when] = status


def pytest_collection_finish(session):
    # session.items contains the selected tests after deselection. Record each
    # occurrence: duplicate targets can legitimately run one node twice.
    _collected.extend(item.nodeid for item in session.items)


def pytest_runtest_logfinish(nodeid, location):
    _finished.append(nodeid)


def pytest_collectreport(report):
    if report.skipped:
        _collection_skips.append(report.nodeid)
    if report.failed:
        _collection_errors.append({"nodeid": report.nodeid, "error": str(report.longrepr)[-2000:]})


def _observed_imports():
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
    return imports


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node, ids):
    # Every worker collects the same selected suite. Do not multiply counts
    # by the worker count: each test occurrence is scheduled exactly once.
    _worker_collections.append(list(ids))
    if len(_worker_collections) == 1:
        _collected.extend(ids)
    elif list(ids) != _worker_collections[0]:
        _worker_errors.append("xdist workers collected different suites")


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node, error):
    if error:
        _worker_errors.append("xdist worker failed: " + str(error))
    evidence = node.workeroutput.get("pr_control_evidence")
    if not isinstance(evidence, dict):
        _worker_errors.append("xdist worker returned no source evidence")
        return
    _worker_skips.extend(evidence.get("collection_skips", []))
    for name, paths in evidence.get("imports", {}).items():
        _worker_imports.setdefault(name, []).extend(paths)


def pytest_sessionfinish(session, exitstatus):
    imports = _observed_imports()
    if hasattr(session.config, "workerinput"):
        # Workers communicate through xdist, never race to replace the shared
        # report file. The controller owns outcomes and final completeness.
        session.config.workeroutput["pr_control_evidence"] = {"imports": imports, "collection_skips": _collection_skips}
        return
    for name, paths in _worker_imports.items():
        imports[name] = sorted(set(imports.get(name, []) + paths))
    errors = _collection_errors + [{"nodeid": "xdist", "error": value} for value in _worker_errors]
    Path(os.environ['PR_CONTROL_REPORT']).write_text(json.dumps({
        'tests': _records, 'failure_details': _failure_details, 'collection_errors': errors,
        'imports': imports, 'exitstatus': int(exitstatus),
        'collected': _collected, 'finished': _finished,
        'collection_skips': sorted(set(_collection_skips + _worker_skips)),
        'runtime_skips': sorted(set(_runtime_skips)),
    }))
