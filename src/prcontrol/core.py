"""Core verification engine for pr-control.

A fix PR makes an implicit claim: "these tests fail without my change and pass
with it." CI runs the PR's tests against the PR's code only. This module runs
the missing combinations in two detached git worktrees and reports the verdict
matrix, plus a same-suite diff so pre-existing failures are distinguished from
regressions introduced by the PR.
"""

from __future__ import annotations

import json
import hashlib
import stat
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import signal
import threading
import time
from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path

from .probe_report import valid_probe_report

TEST_PATH_RE = re.compile(r"(^|/)(tests?|spec)/.*\.(py|js|ts)$|(^|/)test_.*\.py$|.*(_test|\.test|\.spec)\.(py|js|ts)$", re.DOTALL)


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
    collection_skips: list[str] = field(default_factory=list)
    runtime_skips: list[str] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    deselected: list[str] = field(default_factory=list)
    test_source: dict | None = None


class PrControlError(RuntimeError):
    """User-facing failure: dirty input, missing refs, unrunnable tests."""


def _git(repo: Path, *args: str, strip: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True
    )
    # Git's -z output contains literal filesystem names. Text-mode pipes
    # normalize CR/CRLF to LF and strict UTF-8 rejects valid POSIX byte names.
    # Decode with the filesystem codec without newline translation instead.
    stdout, stderr = os.fsdecode(proc.stdout), os.fsdecode(proc.stderr)
    if proc.returncode != 0:
        raise PrControlError(f"git {' '.join(args)} failed: {stderr.strip()[:300]}")
    return stdout.strip() if strip else stdout


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
    """Head test paths added, modified or renamed in the three-dot diff.

    NUL delimiters preserve literal Unicode, whitespace and newline filenames
    without Git's display quoting. Rename output names the head destination.
    """
    out = _git(repo, "diff", "--name-only", "-z", "--diff-filter=AMR", f"{base_sha}...{head_sha}", strip=False)
    files = [f for f in out.split("\0") if f]
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


def _tracked_snapshot(worktree: Path):
    """Content/mode snapshot of tracked paths, including staged additions.

    An opt-in trusted-code check, not tamper-proof: changes restored before
    the post-run snapshot are invisible. Untracked scratch files are excluded.
    """
    names = _git(worktree, "ls-files", "-z", strip=False).split("\0")
    snapshot = {}
    for name in names:
        if not name:
            continue
        path = worktree / name
        try:
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                content = os.fsencode(os.readlink(path))
            elif stat.S_ISREG(mode):
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                content = None
            else:
                # Submodules/directories are not inspected recursively.
                content = b""
            snapshot[name] = (stat.S_IFMT(mode), bool(mode & stat.S_IXUSR),
                              hasher.hexdigest() if content is None else hashlib.sha256(content).hexdigest())
        except FileNotFoundError:
            snapshot[name] = None
    return snapshot


