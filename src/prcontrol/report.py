"""Markdown + JSON rendering for a verification result."""

from __future__ import annotations

import json
import html
import re


def _inline(value: str) -> str:
    """Keep literal identifiers inside one Markdown cell/heading."""
    return "<code>" + (html.escape(str(value), quote=False).replace("`", "&#96;")
            .replace("|", "&#124;").replace("\r", r"\r").replace("\n", r"\n")
            .replace("\t", r"\t")) + "</code>"


def _code_block(value: str) -> list[str]:
    """Keep diagnostic text literal and inside its own Markdown code block.

    Longer source fences must not close the report's fence. Choose a delimiter
    longer than every backtick run in the text instead of altering evidence.
    This contains Markdown structure, not terminal controls or adversarial code.
    """
    text = str(value)
    width = max((len(match.group()) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, width + 1)
    return [fence + "text", text, fence]


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_markdown(payload: dict) -> str:
    lines = [
        f"# pr-control report: {_inline(payload['repo'])}",
        "",
        f"- base `{payload['base']}` vs head `{payload['head']}`",
        f"- env: python {payload['env']['python']}, pytest {payload['env']['pytest']}, {payload['env']['platform']}",
        "",
        "## Verdicts (selected head tests)",
        "",
        "| test | base | head | verdict |",
        "| --- | --- | --- | --- |",
    ]
    if payload.get("require_clean_worktrees"):
        lines[6:6] = ["- tracked-file integrity policy: before/after each attempt; untracked scratch allowed; not tamper-proof", ""]
    if payload.get("per_test_source"):
        lines[6:6] = ["- per-test source gate: observed local Python production calls during each test call phase; not causality or tamper-proof tracing", ""]
    if payload.get("test_overlay"):
        overlay = payload["test_overlay"]
        lines[6:6] = ["- explicit test overlay: " + overlay["scope"],
                      "- config/ancestor fixture bytes checked; no automatic production/helper copying", ""]
    if payload.get("test_support"):
        lines[6:6] = ["- explicit head test support (after selected source paths): "
                      + ", ".join(f"{_inline(path)}" for path in payload["test_support"]), ""]
    if payload.get("repeats", 1) > 1:
        lines[6:6] = [f"- repeated stability check: {payload['repeats']} fresh pytest subprocesses per ref; shared worktree, no file reset", ""]
    for row in payload["verdicts"]:
        lines.append(f"| {_inline(row['test'])} | {row['base'] or '-'} | {row['head'] or '-'} | **{row['verdict']}** |")
    for ref, run in payload.get("runs", {}).items():
        if run.get("deselected"):
            lines += ["", f"### {ref}: deselected tests", "",
                      "Reported by pytest selection hooks; not skipped tests or executed coverage."]
            lines += [f"- {_inline(node)}" for node in run["deselected"]]
        runtime = run.get("runtime_skips", [])
        if runtime:
            lines += ["", f"### {ref}: skipped during execution", ""]
            lines += [f"- {_inline(node)}" for node in runtime]
        skipped = run.get("collection_skips", [])
        if skipped:
            lines += ["", f"### {ref}: skipped during collection", ""]
            lines += [f"- {_inline(node)}" for node in skipped]
    if payload.get("require_no_deselections"):
        lines += ["", "- policy: --require-no-deselections (reported deselection fails the command)"]
    if payload.get("require_no_skips"):
        lines += ["", "- policy: --require-no-skips (runtime and collection skips fail the command)"]
    if payload.get("repeats", 1) > 1:
        lines += ["", "## Repeated attempts", "", "Every attempt is retained in JSON. Repetition detects observed instability, not all flakes."]
        for ref, run in payload.get("runs", {}).items():
            for attempt in run.get("attempts", []):
                lines.append(f"- {ref} attempt {attempt['attempt']}: exit {attempt['returncode']}; source verified {attempt['source_verified']}; incomplete {bool(attempt['collection_error'])}")
                if attempt.get("deselected"):
                    lines.append("  - reported deselection: " + ", ".join(_inline(node) for node in attempt["deselected"]))
                for node in attempt.get("outcomes", []):
                    lines.append(f"  - {_inline(node['nodeid'])}: {node['outcome']}")
    if "base_failure_witness" in payload:
        policy = payload["base_failure_witness"]
        lines += ["", "## Base failure witness policy", "",
                  f"- passed: {policy['passed']}",
                  "- literal assertion exception-message matching only; not a causal proof"]
        for expected in policy["expectations"]:
            lines.append(f"- {_inline(expected['test'])}: matched {expected['matched']}; literal {_inline(expected['literal_message'])}")
            lines.extend(f"  - {reason}" for reason in expected["reasons"])
    detailed = [row for row in payload["verdicts"] if row.get("base_failure_details") or row.get("head_failure_details")]
    if detailed:
        lines += ["", "## Failure evidence", "", "Inspect these failures before interpreting outcome transitions."]
        for row in detailed:
            lines += ["", f"### {_inline(row['test'])}"]
            for ref in ("base", "head"):
                for failure in row.get(f"{ref}_failure_details", []):
                    lines += ["", f"{ref}: {failure['phase']} / {failure['classification']}", ""]
                    lines += _code_block(failure['traceback'])
    sd = payload["suite_diff"]
    lines += [
        "",
        "## Suite diff (same targets on both refs)",
        "",
        f"- complete comparable suite: {sd['complete']}",
        f"- collected only on base: {len(sd['only_on_base'])}; only on head: {len(sd['only_on_head'])}",
        f"- failed on base: {len(sd['base_failed'])}; failed on head: {len(sd['head_failed'])}",
        f"- new failures on head: {len(sd['new_failures_on_head'])}",
        f"- fixed on head: {len(sd['fixed_on_head'])}",
    ]
    if sd.get("occurrence_mismatches"):
        lines += ["", "### Test occurrence count mismatches", "",
                  "The selected test IDs match, but their execution counts differ; the suite is not comparable."]
        for node, counts in sd["occurrence_mismatches"].items():
            lines.append(f"- {_inline(node)}: base {counts['base']}, head {counts['head']}")
    if sd["new_failures_on_head"]:
        lines += ["", "### New failures on head", ""]
        lines += [f"- {_inline(t)}" for t in sd["new_failures_on_head"]]
    for ref, error in payload.get("collection_errors", {}).items():
        lines += ["", f"### {ref}: incomplete run", ""]
        lines += _code_block(error)
    if payload.get("test_overlay"):
        lines += ["", "## Test overlay manifest", "",
                  "Production source paths remain verified; selected test/fixture bytes are explicitly replaced."]
        for ref, files in payload["test_overlay"]["files"].items():
            for item in files:
                lines.append(f"- {ref}: {_inline(item['path'])}; head sha256 `{item['head_sha256']}`; previous `{item['previous_sha256']}`")
    lines += ["", "## Source provenance", ""]
    for ref, run in payload.get("runs", {}).items():
        lines.append(f"- {ref}: exit {run['returncode']}; source imports verified: {run['source_verified']}")
    lines += ["", "A transition is selected-test evidence in this environment, not proof of overall fix correctness."]
    summary = payload["summary"]
    lines += [
        "",
        "## Summary",
        "",
        f"- fix_confirmed: {summary.get('fix_confirmed', 0)}",
        f"- passes_both: {summary.get('passes_both', 0)}",
        f"- regression_introduced: {summary.get('regression_introduced', 0)}",
        f"- fails_both: {summary.get('fails_both', 0)}",
        f"- inconclusive: {summary.get('inconclusive', 0)}",
        f"- unrunnable: {summary.get('unrunnable', 0)}",
        f"- transition_unverified_source: {summary.get('transition_unverified_source', 0)}",
        f"- transition_with_skips: {summary.get('transition_with_skips', 0)}",
    ]
    return "\n".join(lines) + "\n"
