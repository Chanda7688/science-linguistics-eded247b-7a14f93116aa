#!/usr/bin/env python3
"""AVA command-line interface.

  # audit ONE task -> JSON (bills $ANTHROPIC_API_KEY, the same key as the other checks):
  python3 -m adversarial_verifier_audit.cli audit <task_dir> --out audit.json

  # batch over a jsonl manifest (task_id + local_dir), same format as the harness:
  python3 -m adversarial_verifier_audit.cli batch --in gt_phase14_targets.jsonl --out ava_out/
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import threading

from .audit import audit_task
from .llm import DEFAULT_MODEL, ClaudeCLI


def _include_deterministic(a) -> bool:
    """blind_ava_only -> no deterministic seeds; ava_with_deterministic_seeds -> include.
    Falls back to the legacy --no-deterministic flag when --config is unset."""
    cfg = getattr(a, "config", "") or ""
    if cfg == "blind_ava_only":
        return False
    if cfg == "ava_with_deterministic_seeds":
        return True
    return not a.no_deterministic


def cmd_audit(a):
    llm = ClaudeCLI(model=a.model, env_var=a.api_key_env)
    print(f"billing: team key via ${a.api_key_env} (bare-mode; subscription NOT used)", flush=True)
    report = audit_task(a.task_dir, llm, task_id=a.task_id, model=a.model,
                        include_deterministic=_include_deterministic(a),
                        parallel=not a.serial, max_inflight=a.max_inflight)
    out = report.to_json()
    if a.out:
        open(a.out, "w", encoding="utf-8").write(out)
        r = report.audit_result
        print(f"wrote {a.out}  routing={r.routing}  "
              f"confirmed_major={r.confirmed_major_count} supported_major={r.supported_major_count} "
              f"potential_major={r.potential_major_count} gaps={r.material_coverage_gaps} "
              f"parse_failures={r.material_parse_failures}")
    else:
        print(out)


def cmd_batch(a):
    llm = ClaudeCLI(model=a.model, env_var=a.api_key_env)
    print(f"billing: team key via ${a.api_key_env} (bare-mode; subscription NOT used)", flush=True)
    os.makedirs(a.out, exist_ok=True)
    tasks = [json.loads(l) for l in open(a.inp) if l.strip()]
    if a.limit:
        tasks = tasks[:a.limit]
    # ONE shared in-flight cap across the whole batch: bounds total concurrent LLM
    # calls to --max-inflight regardless of --workers x (<=3 intra-task passes).
    sem = threading.Semaphore(a.max_inflight)
    wlock = threading.Lock()

    def do(t):
        tid = t.get("task_id", "?")
        td = t.get("local_dir")
        if not td or not os.path.isdir(td):
            print(f"[{tid[:8]}] SKIP missing local_dir", flush=True)
            return {"task_id": tid, "routing": "skipped", "reason": "missing local_dir"}
        report = audit_task(td, llm, task_id=tid, model=a.model,
                            include_deterministic=_include_deterministic(a),
                            parallel=not a.serial, sem=sem)
        with wlock:
            # atomic publication: write to a temp path then os.replace so an
            # interrupted run never leaves a partial/corrupt {tid}.json (resume-safe).
            final = os.path.join(a.out, f"{tid}.json")
            tmp = final + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(report.to_json())
            os.replace(tmp, final)
        r = report.audit_result
        print(f"[{tid[:8]}] => {r.routing} ({r.reason})", flush=True)
        return {"task_id": tid, "routing": r.routing,
                "confirmed_major": r.confirmed_major_count,
                "supported_major": r.supported_major_count,
                "potential_major": r.potential_major_count}

    workers = max(1, a.workers)
    if workers == 1:
        summary = [do(t) for t in tasks]
    else:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            summary = list(ex.map(do, tasks))
    open(os.path.join(a.out, "_summary.json"), "w", encoding="utf-8").write(json.dumps(summary, indent=2))
    print(f"\nSaved {len(summary)} reports to {a.out}/")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Adversarial Verifier Audit (read-only v1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    au = sub.add_parser("audit", help="audit a single task directory")
    au.add_argument("task_dir")
    au.add_argument("--out", default="")
    au.add_argument("--task-id", dest="task_id", default="")
    au.add_argument("--model", default=DEFAULT_MODEL)
    au.add_argument("--no-deterministic", dest="no_deterministic", action="store_true")
    au.add_argument("--config", choices=["blind_ava_only", "ava_with_deterministic_seeds"], default="")
    au.add_argument("--serial", action="store_true", help="run the 4 passes serially (default runs the 3 independent passes in parallel)")
    au.add_argument("--max-inflight", dest="max_inflight", type=int, default=3, help="cap on concurrent LLM calls")
    au.add_argument("--api-key-env", dest="api_key_env", default="ANTHROPIC_API_KEY")
    au.set_defaults(fn=cmd_audit)

    b = sub.add_parser("batch", help="audit every task in a jsonl manifest")
    b.add_argument("--in", dest="inp", required=True)
    b.add_argument("--out", default="ava_out")
    b.add_argument("--model", default=DEFAULT_MODEL)
    b.add_argument("--limit", type=int, default=0)
    b.add_argument("--no-deterministic", dest="no_deterministic", action="store_true")
    b.add_argument("--config", choices=["blind_ava_only", "ava_with_deterministic_seeds"], default="")
    b.add_argument("--workers", type=int, default=1, help="number of tasks to audit concurrently")
    b.add_argument("--max-inflight", dest="max_inflight", type=int, default=4,
                   help="global cap on concurrent LLM calls across the whole batch (prevents thrash)")
    b.add_argument("--serial", action="store_true", help="run each task's 4 passes serially")
    b.add_argument("--api-key-env", dest="api_key_env", default="ANTHROPIC_API_KEY")
    b.set_defaults(fn=cmd_batch)

    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
