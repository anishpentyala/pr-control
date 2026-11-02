"""pr-control CLI: verify a change's tests against both sides of the diff."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter
from pathlib import Path

from .core import (
    PrControlError,
    load_failure_witnesses,
    check_failure_witnesses,
    changed_test_files,
    check_repo,
    cleanup,
    env_fingerprint,
    make_worktree,
    run_repeated_pytest,
    suite_diff,
    verdicts,
)
from .report import render_json, render_markdown


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pr-control",
        description=(
            "Negative control for pull requests: run the PR's tests against the "
            "base ref AND the head ref, then diff the suite results."
        ),
    )
    p.add_argument("repo", type=Path, help="path to a local clone of the repository")
    p.add_argument("--base", required=True, help="base ref (merge-base, main sha, ...)")
    p.add_argument("--head", required=True, help="head ref (PR head sha, branch, ...)")
    p.add_argument(
        "--targets", nargs="*", default=None,
        help="pytest targets to run on both refs (default: test files changed between base and head)",
    )
    p.add_argument("--base-failure-witness", type=Path, default=None,
                   help="JSON object of exact node IDs to literal assertion-message fragments; require every named control to match on every base attempt")
    p.add_argument("--repeats", type=int, default=1, help="fresh pytest subprocesses per ref (1-20); observed instability fails closed")
    p.add_argument("--timeout", type=int, default=600, help="pytest timeout per ref in seconds (default 600)")
    p.add_argument(
        "--import-mode", choices=["importlib", "prepend", "append"], default="importlib",
        help="pytest import mode. importlib (default) avoids common test-path shadowing; "
        "use prepend only if the suite requires it.",
    )
    p.add_argument("--test-support", action="append", default=[], metavar="DIR",
                   help="head test-helper directory appended after selected source paths; repeatable, no automatic fallback")
    p.add_argument("--require-no-skips", action="store_true",
                   help="exit 1 if either side skips a selected test; default permits and reports skips")
    p.add_argument("--json-out", type=Path, default=None, help="write the JSON report here")
    p.add_argument("--md-out", type=Path, default=None, help="write the Markdown report here")
    p.add_argument(
        "--fail-on", choices=["never", "regression", "no-control"], default="never",
        help="exit 2 when: regression = new failures appear on head; no-control = no test shows fix_confirmed (default: never)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()
    worktrees = []
    tmp = None
    try:
        if not 1 <= args.repeats <= 20:
            raise PrControlError("--repeats must be between 1 and 20")
        if args.timeout <= 0:
            raise PrControlError("--timeout must be positive")
        if args.targets == []:
            raise PrControlError("--targets needs at least one target")
        expected_witnesses = load_failure_witnesses(args.base_failure_witness) if args.base_failure_witness else None
        base_sha, head_sha = check_repo(repo, args.base, args.head)
        targets = args.targets
        if targets is None:
            targets = changed_test_files(repo, base_sha, head_sha)
            if not targets:
                raise PrControlError(
                    "no test files changed between base and head; pass --targets explicitly to run a suite"
                )
        tmp = Path(tempfile.mkdtemp(prefix="pr-control-"))
        base_wt = make_worktree(repo, base_sha, tmp / "base")
        worktrees.append(base_wt)
        head_wt = make_worktree(repo, head_sha, tmp / "head")
        worktrees.append(head_wt)

        # Both sides run the HEAD version of the tests; only the code under
        # test changes. On base: head's tests against base's code (the control).
        base_run = run_repeated_pytest(base_wt, head_wt, "base", targets, args.timeout, args.import_mode, args.test_support, args.repeats)
        head_run = run_repeated_pytest(head_wt, head_wt, "head", targets, args.timeout, args.import_mode, args.test_support, args.repeats)
        rows = verdicts(base_run, head_run)
        summary = Counter(r["verdict"] for r in rows)
        payload = {
            "repo": str(repo),
            "repeats": args.repeats,
            "collection_errors": {
                r.ref: r.collection_error for r in (base_run, head_run) if r.collection_error
            },
            "base": base_sha,
            "head": head_sha,
            "targets": targets,
            "test_support": args.test_support,
            "require_no_skips": args.require_no_skips,
            "runs": {r.ref: {"returncode": r.returncode, "imports": r.imports, "source_verified": r.source_verified, "collected_counts": r.collected_counts, "collection_skips": r.collection_skips, "runtime_skips": r.runtime_skips, "attempts": r.attempts} for r in (base_run, head_run)},
            "env": env_fingerprint(),
            "verdicts": rows,
            "summary": dict(summary),
            "suite_diff": suite_diff(base_run, head_run),
        }
        if expected_witnesses is not None:
            payload["base_failure_witness"] = check_failure_witnesses(expected_witnesses, rows, base_run)
        md = render_markdown(payload)
        js = render_json(payload)
        if args.md_out:
            args.md_out.write_text(md)
        if args.json_out:
            args.json_out.write_text(js)
        if not args.md_out and not args.json_out:
            sys.stdout.write(md)

        if expected_witnesses is not None and not payload["base_failure_witness"]["passed"]:
            print("pr-control: base failure witness policy not satisfied", file=sys.stderr)
            return 1
        has_skips = any(run.collection_skips or run.runtime_skips for run in (base_run, head_run)) or any(
            o.outcome == "skipped" for run in (base_run, head_run) for o in run.outcomes
        )
        if args.require_no_skips and has_skips:
            print("pr-control: skipped selected tests violate --require-no-skips", file=sys.stderr)
            return 1
        if not payload["suite_diff"]["complete"]:
            return 1
        if args.fail_on == "regression" and payload["suite_diff"]["new_failures_on_head"]:
            return 2
        if args.fail_on == "no-control" and summary.get("fix_confirmed", 0) == 0:
            return 2
        return 0
    except (PrControlError, OSError) as exc:
        print(f"pr-control: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if worktrees:
            cleanup(repo, worktrees)
        if tmp is not None:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
