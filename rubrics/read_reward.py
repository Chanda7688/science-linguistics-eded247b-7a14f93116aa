#!/usr/bin/env python3
"""Print the max reward recorded in harbor result.json files under a directory (empty if none).

    read_reward.py <harbor-output-dir>

Reads the structured reward from `stats.evals.*.reward_stats.reward` (keys are the reward values)
with a `metrics[].mean` fallback — robust, unlike grepping harbor's verbose stdout for "reward.*1.0"
(which false-matches a `1.0` elsewhere on a reward line).
"""
import json
import glob
import sys

vals = []
root = sys.argv[1] if len(sys.argv) > 1 else "."
for f in glob.glob(root + "/**/result.json", recursive=True):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    evals = ((d.get("stats") or {}).get("evals") or {})
    for ev in evals.values():
        for v in (ev.get("reward_stats", {}).get("reward", {}) or {}):
            try:
                vals.append(float(v))
            except Exception:
                pass
        for m in (ev.get("metrics") or []):
            if isinstance(m, dict) and "mean" in m:
                try:
                    vals.append(float(m["mean"]))
                except Exception:
                    pass
print(max(vals) if vals else "")
