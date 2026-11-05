"""Check the shape of consumed probe evidence, not its trustworthiness."""
from pathlib import Path


_PHASES = {'setup', 'call', 'teardown'}
_STATUSES = {'passed', 'failed', 'error', 'skipped'}


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def valid_probe_report(data, returncode):
    """Malformed/incompatible reports cannot establish outcomes or imports.

    Unknown extra keys remain compatible. A well-shaped report can still be
    fabricated by tests/plugins; this is no attestation or sandbox boundary.
    """
    if not isinstance(data, dict):
        return False
    if (type(data.get('exitstatus')) is not int
            or data['exitstatus'] not in range(6)):
        return False
    tests = data.get('tests')
    if not isinstance(tests, dict):
        return False
    for node, phases in tests.items():
        if not isinstance(node, str) or not isinstance(phases, dict) or not phases:
            return False
        if any(phase not in _PHASES or not isinstance(status, str)
               or status not in _STATUSES for phase, status in phases.items()):
            return False
    if data['exitstatus'] != returncode:
        # Preserve the existing, more specific zero-exit-with-failures
        # diagnosis when a later session hook rewrites pytest's exit status.
        if returncode != 0 or not any(
            status in {'failed', 'error'}
            for phases in tests.values() for status in phases.values()
        ):
            return False
    for key in ('collected', 'finished'):
        if not _strings(data.get(key)):
            return False
    for key in ('collection_skips', 'runtime_skips', 'deselected'):
        if not _strings(data.get(key, [])):
            return False
    errors = data.get('collection_errors')
    if not isinstance(errors, list) or any(
        not isinstance(error, dict) or not isinstance(error.get('nodeid'), str)
        or not isinstance(error.get('error'), str) for error in errors
    ):
        return False
    imports = data.get('imports')
    if not isinstance(imports, dict):
        return False
    for name, paths in imports.items():
        if (not isinstance(name, str) or not _strings(paths) or not paths
                or any(not Path(path).is_absolute() for path in paths)):
            return False
    details = data.get('failure_details', {})
    if not isinstance(details, dict):
        return False
    for node, failures in details.items():
        if not isinstance(node, str) or not isinstance(failures, list):
            return False
        for failure in failures:
            if not isinstance(failure, dict):
                return False
            if (not isinstance(failure.get('phase'), str)
                    or failure['phase'] not in _PHASES
                    or not isinstance(failure.get('classification'), str)
                    or failure['classification'] not in _STATUSES
                    or not isinstance(failure.get('traceback'), str)):
                return False
            for key in ('exception_message', 'subtest'):
                value = failure.get(key)
                if value is not None and not isinstance(value, str):
                    return False
    return True
