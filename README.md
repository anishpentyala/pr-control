# pr-control

A negative control for pull requests.

Code review answers "does this change look right?" pr-control answers a
stricter question: **do the PR's own tests fail on base and pass on head?** It
runs the head ref's tests twice - once against the base ref's code, once
against the head ref's code - and reports the observed outcome transition.

## Why

A fix PR usually adds a test that passes on the PR branch. That alone proves
nothing: a test that also passes on the base ref is toothless as evidence of a
fix. The evidence is the *difference*: fails on base, passes on head. Doing
that by hand for every PR is tedious, so reviews skip it and useful evidence is missed.

Historical fail-before/pass-after testing is an existing technique, not an
invention of this project. Related tools include:

- [mustfail](https://github.com/greenlitbooks/mustfail), which keeps tests and
  reverts changed source in a worktree, runs positive and negative controls,
  and heuristically distinguishes load failures from other detections. It
  supports arbitrary check commands and file-level localization.
- [pytest-fixed-by](https://ruachtov.ai/blog/fixed-by.html), whose published
  protocol links individual tests to fix commits and replays today's tests
  against pre-fix and post-fix source.
- [verify-regression-tests](https://github.com/aiopshwang/verify-regression-tests),
  an agent skill for checking that a frozen test fails at the intended defect
  witness, rather than an unrelated gate or exception.
- [greenproof](https://github.com/zxyasfas/greenproof), which checks original
  test snapshots against changed code to investigate test-dependent greens.
- [pytest-drift](https://pypi.org/project/pytest-drift/), which compares values
  returned by tests across branches rather than ordinary assertion outcomes.

pr-control's scope is a Python/pytest command that runs head tests against both
source refs, records per-node transitions and bounded failure evidence, checks
observed local import origins, and flags incomplete or incomparable suites.
These are implementation choices, not a claim that this combination is unique.
A failure-to-pass transition still needs inspection of the intended behavior.
The comparisons above were checked against the projects' own descriptions on
2026-10-31; they are not a benchmark or an independent audit of those tools.

## Install

```bash
pip install .
```

## Usage

```bash
cd /path/to/clone
pr-control . --base <base-sha> --head <head-sha>
```

With no `--targets`, pr-control runs Python test paths added, modified or renamed by the PR
(the head destinations from `git diff --name-only -z --diff-filter=AMR base...head`).
NUL-delimited discovery preserves literal Unicode, whitespace and newline names;
deleted tests and `conftest.py` are not automatic targets. A rename is test
selection, not proof that the test binds to the fix. To check a fixed suite instead:

```bash
pr-control . --base main --head feature --targets tests/test_utils.py
```

Both refs run the **head version** of the tests; only the code under test
changes. The base run is the negative control: the package resolves from a
worktree of the base ref (via `PYTHONPATH`, cwd, and pytest's
`--import-mode=importlib`, which stops pytest from prepending the head tree to
`sys.path`). No project dependencies are installed by the verification command; refs run in
temporary `git worktree`s that are removed afterwards.

### Shared test helpers without head-source precedence

Some suites import `helpers`, `conftest`, or sibling test modules directly.
Pytest's default `importlib` mode does not add their directory to the import
path. If collection fails for that reason, opt into explicit head test support:

```bash
pr-control . --base BASE --head HEAD --targets tests/test_utils.py \
  --test-support tests --test-support tests/shared
```

Each directory is resolved inside the detached head worktree and appended
**after** the selected ref's root and `src` paths. This makes shared head test
helpers available without placing them before base source. Missing directories
and paths or symlinks escaping head are errors. There is no automatic retry or
switch to `prepend`; default behavior is unchanged. JSON and Markdown record
these directories so the comparison can be reproduced.

This only changes import search paths. Head helpers still execute head code;
they are not sandboxed or treated as base source. If a head helper path provides
a package absent on base, the observed wrong-worktree import still makes the
comparison inconclusive. `--import-mode prepend` can still shadow source, and
observed shadowing is rejected. Dynamically rewritten paths and subprocess-only
imports remain outside the provenance guarantee. In particular, pr-control's
own subprocess-driven tests can run with `--test-support tests`, but their
outcome transitions stay `transition_unverified_source` rather than being
upgraded to `fix_confirmed`.

## Parallel pytest suites with xdist

If the environment has `pytest-xdist`, existing `PYTEST_ADDOPTS` such as
`-n 2 --dist load` can run the selected head tests in workers on both refs.
The controller writes the final report; workers return their observed local
import paths instead of racing to overwrite the shared JSON report. Worker
collection is counted once, not once per worker. The same completeness,
assertion classification and wrong-worktree checks still apply.

The executed CPU coverage includes one/two workers, `load`, `loadscope`,
`loadfile` and `worksteal` scheduling, duplicate node IDs, early stop, worker
crash, collection mismatch and missing worker evidence. A crashed or incomplete
worker run fails closed. Tests that import no local source still produce
`transition_unverified_source`, never `fix_confirmed`. xdist is optional and
is not installed by pr-control. Remote workers, subprocess-only imports,
custom schedulers and plugins that rewrite reports remain outside this
coverage. This is not a sandbox or tamper-proof worker attestation.

## Verdicts

Per changed test:

| verdict | base | head | meaning |
| --- | --- | --- | --- |
| `fix_confirmed` | failed | passed | evidence the test binds to the change *in this environment*: it fails on base and passes on head. Not proof the fix is correct in general |
| `passes_both` | passed | passed | toothless as a fix control (fine as coverage) |
| `regression_introduced` | passed | failed | the PR breaks it |
| `fails_both` | failed | failed | fix incomplete, or the test is broken |
| `inconclusive` | varies | varies | collection, dependency or import-provenance check failed |
| `unrunnable` | error | varies | setup/teardown failed; not evidence of a fix |
| `transition_unverified_source` | failed | passed | outcome changed, but local-package source origin was not observed |
| `missing_on_head` | - | - | test collected on base but not head |

Plus a same-suite diff: failures that appeared on head, and failures that
disappeared. A nonzero pytest session exit without recorded failures, or a zero exit despite recorded failures, is incomplete. An incomplete run, execution error or different collected test set is labeled
and exits 1 even with `--fail-on never`. Same-suite completeness requires every selected test to finish in each run,
and both runs to produce the same test IDs with the same occurrence counts,
without execution errors. A collection hook that repeats a test on just one
ref makes the comparison inconclusive, even when both runs finish cleanly.
JSON and Markdown reports list the differing per-node occurrence counts. Matching early
stops from `-x` or `--maxfail` are incomplete even when their observed node sets match. Per-test rows still
show observed transitions; a complete suite is required for a successful exit.

## Output

Markdown to stdout by default; `--json-out report.json` and
`--md-out report.md` write files. Every report carries the environment
fingerprint (python/pytest/platform) and the exact refs. Failed tests include
phase, classification and a bounded traceback for manual interpretation.
Non-text bytes written by tests to stdout/stderr are decoded with replacement
characters for diagnostics. They cannot change the structured pytest verdict;
a missing report or timeout remains incomplete.
Reports can contain absolute paths and exception text from the tested project;
review and redact them before sharing. Do not run tests with secrets in scope.

Exit codes: `0` ran clean, `1` input/environment error (same commit, missing
ref, nothing to run), `2` with `--fail-on regression` (new failures on head)
or `--fail-on no-control` (no test shows `fix_confirmed`).

## Requirements

- Python >= 3.9, pytest (run pr-control with the interpreter whose
  environment can run the repo's tests)
- git with worktree support

## Security

**Running a PR's tests executes that PR's code on your machine.** Untrusted
test code can read files, make network calls, or try to escape - pr-control
does not sandbox anything. Run it in a disposable VM or container with no
tokens, credentials, SSH keys, or secrets, and never on a CI runner that
holds privileged credentials.

## Strict XPASS is not a negative control

A pytest strict XPASS means the test body passed but an obsolete expected-failure
mark made its report fail. pr-control classifies call-phase failures without an
underlying exception as execution errors, not `fix_confirmed` or
`regression_introduced`. The suite is incomplete and the CLI exits 1. An inactive
xfail mark still allows a genuine assertion failure to serve as a control.
Ordinary expected failures remain skipped, not proof of a fix.

For dependency-complete CI, add `--require-no-skips` to reject any selected
test skipped on either ref, including collection-time `pytest.importorskip`
and module-level skips. This returns 1 even if another test supplies a valid
negative control; it is independent of `--fail-on`. Reports are still written,
and include collection skips and the enabled policy. Without the flag, skips
remain permitted and reported. The flag also rejects expected-failure skips,
so use it only when the selected suite is expected to run without skips.
With xdist, worker collection skips are aggregated once in the controller.


## unittest lifecycle failures

Assertions from standard `unittest` setup, teardown or cleanup are execution
errors, not fix evidence, even when pytest reports them in its call phase.
The same rule covers `IsolatedAsyncioTestCase` lifecycle methods. Test-body
assertions still count as ordinary negative controls. Classification uses the
standard unittest lifecycle traceback frames; custom runners that remove or
rewrite tracebacks can hide this distinction. Always inspect failure evidence.

## Limitations

- Python/pytest only.
- Both runs share one interpreter environment (the one you invoke pr-control
  with); only the repository source differs between refs. If base and head
  need different dependency versions, the mismatch shows up in the outcomes
  as-is - pr-control reports it (per-ref `collection_errors` and
  `inconclusive` verdicts) rather than hiding it, but it does not
  manage environments.
- Isolation is limited to the source tree: temporary git worktrees mean your
  checkout source files are not edited. Git worktree metadata is temporarily
  changed. Worktrees do not contain test side effects - see Security.
- The default importlib mode avoids common path-shadowing mistakes. The
  report records loaded local-package file paths and rejects observed
  imports from the wrong worktree. This is diagnostic evidence, not
  tamper-proof attestation: tests can change paths or falsify reports.
  Native extensions, editable installs, namespace packages and custom import
  hooks may need manual inspection. A failure-to-pass transition without
  observed local-package imports is `transition_unverified_source`, never
  `fix_confirmed`.
- Environment-dependent failures (GPUs, network, optional deps) show up as
  they are; pr-control reports outcomes, it does not judge them.
- On pytest versions emitting separate subtest reports, failed subtests sharing a parent nodeid are conservatively execution errors;
  a later parent pass cannot erase them. Detailed subtest attribution is not
  supported.
- Unexpected call exceptions (including unittest setup errors) are classified
  as execution errors, not assertion failures. This is conservative: a real
  regression that raises an unexpected exception needs manual investigation.
  Explicit `pytest.fail()` and missing `pytest.raises(...)` failures count as
  assertion evidence alongside `AssertionError`. This classifies the failure,
  not its cause; the bounded traceback still needs manual inspection.
  An AssertionError inside unittest setup cannot be separated from its test
  body by this diagnostic; inspect the failure before drawing conclusions.
- `fix_confirmed` is per-test evidence in one environment, not a global
  verdict on the PR.

## License

MIT. Built with AI assistance. Tests cover documented scenarios, not all possible environments.

Duplicate test IDs are aggregated conservatively by worst phase outcome. Equal
occurrence counts make the selected suite comparable; they do not attribute
a separate result to each duplicate occurrence.

## Repeat stability checks

```bash
pr-control . --base BASE --head HEAD --repeats 3 --fail-on no-control \
  --require-no-skips --json-out evidence.json --md-out evidence.md
```

`--repeats N` (1-20, default 1) runs each ref's selected head tests in N fresh
pytest subprocesses. Every attempt must be complete. Observed changes in test
outcomes, collection occurrence counts, skip sets or source-verification status make all
verdicts inconclusive and exit 1, even with `--fail-on never`. A stable
fail-on-base/pass-on-head transition still needs manual failure inspection;
repetition does not prove the source change caused it or detect every flake.

Attempts share their ref's worktree; files written by tests are not reset.
This can reveal state-sensitive tests, but is not hermetic repeated execution.
The worst-case pytest budget is `2 * N * --timeout`, plus process/reporting
work. Each attempt has its own timeout. Dependencies are not installed.
JSON retains each attempt's outcomes, counts, imports, skips, exit status,
collection errors and bounded failure evidence. The top-level run uses the
first attempt's outcomes for compatibility, and rejects instability rather
than averaging or voting. Markdown lists each attempt's outcomes. Skip policy
aggregates all attempts, including collection and runtime skips seen only
later. Repeated success is selected-test evidence, not a reliability guarantee.

## Optional base-failure witness policy

A failed-to-passed transition can come from the wrong assertion. To require a
specific diagnostic on named control tests, supply a JSON object whose keys are
exact pytest node IDs and values are nonempty literal exception-message fragments:

```json
{"tests/test_calc.py::test_add": "addition must preserve both operands"}
```

```bash
pr-control . --base BASE --head HEAD --targets tests/test_calc.py \
  --base-failure-witness witnesses.json --fail-on no-control
```

Every named test must have a `fix_confirmed` transition, and every collected
occurrence in every base attempt must contain the literal fragment in its call
assertion exception message. Explicit `pytest.fail` messages are supported.
Only the first 8,000 characters of each exception message are retained; a
fragment outside that bound cannot match. Regex syntax has no special meaning. Matching a test's source line, comment or
traceback context alone is insufficient. Missing/unselected nodes, skipped tests,
execution errors, missing message evidence and any mismatched attempt fail the
policy with exit 1, including with `--fail-on never`. JSON and Markdown retain the
expectations, match results and reasons. Invalid or duplicate-key policy files
fail before creating worktrees. No witness policy is applied by default; ordinary
verdicts and `--fail-on` behavior are unchanged.

This gates on observed diagnostic text, not on causality or a security guarantee.
Pytest assertion rewriting can include observed values in an exception message;
choose a distinctive explicit assertion message and inspect the failure evidence.
Tests/plugins can fabricate messages or reports. Repeats still share worktrees and
do not prove absence of flakes. The policy does not interpret expected behavior,
validate a patch, or make untrusted tests safe to execute.
