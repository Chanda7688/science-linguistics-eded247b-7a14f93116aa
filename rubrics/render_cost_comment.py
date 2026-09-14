#!/usr/bin/env python3
"""Render the aggregated LLM-cost summary as a PR sticky comment.

    render_cost_comment.py <summary.json>

Reads the summary.json produced by `claude_costs.py aggregate` (which rolls up the per-stage
partials: harbor trials + analyze + `harbor check` + the claude-CLI deep review + AVA). Advisory —
gives the maintainer a per-run $/task figure. Estimates only (see the note)."""
import json
import sys

HEADER = "## 💰 LLM cost (this run)"

STAGE_LABEL = {
    "rubric-review": "Rubric review (harbor check)",
    "pass2-agent-trial": "pass@2 agent trials",
    "pass2-analyze": "pass@2 analyze",
    "deep-review": "Deep review",
    "cheat-agent-trial": "Cheat trial",
    "cheat-analyze": "Cheat analyze",
    "ava-review": "Verifier audit (AVA)",
}


def main():
    if len(sys.argv) < 2:
        print(f"{HEADER}\n\n_No cost summary found._")
        return
    try:
        d = json.load(open(sys.argv[1]))
    except Exception as e:
        print(f"{HEADER}\n\n_Cost summary could not be read ({type(e).__name__})._")
        return

    total = d.get("total_estimated_cost_usd", 0.0) or 0.0
    by_stage = d.get("cost_by_stage", {}) or {}
    sessions = d.get("sessions_or_requests_by_stage", {}) or {}
    no_cost = d.get("records_without_cost", 0)

    lines = [HEADER, "", f"**Total: ${total:,.2f}** across {d.get('record_count', 0)} LLM session(s)/job(s).", ""]
    lines += ["| Stage | Est. cost | Sessions |", "|---|--:|--:|"]
    for stage in sorted(by_stage, key=lambda s: -by_stage[s]):
        label = STAGE_LABEL.get(stage, stage)
        lines.append(f"| {label} | ${by_stage[stage]:,.2f} | {sessions.get(stage, '—')} |")
    lines.append(f"| **Total** | **${total:,.2f}** | {d.get('estimated_session_or_request_count', '')} |")

    tok = d.get("token_usage", {}) or {}
    if tok:
        parts = [f"{k.replace('_tokens','').replace('_',' ')}: {v:,}" for k, v in tok.items()]
        lines += ["", "<sub>Tokens — " + " · ".join(parts) + "</sub>"]

    if no_cost:
        lines += [f"\n<sub>⚠️ {no_cost} record(s) had no cost figure (e.g. `harbor check` may not "
                  "emit one) and are excluded from the total.</sub>"]
    lines += [
        "",
        "<sub>Advisory. Client/provider-reported **estimates**, not an invoice. Agent-trial cost is the "
        "provider-reported figure from the run (via OpenRouter); the Claude review stages use Anthropic "
        "list prices (as of 2026-07-23).</sub>",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
