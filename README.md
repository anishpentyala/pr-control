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

With no `--targets`, pr-control runs the test files the PR changed
(`git diff --name-only base...head`). To check a fixed suite instead:

```bash
pr-control . --base main --head feature --targets tests/test_utils.py
```

Both refs run the **head version** of the tests; only the code under test
changes. The base run is the negative control: the package resolves from a
worktree of the base ref (via `PYTHONPATH`, cwd, and pytest's
`--import-mode=importlib`, which stops pytest from prepending the head tree to
`sys.path`). No project dependencies are installed by the verification command; refs run in
temporary `git worktree`s that are removed afterwards.

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
