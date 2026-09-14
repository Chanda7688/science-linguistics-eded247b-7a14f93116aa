#!/usr/bin/env python3
"""Harvest LLM cost from harbor 0.18 run/analyze output into claude_costs records.

    harvest.py --stage <name> --cost-dir <dir> --kind trials|analyze|check [--model M] ROOT [ROOT...]

harbor 0.18 exposes cost differently from the tb2-era combined `trials:[]` file, so the vendored
claude_costs harbor extractors don't match. This 0.18-aware harvester reads the real locations:
  - kind=trials : per-trial result.json -> agent_result.cost_usd (+ n_input/n_output/n_cache tokens)
  - kind=analyze: per-trial analysis.json -> estimated_cost_usd
  - kind=check  : any *.json under a `harbor check` jobs dir carrying estimated_cost_usd
It emits one claude_costs record per session via claude_costs.write_record, so the standard
`claude_costs.py aggregate` rolls trials + analyze + claude-CLI (deep review, AVA) into one summary.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ava"))
import claude_costs as cc  # noqa: E402


def _rglob(roots, name):
    import pathlib
    for r in roots:
        for p in pathlib.Path(r).rglob(name):
            if p.is_file():
                yield p


def _load(p):
    import json
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def harvest_trials(roots, stage, cost_dir, model):
    n = 0
    for p in _rglob(roots, "result.json"):
        d = _load(p)
        ar = d.get("agent_result") if isinstance(d, dict) else None
        if not isinstance(ar, dict) or "cost_usd" not in ar:  # top-level result.json has no agent_result
            continue
        usage = {
            "input_tokens": ar.get("n_input_tokens"),
            "output_tokens": ar.get("n_output_tokens"),
            "cache_read_input_tokens": ar.get("n_cache_tokens"),
        }
        cc.write_record(cost_dir, cc.make_record(
            source="harbor_trial", stage=stage, model=model,
            estimated_cost_usd=ar.get("cost_usd"),
            status="success" if ar.get("cost_usd") is not None else "cost_unavailable",
            usage=usage, environ=os.environ,
        ))
        n += 1
    return n


def harvest_analyze(roots, stage, cost_dir, model):
    n = 0
    for p in _rglob(roots, "analysis.json"):
        d = _load(p)
        if not isinstance(d, dict) or "estimated_cost_usd" not in d:
            continue
        cc.write_record(cost_dir, cc.make_record(
            source="harbor_analyze", stage=stage, model=model,
            estimated_cost_usd=d.get("estimated_cost_usd"),
            status="success" if d.get("estimated_cost_usd") is not None else "cost_unavailable",
            environ=os.environ,
        ))
        n += 1
    return n


def harvest_check(roots, stage, cost_dir, model):
    # `harbor check` output shape isn't pinned; capture any JSON carrying estimated_cost_usd
    # (or a nested final_metrics.total_cost_usd). If none is found, record cost_unavailable so
    # the stage is visibly accounted for rather than silently dropped.
    n = 0
    for p in _rglob(roots, "*.json"):
        d = _load(p)
        if not isinstance(d, dict):
            continue
        cost = d.get("estimated_cost_usd")
        if cost is None and isinstance(d.get("final_metrics"), dict):
            cost = d["final_metrics"].get("total_cost_usd")
        if cost is None:
            continue
        cc.write_record(cost_dir, cc.make_record(
            source="harbor_check", stage=stage, model=model,
            estimated_cost_usd=cost, environ=os.environ,
        ))
        n += 1
    if n == 0:
        cc.write_record(cost_dir, cc.make_record(
            source="harbor_check", stage=stage, model=model,
            status="cost_unavailable", environ=os.environ,
        ))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--cost-dir", required=True)
    ap.add_argument("--kind", required=True, choices=["trials", "analyze", "check"])
    ap.add_argument("--model", default="")
    ap.add_argument("roots", nargs="+")
    a = ap.parse_args()
    fn = {"trials": harvest_trials, "analyze": harvest_analyze, "check": harvest_check}[a.kind]
    n = fn(a.roots, a.stage, a.cost_dir, a.model or None)
    print(f"harvested {n} {a.kind} record(s) for stage={a.stage} into {a.cost_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
