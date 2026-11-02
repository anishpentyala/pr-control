"""Core verification engine for pr-control.

A fix PR makes an implicit claim: "these tests fail without my change and pass
with it." CI runs the PR's tests against the PR's code only. This module runs
the missing combinations in two detached git worktrees and reports the verdict
matrix, plus a same-suite diff so pre-existing failures are distinguished from
regressions introduced by the PR.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import signal
from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path

TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec)/.*\.(py|js|ts)$|(^|/)test_.*\.py$|.*(_test|\.test|\.spec)\.(py|js|ts)$")


@dataclass(frozen=True)
class RefState:
    """One side (base or head) of the comparison."""

    name: str
    sha: str
    worktree: Path


@dataclass
class TestOutcome:
    nodeid: str
    outcome: str  # passed | failed | error | skipped


@dataclass
class RunResult:
    ref: str
    returncode: int
    outcomes: list[TestOutcome] = field(default_factory=list)
    collection_error: str | None = None
    imports: dict = field(default_factory=dict)
    source_verified: bool = False
    failure_details: dict = field(default_factory=dict)
    collected_counts: dict[str, int] = field(default_factory=dict)


class PrControlError(RuntimeError):
    """User-facing failure: dirty input, missing refs, unrunnable tests."""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise PrControlError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


def check_repo(repo: Path, base: str, head: str) -> tuple[str, str]:
    """Fail closed on anything that would make the comparison meaningless."""
    if not (repo / ".git").exists():
        raise PrControlError(f"{repo} is not a git repository")
    base_sha = _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    head_sha = _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}")
    if base_sha == head_sha:
        raise PrControlError("base and head resolve to the same commit; nothing to compare")
    return base_sha, head_sha


def changed_test_files(repo: Path, base_sha: str, head_sha: str) -> list[str]:
    """Test files added or modified between base and head (three-dot diff)."""
    out = _git(repo, "diff", "--name-only", "--diff-filter=AM", f"{base_sha}...{head_sha}")
    files = [f for f in out.splitlines() if f.strip()]
    return [f for f in files if f.endswith(".py") and TEST_PATH_RE.search(f) and Path(f).name != "conftest.py"]


def make_worktree(repo: Path, sha: str, dest: Path) -> Path:
    _git(repo, "worktree", "add", "--force", "--detach", str(dest), sha)
    return dest


def _pythonpath_for(worktree: Path) -> str:
    """Mirror how a contributor runs tests from a checkout: root + src on path."""
    parts = [str(worktree)]
    if (worktree / "src").is_dir():
        parts.insert(0, str(worktree / "src"))
    return os.pathsep.join(parts)


def _package_names(worktree: Path) -> set[str]:
    roots = [worktree / "src", worktree]
    names = set()
    for root in roots:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            if (entry.name in {"tests", "test", "conftest.py", "setup.py"}
                    or entry.name.startswith((".", "test_")) or entry.name.endswith("_test.py")):
                continue
            if entry.is_file() and entry.suffix == ".py":
                names.add(entry.stem)
            elif entry.is_dir() and ((entry / "__init__.py").exists() or root.name == "src"):
                names.add(entry.name)
    return names


def run_pytest(code_worktree: Path, test_worktree: Path, ref_name: str,
               targets: list[str], timeout: int, import_mode: str = "importlib") -> RunResult:
    """Run head tests against selected source and inspect imported file origins.

    Tests and plugins can override imports or falsify reports. This is a
    diagnostic for trusted code, never a sandbox or adversarial attestation.
    """
    packages = _package_names(code_worktree) | _package_names(test_worktree)
    with tempfile.TemporaryDirectory(prefix="pr-control-probe-") as tmp:
        probe_dir = Path(tmp)
        shutil.copyfile(Path(__file__).with_name("probe.py"), probe_dir / "pr_control_probe.py")
        report = probe_dir / "report.json"
        env = dict(os.environ, PYTHONPATH=str(probe_dir) + os.pathsep + _pythonpath_for(code_worktree))
        env.update(PR_CONTROL_REPORT=str(report), PR_CONTROL_PACKAGES=json.dumps(sorted(packages)))
        abs_targets = []
        for target in targets:
            file, *selectors = target.split("::")
            resolved = (test_worktree / file).resolve()
            if not resolved.is_relative_to(test_worktree.resolve()):
                raise PrControlError("targets must stay inside the repository")
            if not resolved.exists():
                raise PrControlError(f"target does not exist on head: {file}")
            abs_targets.append(str(resolved) + ("::" + "::".join(selectors) if selectors else ""))
        cmd = [sys.executable, "-m", "pytest", *abs_targets, "-q", "-p", "pr_control_probe",
               "-p", "no:cacheprovider", "--rootdir", str(test_worktree), "--import-mode", import_mode]
        try:
            proc = subprocess.Popen(cmd, cwd=code_worktree, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=os.name == "posix")
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Stop ordinary child processes as well as pytest. This is cleanup,
            # not containment: hostile code can still detach from the group.
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.communicate()
            return RunResult(ref=ref_name, returncode=-1, collection_error=f"pytest timed out after {timeout}s")
        try:
            data = json.loads(report.read_text())
        except (OSError, ValueError):
            return RunResult(ref=ref_name, returncode=proc.returncode,
                             collection_error="pytest produced no valid report: " + (stderr or stdout)[-500:])
        outcomes = []
        for nodeid, phases in data['tests'].items():
            # Setup/teardown errors must never count as a failing assertion.
            outcome = ('error' if phases.get('setup') == 'failed' or phases.get('teardown') == 'failed'
                       else 'error' if 'error' in phases.values()
                       else 'skipped' if 'skipped' in phases.values()
                       else phases.get('call', 'skipped'))
            outcomes.append(TestOutcome(nodeid=nodeid, outcome=outcome))
        errors = data['collection_errors']
        collection_error = json.dumps(errors) if errors else None
        collected = Counter(data['collected'])
        finished = Counter(data['finished'])
        if collected != finished:
            missing = dict(collected - finished)
            extra = dict(finished - collected)
            collection_error = (collection_error or '') + (
                'not all collected tests ran: ' + json.dumps({'missing': missing, 'unexpected': extra})
            )
        has_failure = any(o.outcome in {"failed", "error"} for o in outcomes)
        if proc.returncode == 1 and not has_failure and not collection_error:
            collection_error = "pytest exited 1 without a recorded test failure; session result is incomplete"
        elif proc.returncode == 0 and (has_failure or collection_error):
            collection_error = "pytest exited 0 despite recorded failures: " + (collection_error or "test failures")
        if proc.returncode not in (0, 1):
            collection_error = collection_error or f"pytest exited {proc.returncode}: " + (stderr or stdout)[-500:]
        if not outcomes:
            collection_error = collection_error or "no test outcomes recorded"
        imports = data['imports']
        bad_imports = {name: paths for name, paths in imports.items()
                       if any(not Path(path).is_relative_to(code_worktree.resolve()) for path in paths)}
        if bad_imports:
            collection_error = "source provenance mismatch: " + json.dumps(bad_imports)
        return RunResult(ref=ref_name, returncode=proc.returncode, outcomes=outcomes,
                         collection_error=collection_error, imports=imports,
                         source_verified=bool(imports) and not bad_imports,
                         failure_details=data.get("failure_details", {}), collected_counts=dict(collected))


def verdicts(base_run: RunResult, head_run: RunResult) -> list[dict]:
    """Per-test verdict across the two refs. The negative control is explicit:
    a fix test should fail on base and pass on head."""
    base_map = {o.nodeid: o.outcome for o in base_run.outcomes}
    head_map = {o.nodeid: o.outcome for o in head_run.outcomes}
    base_counts = base_run.collected_counts or dict(Counter(o.nodeid for o in base_run.outcomes))
    head_counts = head_run.collected_counts or dict(Counter(o.nodeid for o in head_run.outcomes))
    occurrence_mismatch = base_counts != head_counts
    rows = []
    for nodeid in sorted(set(base_map) | set(head_map)):
        b, h = base_map.get(nodeid), head_map.get(nodeid)
        if base_run.collection_error or head_run.collection_error or occurrence_mismatch:
            verdict = "inconclusive"
        elif b == "error" or h == "error":
            verdict = "unrunnable"
        elif b == "failed" and h == "passed":
            verdict = "fix_confirmed" if base_run.source_verified and head_run.source_verified else "transition_unverified_source"
        elif b == "passed" and h == "passed":
            verdict = "passes_both"          # toothless as a fix control; fine as coverage
        elif b == "passed" and h == "failed":
            verdict = "regression_introduced"
        elif b == "failed" and h == "failed":
            verdict = "fails_both"           # fix incomplete, or test broken
        elif b is None:
            verdict = "unrunnable_on_base"   # e.g. imports the new API
        elif h is None:
            verdict = "missing_on_head"
        else:
            verdict = f"{b or 'absent'}_on_base__{h or 'absent'}_on_head"
        rows.append({"test": nodeid, "base": b, "head": h, "verdict": verdict,
                     "base_failure_details": base_run.failure_details.get(nodeid, []),
                     "head_failure_details": head_run.failure_details.get(nodeid, [])})
    return rows


def suite_diff(base_run: RunResult, head_run: RunResult) -> dict:
    """Same-suite comparison over whatever targets were run on both refs."""
    base_fail = sorted(o.nodeid for o in base_run.outcomes if o.outcome == "failed")
    head_fail = sorted(o.nodeid for o in head_run.outcomes if o.outcome == "failed")
    base_nodes = {o.nodeid for o in base_run.outcomes}
    head_nodes = {o.nodeid for o in head_run.outcomes}
    execution_errors = {
        run.ref: sorted(o.nodeid for o in run.outcomes if o.outcome == "error")
        for run in (base_run, head_run)
    }
    base_counts = base_run.collected_counts or dict(Counter(o.nodeid for o in base_run.outcomes))
    head_counts = head_run.collected_counts or dict(Counter(o.nodeid for o in head_run.outcomes))
    occurrence_mismatches = {
        node: {"base": base_counts.get(node, 0), "head": head_counts.get(node, 0)}
        for node in sorted(set(base_counts) | set(head_counts))
        if base_counts.get(node, 0) != head_counts.get(node, 0)
    }
    return {
        "occurrence_mismatches": occurrence_mismatches,
        "complete": (not occurrence_mismatches and not base_run.collection_error and not head_run.collection_error
                     and base_nodes == head_nodes and not any(execution_errors.values())),
        "only_on_base": sorted(base_nodes - head_nodes),
        "only_on_head": sorted(head_nodes - base_nodes),
        "execution_errors": execution_errors,
        "base_failed": base_fail,
        "head_failed": head_fail,
        "new_failures_on_head": sorted(set(head_fail) - set(base_fail)),
        "fixed_on_head": sorted(set(base_fail) - set(head_fail)),
    }


def env_fingerprint() -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pytest": _pytest_version(),
    }


def _pytest_version() -> str:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"], capture_output=True, text=True
    )
    return proc.stdout.strip().splitlines()[0] if proc.returncode == 0 else "unknown"


def cleanup(repo: Path, worktrees: list[Path]) -> None:
    for wt in worktrees:
        try:
            _git(repo, "worktree", "remove", "--force", str(wt))
        except PrControlError:
            shutil.rmtree(wt, ignore_errors=True)
