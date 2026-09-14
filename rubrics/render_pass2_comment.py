#!/usr/bin/env python3
"""Render the pass@k trajectory analysis → a rich sticky comment (tb2 pre-check style): the
valid-fail verdict plus, per trajectory, the reward and the full trial-analysis rubric
(outcome + note for each criterion) so a reviewer can see WHY each attempt failed.

Usage: render_pass2_comment.py <run-dir> <analyze-dir> [n_attempts]
"""
import sys
import re
import json
import glob
import os
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from _ashape import rewards, trial_checks  # noqa: E402

run = sys.argv[1] if len(sys.argv) > 1 else "pass2-output"
adir = sys.argv[2] if len(sys.argv) > 2 else "pass2-analyze"
N = sys.argv[3] if len(sys.argv) > 3 else ""

# a fail is "valid" (proves genuine difficulty) unless one of these is FAIL — i.e. it's really
# a task bug, a refusal, a spec problem, or the agent never made a legit attempt.
GATING = ["approach_validity", "reward_hacking", "refusals", "task_specification"]
INFO = ["near_miss", "difficulty_crux", "low_timeout", "decisive_rule_disclosed", "spec_consistency"]
BADGE = {"pass": "✅", "fail": "❌"}
# Timeouts are NOT a valid fail (mirrors count_valid_fails.py / handshake-orchestration-tb2).
SETUP_TIMEOUTS = {"EnvironmentStartTimeoutError", "AgentSetupTimeoutError", "VerifierTimeoutError"}

rw = rewards(run)
ch = {**trial_checks(adir), **trial_checks(run)}

# exception_type per trial from the per-trial result.json (has trial_name + exception_info)
exc = {}
for _res in glob.glob(f"{run}/**/result.json", recursive=True):
    try:
        _d = json.load(open(_res))
    except Exception:
        continue
    _tn = _d.get("trial_name")
    if not _tn:
        _b = os.path.basename(os.path.dirname(_res))
        _tn = _b if "__" in _b else None
    if _tn:
        _e = (_d.get("exception_info") or {}).get("exception_type")
        if _e:
            exc[_tn] = _e


def cell(t):
    # Sanitize for a one-line markdown table cell; no truncation — full explanation is shown.
    return re.sub(r"\s+", " ", (t or "").strip()).replace("|", "\\|")


def outc(t, k):
    return str(((ch.get(t, {}) or {}).get(k, {}) or {}).get("outcome", "")).strip().lower()


def is_valid_fail(t):
    if rw.get(t, 1.0) >= 1:
        return False, "solved (reward ≥ 1)"
    e = exc.get(t)
    if e in SETUP_TIMEOUTS or (e and e != "AgentTimeoutError"):
        return False, f"infra error ({e}) — not a valid fail"
    bad = [k for k in GATING if outc(t, k) == "fail"]
    if bad:
        return False, "disqualified: " + ", ".join(bad)
    lt = outc(t, "low_timeout")
    if e == "AgentTimeoutError":
        if lt != "pass":
            return False, "timeout while progressing — not a valid fail"
    elif lt == "fail":
        return False, "timeout while progressing — not a valid fail"
    return True, "valid failure"


trials = sorted(rw) or sorted(ch)
valid = [t for t in trials if is_valid_fail(t)[0]]
k = N or (str(len(trials)) if trials else "?")
status = "✅" if len(valid) >= 1 else "❌"

L = [f"## 🎯 pass@{k} pre-check — {status}", ""]
L.append(f"**Valid agent failures: {len(valid)} / {len(trials)}** (need ≥ 1). "
         + ("Proceeding — the task genuinely stumps the agent." if valid
            else "No valid failure — the task may be too easy, or a verifier/infra bug made the fails not count."))

if not trials:
    L.append("\n_No trajectories found to analyze — see the `pass2` job log._")
    print("\n".join(L))
    sys.exit(0)

for t in trials:
    ok, why = is_valid_fail(t)
    r = rw.get(t, "—")
    tag = "🟢 valid failure" if ok else ("🔵 " + why if "solved" in why else "⚪ " + why)
    L += ["", f"### `{t}` — reward `{r}` · {tag}"]
    rows = [k2 for k2 in (GATING + INFO) if k2 in (ch.get(t, {}) or {})]
    if rows:
        L += ["| Criterion | | Note |", "|---|:-:|---|"]
        for k2 in rows:
            v = (ch.get(t, {}) or {}).get(k2, {}) or {}
            o = str(v.get("outcome", "")).strip().lower()
            L.append(f"| `{k2}` | {BADGE.get(o, o or '—')} | {cell(v.get('explanation'))} |")
    else:
        L.append("_No analysis criteria recorded for this trajectory._")

L += ["", "<sub>Agent trials analyzed with the TB3 trial-analysis rubric (harbor analyze). A "
      "*valid failure* means the agent genuinely tried and couldn't — not a task bug, refusal, or "
      "reward-hack. ≥ 1 valid failure is required.</sub>"]

body = "\n".join(L)
if len(body) > 65000:
    body = body[:64800].rsplit("\n", 1)[0] + "\n\n> ⚠️ Trimmed to fit GitHub's comment limit — see the `pass2` job log."
print(body)
