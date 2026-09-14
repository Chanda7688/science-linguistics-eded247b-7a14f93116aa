#!/usr/bin/env python3
"""Fail-closed gate on harbor 0.18 check verdicts (check-result.json flat shape). Exit 1 on any FAIL / none."""
import sys, glob, json
jobs = sys.argv[1]; crit = {}
def load(p):
    try: return json.load(open(p))
    except Exception: return None
for f in glob.glob(f"{jobs}/**/artifacts/check-result.json", recursive=True) + glob.glob(f"{jobs}/**/check-result.json", recursive=True):
    d = load(f)
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict) and "outcome" in v: crit[k] = v
if not crit:
    print("::error::rubric produced no verdicts (fail-closed)"); sys.exit(1)
fails = [k for k, v in crit.items() if str(v.get("outcome", "")).lower() == "fail"]
if fails: print("::error::Implementation rubric FAILs:", ", ".join(fails)); sys.exit(1)
print(f"Implementation rubric PASS ({len(crit)} criteria)")
