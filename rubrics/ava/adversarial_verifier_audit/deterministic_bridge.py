#!/usr/bin/env python3
"""Bridge the existing deterministic scanner into AVA candidates.

Consumes ``deterministic_checks.scan_task`` (the phase14 static scanner) and emits
``CandidateIssue`` records with ``source_passes=["deterministic"]`` and
``provenance="deterministic_scanner"`` -- the one provenance that the adjudicator
promotes to ``confirmed`` without execution (a greppable/AST pattern firing is a
concrete, re-checkable fact).

Import is done lazily/defensively so AVA still works if the scanner is absent
(the bridge just yields nothing).
"""
from __future__ import annotations

import importlib
import os
import sys

from .schemas import CandidateIssue, Witness

# map each deterministic pattern to an AVA witness type + severity
_PATTERN_META = {
    "G1": ("leaked_answer", "major"),
    "G2": ("expected_output_modification", "major"),
    "G3": ("writable_trusted_file", "major"),
    "C_NAN": ("nan_inf", "major"),
    "C_COERCE": ("numeric_coercion", "minor"),
    "C_SCHEMA": ("missing_or_extra_fields", "minor"),
    "R1_IMPORT": ("leaked_answer", "major"),
    "R1_HARDCODE": ("hardcoded_output", "minor"),
    "R1_DEFAULT": ("hardcoded_output", "minor"),
    "D_CONTRA": ("unverified_requirement", "minor"),
    "D_PRECOND": ("valid_but_rejected", "major"),
    "D_UNDERSPEC": ("unverified_requirement", "minor"),
}


def _load_scanner():
    """Import deterministic_checks from the CF dir (parent of this package)."""
    cf_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if cf_dir not in sys.path:
        sys.path.insert(0, cf_dir)
    try:
        return importlib.import_module("deterministic_checks")
    except Exception:
        return None


def scan(task_dir: str) -> list[CandidateIssue]:
    mod = _load_scanner()
    if mod is None or not hasattr(mod, "scan_task"):
        return []
    try:
        flags = mod.scan_task(task_dir)
    except Exception:
        return []
    cat = getattr(mod, "CAT", {})
    out: list[CandidateIssue] = []
    idx = 0
    for pat, (fired, evidence) in flags.items():
        if not fired:
            continue
        wtype, sev = _PATTERN_META.get(pat, ("other", "potential_major"))
        idx += 1
        out.append(CandidateIssue(
            issue_id=f"D{idx}",
            source_passes=["deterministic"],
            status="potential",             # adjudication will promote to confirmed
            severity=sev,
            provenance="deterministic_scanner",
            claimed_provenance="deterministic_scanner",
            claim=f"[{pat}] {cat.get(pat, pat)}: {evidence}",
            evidence=[{"file": "tests/ or solution/", "location": pat, "detail": evidence}],
            failure_mode=pat,
            witness=Witness(type=wtype, submission_or_condition=evidence,
                            execution_plan=f"deterministic pattern {pat} fired (static, re-checkable)"),
        ))
    return out
