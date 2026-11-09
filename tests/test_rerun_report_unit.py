"""The report hook accepts retry evidence without requiring an optional plugin."""
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from prcontrol import probe


@pytest.mark.parametrize('phase', ['setup', 'call', 'teardown'])
@pytest.mark.parametrize('final', ['passed', 'failed'])
def test_rerun_is_preserved_after_final_report(phase, final, monkeypatch):
    monkeypatch.delenv('PR_CONTROL_TEST_SOURCE', raising=False)
    namespace = runpy.run_path(str(Path(probe.__file__)))
    hook = namespace['pytest_runtest_logreport']
    node = 'tests/test_example.py::test_example'
    hook(SimpleNamespace(nodeid=node, when=phase, outcome='rerun', skipped=False, failed=False,
                         longrepr='synthetic retry traceback', pr_control_exception_message='retry witness'))
    hook(SimpleNamespace(nodeid=node, when=phase, outcome=final, skipped=False,
                         failed=final == 'failed', longrepr='final traceback'))
    assert namespace['_records'][node][phase] == 'error'
    details = namespace['_failure_details'][node]
    assert details[0]['phase'] == phase
    assert details[0]['classification'] == 'error'
    assert details[0]['traceback'] == 'synthetic retry traceback'
    assert details[0]['exception_message'] == 'retry witness'
