#!/usr/bin/env python3
"""Render harbor 0.18 `check` verdicts → a full graded-rubric sticky comment (tb2 dynamo-eval
style): a compact scan table (result per criterion) followed by the FULL, untruncated note for
EVERY criterion — failures first — and an actionable footer. No per-criterion truncation; the
only limit is GitHub's 65 536-char sticky cap, applied as a last-resort backstop.

Usage: render_rubric_comment.py <jobs-dir>
"""
import sys
import glob
import json
import re

GH_LIMIT = 65000

jobs = sys.argv[1] if len(sys.argv) > 1 else "."


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return None


# harbor check writes check-*/artifacts/check-result.json (flat {criterion: {outcome, explanation}})
crit = {}
for f in (glob.glob(f"{jobs}/**/artifacts/check-result.json", recursive=True)
          + glob.glob(f"{jobs}/**/check-result.json", recursive=True)):
    d = load(f)
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict) and "outcome" in v:
                crit[k] = v
if not crit:  # fallback: analyze-style analysis.json
    for f in glob.glob(f"{jobs}/**/analysis.json", recursive=True):
        d = load(f) or {}
        for r in (d.get("results") or d.get("trials") or [d]):
            for k, v in (r.get("checks") or {}).items():
                crit[k] = v or {}


def norm(outcome):
    o = str(outcome or "").strip().lower()
    if o in ("pass", "passed", "ok", "true"):
        return "pass"
    if o in ("fail", "failed", "false"):
        return "fail"
    if o in ("na", "n/a", "not_applicable", "skip", "skipped", "none", ""):
        return "na"
    return o


BADGE = {"pass": "✅ PASS", "fail": "❌ FAIL", "na": "⚪ N/A"}


def full(k):
    """Full explanation, whitespace-collapsed so it flows in one markdown bullet (never truncated)."""
    return re.sub(r"\s+", " ", (crit[k].get("explanation") or "").strip()) or "(no explanation given)"


names = list(crit.keys())
fails = [k for k in names if norm(crit[k].get("outcome")) == "fail"]
nas = [k for k in names if norm(crit[k].get("outcome")) == "na"]
passes = len(names) - len(fails) - len(nas)

if not crit:
    print("## 🧪 Implementation Rubric Review — ❌ FAIL\n\n"
          "**Verdict:** FAIL — no rubric verdicts found (fail-closed). See the `rubric_review` job log.")
    sys.exit(0)

status = "❌ FAIL" if fails else "✅ PASS"
L = [f"## 🧪 Implementation Rubric Review — {status}", ""]
if fails:
    L.append(f"**Verdict:** FAIL — {len(fails)} criteri{'on' if len(fails) == 1 else 'a'} to fix "
             f"({passes}/{len(names)} pass, {len(nas)} N/A).")
else:
    L.append(f"**Verdict:** PASS — {passes}/{len(names)} criteria pass"
             + (f" ({len(nas)} N/A)." if nas else "."))

# Compact scan table — result per criterion; every FULL note follows below, untruncated.
L += ["", "### Criteria", "| # | Criterion | Result |", "|---|---|---|"]
for i, k in enumerate(names, 1):
    L.append(f"| {i} | `{k}` | {BADGE.get(norm(crit[k].get('outcome')), norm(crit[k].get('outcome')))} |")

# Failures first, in full, with fix advice.
if fails:
    L += ["", "### ❌ Must fix"]
    for k in fails:
        L.append(f"- **`{k}`** — {full(k)}")

# Every remaining criterion, full note — nothing truncated.
rest = [k for k in names if norm(crit[k].get("outcome")) != "fail"]
if rest:
    L += ["", "### Notes — all other criteria"]
    for k in rest:
        L.append(f"- **`{k}`** ({BADGE.get(norm(crit[k].get('outcome')))}) — {full(k)}")

L += ["",
      "<sub>Automated read-only review against the TB3 implementation rubric (`harbor check`, Opus). "
      + ("Address the ❌ criteria above, then `git commit` and `git push` to re-run — all criteria must "
         "pass to be accepted." if fails else "A maintainer reviews after checks pass.") + "</sub>"]

body = "\n".join(L)
if len(body) > GH_LIMIT:
    note = ("\n\n> ⚠️ Trimmed to fit GitHub's comment size limit — see the `rubric_review` job log "
            "for the full rubric.")
    cut = body.rfind("\n| ", 0, GH_LIMIT - len(note))
    body = (body[:cut] if cut > 0 else body[:GH_LIMIT - len(note)]) + note
print(body)
