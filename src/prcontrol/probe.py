"""Internal pytest plugin. This is reporting, not a security boundary."""
import json
import os
import sys
from pathlib import Path

_records = {}
_collection_errors = []


def pytest_runtest_logreport(report):
    row = _records.setdefault(report.nodeid, {})
    row[report.when] = report.outcome


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
