"""Markdown + JSON rendering for a verification result."""

from __future__ import annotations

import json


def render_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_markdown(payload: dict) -> str:
    lines = [
        f"# pr-control report: {payload['repo']}",
        "",
        f"- base `{payload['base']}` vs head `{payload['head']}`",
        f"- env: python {payload['env']['python']}, pytest {payload['env']['pytest']}, {payload['env']['platform']}",
        "",
        "## Verdicts (selected head tests)",
        "",
        "| test | base | head | verdict |",
        "| --- | --- | --- | --- |",
    ]
    for row in payload["verdicts"]:
        lines.append(f"| `{row['test']}` | {row['base'] or '-'} | {row['head'] or '-'} | **{row['verdict']}** |")
    sd = payload["suite_diff"]
    lines += [
        "",
        "## Suite diff (same targets on both refs)",
        "",
        f"- complete runs: {sd['complete']}",
        f"- failed on base: {len(sd['base_failed'])}; failed on head: {len(sd['head_failed'])}",
        f"- new failures on head: {len(sd['new_failures_on_head'])}",
        f"- fixed on head: {len(sd['fixed_on_head'])}",
    ]
    if sd["new_failures_on_head"]:
        lines += ["", "### New failures on head", ""]
        lines += [f"- `{t}`" for t in sd["new_failures_on_head"]]
    for ref, error in payload.get("collection_errors", {}).items():
        lines += ["", f"### {ref}: incomplete run", "", "```", str(error), "```"]
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
    ]
    return "\n".join(lines) + "\n"