def run_pytest(code_worktree: Path, test_worktree: Path, ref_name: str,
               targets: list[str], timeout: int, import_mode: str = "importlib",
               test_support: list[str] | None = None, per_test_source: bool = False,
               require_clean_worktrees: bool = False) -> RunResult:
    """Run head tests against selected source and inspect imported file origins.

    Tests and plugins can override imports or falsify reports. This is a
    diagnostic for trusted code, never a sandbox or adversarial attestation.
    """
    support_dirs = []
    for path in test_support or []:
        resolved = (test_worktree / path).resolve()
        if not resolved.is_relative_to(test_worktree.resolve()):
            raise PrControlError("test support must stay inside the head repository")
        if not resolved.is_dir():
            raise PrControlError(f"test support directory does not exist on head: {path}")
        support_dirs.append(str(resolved))
    packages = _package_names(code_worktree) | _package_names(test_worktree)
    with tempfile.TemporaryDirectory(prefix="pr-control-probe-") as tmp:
        probe_dir = Path(tmp)
        shutil.copyfile(Path(__file__).with_name("probe.py"), probe_dir / "pr_control_probe.py")
        report = probe_dir / "report.json"
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(
            [str(probe_dir), _pythonpath_for(code_worktree), *support_dirs]))
        if per_test_source:
            env["PR_CONTROL_TEST_SOURCE"] = "1"
        else:
            env.pop("PR_CONTROL_TEST_SOURCE", None)
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
        env["PR_CONTROL_SELECTED_TEST_FILES"] = json.dumps([
            str((test_worktree / target.split("::")[0]).resolve()) for target in targets
        ])
        cmd = [sys.executable, "-m", "pytest", *abs_targets, "-q", "-p", "pr_control_probe",
               "-p", "no:cacheprovider", "--rootdir", str(test_worktree), "--import-mode", import_mode]
        checked_trees = list(dict.fromkeys((code_worktree, test_worktree)))
        try:
            before = ([_tracked_snapshot(tree) for tree in checked_trees]
                      if require_clean_worktrees else None)
        except (OSError, PrControlError):
            # Neither exception text nor filenames belong in this diagnostic.
            return RunResult(ref=ref_name, returncode=-1,
                             collection_error="tracked worktree snapshot unavailable before pytest; outcomes and source evidence unavailable")
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=code_worktree, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=os.name == "posix",
            )
            stdout, stderr = _capture_output(proc, timeout)
        except subprocess.TimeoutExpired:
            # Cleanup, not containment: hostile code can detach the group.
            if os.name == "posix":
                try:
                    if proc.returncode is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            return RunResult(ref=ref_name, returncode=-1,
                             collection_error=f"pytest timed out after {timeout}s")
        except BaseException:
            # A caller interrupt or pipe-read failure must not abandon the
            # separately started pytest session while worktrees are removed.
            # Re-raise the original exception after ordinary child cleanup.
            if proc is not None:
                if os.name == "posix":
                    try:
                        if proc.returncode is None:
                            os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    proc.kill()
                proc.wait()
            raise
        if before is not None:
            try:
                after = [_tracked_snapshot(tree) for tree in checked_trees]
            except (OSError, PrControlError):
                return RunResult(ref=ref_name, returncode=proc.returncode,
                                 collection_error="tracked worktree snapshot unavailable after pytest; outcomes and source evidence unavailable")
            if after != before:
                return RunResult(ref=ref_name, returncode=proc.returncode,
                                 collection_error="tracked worktree changed during pytest; outcomes and source evidence unavailable")
        try:
            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        # Keys/values can contain user data. Keep diagnostics
                        # generic and do not choose one conflicting member.
                        raise ValueError("duplicate probe JSON object member")
                    value[key] = item
                return value
            data = json.loads(report.read_text(), object_pairs_hook=unique_object)
        except (OSError, ValueError, RecursionError):
            return RunResult(ref=ref_name, returncode=proc.returncode,
                             collection_error="pytest produced no valid report: " + (stderr or stdout)[-500:])
        if not valid_probe_report(data, proc.returncode):
            return RunResult(ref=ref_name, returncode=proc.returncode,
                             collection_error="invalid probe report shape; outcomes and source evidence unavailable")
        test_source = None
        if per_test_source:
            raw_sources = data.get("test_source")
            if not isinstance(raw_sources, dict):
                return RunResult(ref=ref_name, returncode=proc.returncode,
                                 collection_error="missing per-test source evidence")
            test_source = raw_sources
            if any(not Path(path).is_relative_to(code_worktree.resolve())
                   for paths in test_source.values() for path in paths):
                return RunResult(ref=ref_name, returncode=proc.returncode,
                                 collection_error="per-test source provenance mismatch")
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
                         failure_details=data.get("failure_details", {}), collected_counts=dict(collected),
                         collection_skips=data.get("collection_skips", []),
                         runtime_skips=data.get("runtime_skips", []), deselected=data.get("deselected", []),
                         test_source=test_source)


def run_repeated_pytest(code_worktree: Path, test_worktree: Path, ref_name: str,
                        targets: list[str], timeout: int, import_mode: str = "importlib",
                        test_support: list[str] | None = None, repeats: int = 1,
                        per_test_source: bool = False,
                        require_clean_worktrees: bool = False) -> RunResult:
    """Repeat selected tests in fresh subprocesses, retaining all attempt evidence.

    Repeats share the same worktree. This detects observed instability, not all
    flakes, and does not reset files written by tests between attempts.
    """
    if not 1 <= repeats <= 20:
        raise PrControlError("--repeats must be between 1 and 20")
    runs = [run_pytest(code_worktree, test_worktree, ref_name, targets, timeout,
                       import_mode, test_support, per_test_source, require_clean_worktrees) for _ in range(repeats)]
    first = runs[0]
    evidence = []
    errors = []
    signature = lambda run: (sorted((o.nodeid, o.outcome) for o in run.outcomes),
                             run.collected_counts, run.source_verified,
                             sorted(run.collection_skips), sorted(run.runtime_skips), sorted(run.deselected), run.test_source)
    for attempt, run in enumerate(runs, 1):
        evidence.append({"attempt": attempt, "returncode": run.returncode,
                         "collection_error": run.collection_error,
                         "outcomes": [{"nodeid": o.nodeid, "outcome": o.outcome} for o in run.outcomes],
                         "collected_counts": run.collected_counts, "imports": run.imports,
                         "source_verified": run.source_verified, "test_source": run.test_source,
                         "failure_details": run.failure_details,
                         "collection_skips": run.collection_skips, "runtime_skips": run.runtime_skips,
                         "deselected": run.deselected})
        if run.collection_error:
            errors.append(f"attempt {attempt}: {run.collection_error}")
        if signature(run) != signature(first):
            errors.append(f"attempt {attempt}: unstable repeated outcomes, collection counts, skips, deselection or source verification")
    # Keep the first observed outcomes, but reject instability instead of voting.
    return RunResult(ref=ref_name, returncode=first.returncode, outcomes=first.outcomes,
                     collection_error="; ".join(errors) or None,
                     imports=first.imports, source_verified=all(r.source_verified for r in runs),
                     failure_details=first.failure_details, collected_counts=first.collected_counts,
                     collection_skips=sorted({n for r in runs for n in r.collection_skips}),
                     runtime_skips=sorted({n for r in runs for n in r.runtime_skips}),
                     attempts=evidence,
                     deselected=sorted({n for r in runs for n in r.deselected}),
                     test_source=first.test_source)


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
            verified = base_run.source_verified and head_run.source_verified
            for run in (base_run, head_run):
                if run.test_source is not None:
                    verified = verified and bool(run.test_source.get(nodeid))
            verdict = "fix_confirmed" if verified else "transition_unverified_source"
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


