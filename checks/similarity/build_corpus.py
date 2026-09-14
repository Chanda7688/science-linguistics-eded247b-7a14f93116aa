#!/usr/bin/env python3
"""Build the TB2/TB3 duplicate-detection corpus (tb_corpus.jsonl).

Admin tool — run when the benchmarks change to refresh the baked corpus that the
similarity check compares fellow submissions against. It scans one or more roots
for Terminal-Bench tasks (a directory containing an `instruction.md`) and writes
one JSON line per task: {"source", "task", "instruction"}.

Usage:
    python3 build_corpus.py \
        --root TB2:/path/to/terminal-bench-2 \
        --root TB3:/path/to/terminal-bench-3/tasks \
        --out tb_corpus.jsonl

Each --root is LABEL:DIR. A task is any subdirectory (recursively, up to depth 2)
that directly contains an instruction.md.
"""
import argparse
import json
import os
import sys


def find_tasks(root):
    """Yield (task_name, instruction_path) for dirs containing instruction.md."""
    for dirpath, dirnames, filenames in os.walk(root):
        # don't descend into task internals (environment/solution/tests/.git)
        depth = os.path.relpath(dirpath, root).count(os.sep)
        if depth > 2:
            dirnames[:] = []
            continue
        if "instruction.md" in filenames:
            yield os.path.basename(dirpath), os.path.join(dirpath, "instruction.md")
            dirnames[:] = []  # a task dir has no nested tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", required=True,
                    help="LABEL:DIR (e.g. TB2:/path/to/terminal-bench-2)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows, seen = [], set()
    for spec in args.root:
        if ":" not in spec:
            sys.exit(f"--root must be LABEL:DIR, got {spec!r}")
        label, _, root = spec.partition(":")
        root = os.path.expanduser(root)
        if not os.path.isdir(root):
            sys.exit(f"not a directory: {root}")
        n = 0
        for task, path in sorted(find_tasks(root)):
            key = (label, task)
            if key in seen:
                continue
            seen.add(key)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
            if not text:
                continue
            rows.append({"source": label, "task": task, "instruction": text})
            n += 1
        print(f"  {label}: {n} tasks from {root}", file=sys.stderr)

    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} tasks -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
