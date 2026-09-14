#!/usr/bin/env python3
"""gate.py -- pure union routing for the scaled (unlabeled) production gate.

Combines the two evaluators into ONE per-task routing decision + the list of
issues to surface in a PR comment:

  * AVA  : the blind materiality-gated audit report ``<task_id>.json`` written by
           ``ava.py batch --config blind_ava_only``. Its ``audit_result.routing``
           is already the LIVE gated decision; we recompute it via
           ``materiality.recalibrated_route`` (guaranteed identical to the live
           path per that module's docstring) so we also get, per candidate, which
           issues are ``blocking_eligible`` / ``routes_to_review``.
  * Alan : one task dict from the deep-review harness ``report.json`` (``tasks[]``),
           carrying ``harness_verdict`` (FAIL/PASS), ``blocking_issues`` (list of
           strings), and ``client_criteria_flagged``.

UNION ROUTING (no client labels involved -- this is a production gate, not a
recall eval). Binary block/pass:
    block  if AVA == block (a Major blocking_eligible finding, or a fail-closed
           parse/truncation safety condition) OR Alan == FAIL
    pass   otherwise

Minor findings (routes_to_review) NO LONGER block -- they are surfaced as
non-blocking advisories (``ava_advisory``). Only gate-authoritative issues are
surfaced (never speculative/discarded candidates), so a rendered comment can
never over-state the block decision.

Pure stdlib + the in-repo ``adversarial_verifier_audit`` package. No model calls.
"""
from __future__ import annotations

import os
import sys

# make check-fix importable so we can reuse the exact materiality gate.
# The bundle may live either INSIDE check-fix (check-fix/tools/scaled_eval) or in a
# sibling repo (e.g. handshake-orchestration-tb2/tools/scaled_eval); resolve the
# check-fix repo robustly, honoring an explicit AVA_REPO override.
def _find_checkfix():
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(os.path.dirname(here))   # .../<repo>
    project = os.path.dirname(repo_root)                  # project root
    candidates = [
        here,                                             # VENDORED: adversarial_verifier_audit ships in the bundle
        os.environ.get("AVA_REPO", ""),                   # explicit override
        repo_root,                                        # bundle lives inside check-fix
        os.path.join(project, "check-fix"),               # sibling check-fix repo (dev only)
    ]
    for c in candidates:
        if c and os.path.isdir(os.path.join(c, "adversarial_verifier_audit")):
            return c
    # fall back to the sibling guess so the ImportError names a sensible path
    return os.path.join(project, "check-fix")


_CHECKFIX = _find_checkfix()
if _CHECKFIX not in sys.path:
    sys.path.insert(0, _CHECKFIX)

from adversarial_verifier_audit import materiality as M  # noqa: E402

BLOCK = "block"
REVIEW = "human_review"
PASS = "pass"


def _trim(s, n=240):
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "\u2026"


# ---- AVA side ---------------------------------------------------------------
def ava_routing(ava_rep):
    """AVA's gated routing: block | human_review | static_pass. Falls back to the
    report's own ``audit_result.routing`` if the materiality recompute fails."""
    if not ava_rep:
        return None
    try:
        return M.recalibrated_route(ava_rep)["routing"]
    except Exception:
        return (ava_rep.get("audit_result") or {}).get("routing")


def _finding(issue, severity):
    """Structured finding for the renderer: enough to state WHAT the check verifies
    and HOW the task failed it (criterion via failure_mode, expected-vs-actual from
    the witness, and one evidence pointer). The literal adversarial submission
    (``witness.submission_or_condition``) is intentionally NOT surfaced, so the
    comment is diagnostic rather than a copy-paste recipe."""
    w = issue.get("witness") or {}
    ev = issue.get("evidence") or []
    return {
        "severity": severity,
        "text": _trim(issue.get("claim") or issue.get("failure_mode") or ""),
        "failure_mode": issue.get("failure_mode") or w.get("type") or "",
        "witness_type": w.get("type") or "",
        "expected": _trim(w.get("expected_contract_result"), 200),
        "predicted": _trim(w.get("predicted_verifier_result"), 200),
        "evidence": [{"file": e.get("file", ""), "location": e.get("location", ""),
                      "detail": _trim(e.get("detail"), 180)} for e in ev[:1]],
    }


def _ava_findings(ava_rep):
    """Classify an AVA report once and split its findings into (major, minor) lists.
    Major = blocking_eligible (drives a block); Minor = routes_to_review (non-blocking
    advisory). Each finding is a structured dict (see ``_finding``)."""
    if not ava_rep:
        return [], []
    issues = ava_rep.get("candidate_issues") or []
    try:
        classes = M.classify_report(ava_rep)
    except Exception:
        classes = []
    major, minor = [], []
    for issue, cls in zip(issues, classes):
        if cls.get("blocking_eligible"):
            major.append(_finding(issue, "Major"))
        elif cls.get("routes_to_review"):
            minor.append(_finding(issue, "Minor"))
    return major, minor


def ava_issues(ava_rep, routing=None):
    """Major (blocking_eligible) AVA findings that DRIVE a block."""
    return _ava_findings(ava_rep)[0]


def ava_advisory_issues(ava_rep):
    """Minor (routes_to_review) AVA findings -- non-blocking advisories, surfaced
    regardless of routing."""
    return _ava_findings(ava_rep)[1]


# ---- Alan side --------------------------------------------------------------
def alan_verdict(alan_task):
    if not alan_task:
        return None
    return (alan_task.get("harness_verdict") or "").strip().upper() or None


def alan_issues(alan_task):
    """Alan's blocking issues (list of free-text strings) + the criteria he flagged.
    Only meaningful when the panel FAILed the task."""
    if not alan_task or alan_verdict(alan_task) != "FAIL":
        return [], []
    blocking = [_trim(b) for b in (alan_task.get("blocking_issues") or []) if isinstance(b, str) and b.strip()]
    crits = [c for c in (alan_task.get("client_criteria_flagged") or []) if isinstance(c, str) and c.strip()]
    return blocking, crits


# ---- union ------------------------------------------------------------------
def union_route(alan_task, ava_rep):
    """Return the combined routing: block | pass (binary policy).
    AVA blocks only on a Major finding or a fail-closed safety condition (Minor
    findings are advisory), so the union is: block if AVA blocks OR Alan FAILs;
    else pass."""
    a = ava_routing(ava_rep)
    al = alan_verdict(alan_task)
    if a == BLOCK or al == "FAIL":
        return BLOCK
    return PASS


def decision(alan_task, ava_rep):
    """Full per-task decision bundle used by the renderer.

    Returns dict: routing, ava_routing, alan_verdict, ava_issues, alan_blocking,
    alan_criteria, sources (which systems flagged).
    """
    a = ava_routing(ava_rep)
    al = alan_verdict(alan_task)
    routing = union_route(alan_task, ava_rep)
    major, minor = _ava_findings(ava_rep)
    al_blocking, al_crits = alan_issues(alan_task)
    sources = []
    if a == BLOCK:
        sources.append("AVA")
    if al == "FAIL":
        sources.append("Alan")
    return {
        "routing": routing,
        "ava_routing": a,
        "alan_verdict": al,
        "ava_issues": major,
        "ava_advisory": minor,
        "alan_blocking": al_blocking if routing == BLOCK else [],
        "alan_criteria": al_crits if routing == BLOCK else [],
        "sources": sources,
    }