def load_failure_witnesses(path: Path) -> dict[str, str]:
    """Read exact node IDs and literal exception-message fragments, not regexes."""
    def unique_object(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise PrControlError(f"duplicate failure witness node ID: {key}")
            out[key] = value
        return out

    try:
        data = json.loads(path.read_text(), object_pairs_hook=unique_object)
    except (ValueError, OSError) as exc:
        raise PrControlError(f"cannot read base failure witnesses: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise PrControlError("base failure witnesses must be a nonempty JSON object")
    if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
           for k, v in data.items()):
        raise PrControlError("witness node IDs and message fragments must be nonempty strings")
    return data


def check_failure_witnesses(expected: dict[str, str], rows: list[dict], base_run: RunResult) -> dict:
    """Gate on matching failure messages for every occurrence and attempt.

    This is an explicit diagnostic policy, not evidence of causality. Matching
    source text or traceback context is deliberately insufficient.
    """
    by_node = {row["test"]: row for row in rows}
    attempts = base_run.attempts or [{"attempt": 1, "failure_details": base_run.failure_details,
                                    "collected_counts": base_run.collected_counts}]
    results = []
    for node, fragment in expected.items():
        reasons = []
        if by_node.get(node, {}).get("verdict") != "fix_confirmed":
            reasons.append("expected node must have a source-verified failed-to-passed transition")
        for attempt in attempts:
            details = attempt.get("failure_details", {}).get(node, [])
            count = attempt.get("collected_counts", {}).get(node, 0)
            if not count or len(details) != count:
                reasons.append(f"attempt {attempt['attempt']}: missing failure evidence for every collected occurrence")
            elif any(d.get("phase") != "call" or d.get("classification") != "failed"
                     or not isinstance(d.get("exception_message"), str)
                     or fragment not in d["exception_message"] for d in details):
                reasons.append(f"attempt {attempt['attempt']}: assertion exception message does not match")
        results.append({"test": node, "literal_message": fragment, "matched": not reasons, "reasons": reasons})
    return {"passed": all(row["matched"] for row in results), "expectations": results}



def _capture_output(proc, timeout: int, limit: int = 16384) -> tuple[str, str]:
    """Drain both pipes with bounded tails; EOF is part of the timeout.

    Children inheriting an output pipe must close it before completion, as
    with communicate(). Daemon readers avoid blocking shutdown on a detached
    process that escapes group cleanup. This is not hostile-code containment.
    """
    tails = [bytearray(), bytearray()]
    errors = []

    def drain(stream, tail):
        try:
            while True:
                chunk = stream.read1(65536)
                if not chunk:
                    break
                tail.extend(chunk)
                del tail[:-limit]
        except OSError as error:
            errors.append(error)
        finally:
            stream.close()

    readers = [threading.Thread(target=drain, args=(stream, tail), daemon=True)
               for stream, tail in zip((proc.stdout, proc.stderr), tails)]
    deadline = time.monotonic() + timeout
    for reader in readers:
        reader.start()
    # Drain/join before wait(): until the final reap, an exited child keeps
    # its PID and process-group identity owned. This needs no waitid API and
    # also covers older supported macOS Python runtimes. EOF is bounded by
    # the same deadline, including children inheriting either output pipe.
    for reader in readers:
        reader.join(timeout=max(0, deadline - time.monotonic()))
    if any(reader.is_alive() for reader in readers):
        raise subprocess.TimeoutExpired(proc.args, timeout)
    if errors:
        raise errors[0]
    output = tuple(bytes(tail).decode("utf-8", errors="replace") for tail in tails)
    proc.wait(timeout=max(0, deadline - time.monotonic()))
    return output
