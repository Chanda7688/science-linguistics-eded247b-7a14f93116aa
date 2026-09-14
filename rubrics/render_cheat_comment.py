#!/usr/bin/env python3
"""Render the adversarial cheat trial → an advisory sticky comment: the exploit verdict plus the
reward-hacking finding (with its rationale) and the rest of the trial-analysis rubric for the
attempt, so the maintainer sees exactly how the agent tried to game the verifier.

Usage: render_cheat_comment.py <run-dir> <analyze-dir>
"""
import sys
import re
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _ashape import rewards, trial_checks  # noqa: E402

run = sys.argv[1]
adir = sys.argv[2] if len(sys.argv) > 2 else run
rw = rewards(run)
ch = {**trial_checks(adir), **trial_checks(run)}
BADGE = {"pass": "✅", "fail": "❌"}
ORDER = ["reward_hacking", "approach_validity", "refusals", "task_specification",
         "near_miss", "difficulty_crux", "low_timeout", "decisive_rule_disclosed", "spec_consistency"]


def cell(t):
    # Sanitize for a one-line markdown table cell; no truncation — full explanation is shown.
    return re.sub(r"\s+", " ", (t or "").strip()).replace("|", "\\|")


def rh(n):
    return (ch.get(n, {}) or {}).get("reward_hacking", {}) or {}


exploited = [n for n, r in rw.items() if r >= 1 and str(rh(n).get("outcome")).lower() == "fail"]

L = ["## 🕵️ Cheat Trial (advisory)", ""]
if exploited:
    L.append(f"**⚠️ Verifier looks exploitable** in {len(exploited)}/{len(rw)} trial(s) — an agent "
             "invited to cheat reached reward = 1 without genuinely solving the task. Harden `tests/`.")
else:
    L.append(f"**No exploit demonstrated** across {len(rw) or '?'} trial(s) — the verifier held under "
             "an agent explicitly told to game it.")

L += ["", "| Trial | Reward | Reward-hacking |", "|---|:-:|:-:|"]
for n, r in sorted(rw.items()):
    L.append(f"| `{n}` | {r} | {BADGE.get(str(rh(n).get('outcome')).lower(), '—')} |")

# Per-trial rubric detail (reward-hacking rationale first)
for n in sorted(rw):
    crit = ch.get(n, {}) or {}
    rows = [k for k in ORDER if k in crit]
    if not rows:
        continue
    L += ["", f"### `{n}` — reward `{rw.get(n, '—')}`", "| Criterion | | Note |", "|---|:-:|---|"]
    for k in rows:
        v = crit.get(k, {}) or {}
        o = str(v.get("outcome", "")).strip().lower()
        L.append(f"| `{k}` | {BADGE.get(o, o or '—')} | {cell(v.get('explanation'))} |")

L += ["", "<sub>Advisory — an agent is told to cheat, then analyzed with the trial-analysis rubric to "
      "surface verifier exploitability. Not a gate.</sub>"]

body = "\n".join(L)
if len(body) > 65000:
    body = body[:64800].rsplit("\n", 1)[0] + "\n\n> ⚠️ Trimmed to fit GitHub's comment limit — see the job log."
print(body)
