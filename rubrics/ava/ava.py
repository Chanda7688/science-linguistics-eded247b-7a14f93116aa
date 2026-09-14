#!/usr/bin/env python3
"""Top-level entrypoint for the Adversarial Verifier Audit (CF flat-script convention).

    python3 ava.py audit <task_dir> --out audit.json
    python3 ava.py batch --in gt_phase14_targets.jsonl --out ava_out/

Delegates to adversarial_verifier_audit.cli. Bills $ANTHROPIC_API_KEY
(the same key the other checks use; aborts if unset).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adversarial_verifier_audit.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
