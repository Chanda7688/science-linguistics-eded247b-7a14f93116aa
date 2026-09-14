#!/usr/bin/env python3
"""Adjudication: status provenance, refutation validation, and routing.

Everything here is COMPUTED IN CODE. The LLM proposes claims, witnesses, a
provenance label, and (optionally) a refutation; it never gets to set the final
status/severity or the routing decision.

Status ladder (the precise definition of what may become ``confirmed`` in v1):

    executed_exploit        -> confirmed   (NOT reachable in read-only v1)
    deterministic_scanner   -> confirmed   (only if a deterministic pass sourced it)
    literal_contradiction   -> confirmed   (only with >=2 cited paths spanning
                                            instruction AND verifier/solution)
    fully_traced_static     -> supported   (a complete static exploit path)
    runtime_dependent       -> potential   (hinges on an unproven runtime assumption)
    speculative / other     -> potential   (default cap)

A model that merely *labels* its finding ``literal_contradiction`` or
``deterministic_scanner`` without meeting the coded precondition is DOWNGRADED to
``supported``/``potential`` and the unmet claim is recorded in ``provenance_downgraded``.
"""
from __future__ import annotations

from . import materiality
from .schemas import AuditResult, CandidateIssue, CoverageGap

# soft rationales that are explicitly NOT valid refutations (spec)
DISALLOWED_REFUTATION_HINTS = (
    "could infer", "competent agent", "obvious", "reference solution", "reference handles",
    "workaround", "unlikely", "seems reasonable", "another pass", "didn't flag", "did not flag",
)

_INSTRUCTION_MARKERS = ("instruction", "task.toml", "readme", "spec")
_VERIFIER_MARKERS = ("test", "verifier", "solution", "grade", "check", "/tests", "/solution")


def _is_instruction_file(f: str) -> bool:
    f = (f or "").lower()
    return any(m in f for m in _INSTRUCTION_MARKERS)


def _is_verifier_file(f: str) -> bool:
    f = (f or "").lower()
    return any(m in f for m in _VERIFIER_MARKERS)


def _is_fully_traced(c: CandidateIssue) -> bool:
    """Section 9: a `fully_traced_static` path must carry cited evidence AND a
    concrete witness (submission/condition + predicted verifier result). If any
    required component is absent, the claim is downgraded to `potential`."""
    has_cite = any(e.get("file") and e.get("detail") for e in c.evidence)
    w = c.witness
    return bool(has_cite and w.submission_or_condition and w.predicted_verifier_result)


def _has_cross_cited_contradiction(c: CandidateIssue) -> bool:
    """True iff the evidence cites >=2 concrete paths that span BOTH an
    instruction-side file and a verifier/solution-side file (a literal
    instruction<->verifier contradiction with cited paths). This is the sole
    LLM-only route to ``confirmed``."""
    cited = [e for e in c.evidence if e.get("file") and e.get("detail")]
    if len(cited) < 2:
        return False
    files = [e["file"] for e in cited]
    return any(_is_instruction_file(f) for f in files) and any(_is_verifier_file(f) for f in files)


def compute_status(c: CandidateIssue) -> CandidateIssue:
    """Assign status from provenance, verifying each provenance's precondition in
    code. Mutates and returns the candidate. Does not touch refuted/duplicate."""
    if c.status in ("refuted", "duplicate"):
        return c
    claimed = c.provenance
    downgrade_reason = ""

    if claimed == "executed_exploit":
        # v1 never executes, so an unbacked "executed" claim cannot stand.
        status, prov = "potential", "speculative"
        downgrade_reason = "executed_exploit claimed but v1 does not execute attacks"
    elif claimed == "deterministic_scanner":
        if "deterministic" in c.source_passes:
            status, prov = "confirmed", claimed
        else:
            status, prov = "supported", "fully_traced_static"
            downgrade_reason = "deterministic_scanner claimed without a deterministic source pass"
    elif claimed == "literal_contradiction":
        if _has_cross_cited_contradiction(c):
            status, prov = "confirmed", claimed
        else:
            status, prov = "supported", "fully_traced_static"
            downgrade_reason = "literal_contradiction lacks >=2 cross-cited (instruction+verifier) paths"
    elif claimed == "fully_traced_static":
        if _is_fully_traced(c):
            status, prov = "supported", claimed
        else:
            status, prov = "potential", "runtime_dependent"
            downgrade_reason = "fully_traced_static claimed but missing cited evidence/witness components"
    elif claimed == "runtime_dependent":
        status, prov = "potential", claimed
    else:  # speculative / unknown
        status, prov = "potential", "speculative"

    # Section 10: a valid-but-rejected alternative is only `supported` when the
    # contract AFFIRMATIVELY permits it (a literal, cited allowance). Otherwise
    # "the instruction doesn't forbid it" is not enough -> cap at `potential`.
    if c.witness.type == "valid_but_rejected" and status == "supported" and prov != "literal_contradiction":
        status, prov = "potential", "runtime_dependent"
        downgrade_reason = ((downgrade_reason + "; ") if downgrade_reason else "") + \
            "valid_but_rejected needs affirmative contract permission (instruction/spec/standard); capped to potential"

    c.provenance = prov
    c.status = status
    # keep severity coherent: a non-major severity is preserved; potential_major stays
    if status in ("confirmed", "supported") and c.severity == "potential_major":
        c.severity = "major"
    if status == "potential" and c.severity == "major":
        c.severity = "potential_major"
    if downgrade_reason:
        c.assumptions = list(c.assumptions) + [f"[provenance_downgraded] {downgrade_reason}"]
    return c


def validate_refutation(c: CandidateIssue) -> tuple[bool, str]:
    """Return (accepted, reason). A refutation is accepted only if it parsed into a
    typed ``Refutation`` (allowed type + cited file + detail) AND the detail does not
    lean on a disallowed soft rationale."""
    r = c.refutation
    if r is None:
        return False, "no structured refutation (missing type or cited contrary evidence)"
    blob = f"{r.contrary_evidence.get('detail','')}".lower()
    for hint in DISALLOWED_REFUTATION_HINTS:
        if hint in blob:
            return False, f"soft rationale not a valid refutation: '{hint}'"
    return True, r.refutation_type


def apply_refutations(candidates: list[CandidateIssue]) -> list[CandidateIssue]:
    """Set status=refuted ONLY for candidates with a valid typed refutation.
    Everything else keeps its provenance-computed status (fail-closed)."""
    for c in candidates:
        if c.refutation is not None:
            ok, _ = validate_refutation(c)
            if ok:
                c.status = "refuted"
            else:
                # keep the candidate live; drop the invalid refutation so it can't mislead
                c.refutation = None
    return candidates


def _is_major(c: CandidateIssue) -> bool:
    return c.severity in ("major", "potential_major")


def route(candidates: list[CandidateIssue], gaps: list[CoverageGap],
          parse_failures: int, taxonomy_complete: bool) -> AuditResult:
    """The v1 fail-closed decision policy, computed entirely in code."""
    live = [c for c in candidates if c.status not in ("refuted", "duplicate")]
    confirmed_major = sum(1 for c in live if c.status == "confirmed" and _is_major(c))
    supported_major = sum(1 for c in live if c.status == "supported" and _is_major(c))
    potential_major = sum(1 for c in live if c.status == "potential" and c.severity in ("major", "potential_major"))
    minor = sum(1 for c in live if c.severity == "minor")
    material_gaps = sum(1 for g in gaps if g.material)

    res = AuditResult(
        confirmed_major_count=confirmed_major,
        supported_major_count=supported_major,
        potential_major_count=potential_major,
        minor_count=minor,
        material_coverage_gaps=material_gaps,
        material_parse_failures=parse_failures,
        taxonomy_coverage_complete=bool(taxonomy_complete),
        execution_validation_complete=False,   # invariant in read-only v1
    )
    if confirmed_major > 0:
        res.routing, res.reason = "block", f"{confirmed_major} confirmed Major issue(s)"
    elif supported_major > 0:
        res.routing, res.reason = "block", f"{supported_major} supported Major issue(s) (fully-traced static exploit)"
    elif potential_major > 0:
        res.routing, res.reason = "human_review", f"{potential_major} potential Major issue(s) need runtime confirmation"
    elif material_gaps > 0:
        res.routing, res.reason = "human_review", f"{material_gaps} material coverage gap(s) unresolved"
    elif parse_failures > 0:
        res.routing, res.reason = "human_review", f"{parse_failures} material finding(s) could not be parsed (fail-closed)"
    else:
        res.routing, res.reason = "static_pass", "no confirmed/supported/potential Major, no material gaps or parse failures"

    res.unresolved_material_gaps = (material_gaps > 0) or (parse_failures > 0)
    # static_audit_passed is NOT "safe to accept": it only means the read-only
    # static audit found nothing blocking and left no material gaps. Real
    # acceptance requires execution_validation_complete (deferred).
    res.static_audit_passed = (res.routing == "static_pass")
    return res


def route_with_materiality(candidates: list[CandidateIssue], gaps: list[CoverageGap],
                           parse_failures: int, taxonomy_complete: bool,
                           golden_conformance: list) -> AuditResult:
    """THE live routing entry point (called by ``audit.py``). Computes the same
    diagnostic counts/fields as ``route()`` (confirmed/supported/potential counts,
    coverage-gap counts, taxonomy completeness), but the FINAL ``routing``/``reason``
    come from the deterministic materiality + ownership gate
    (``materiality.route_candidates``), not from the old "any supported Major blocks"
    policy. Terminal routes remain exactly {block, human_review, static_pass}; no
    ``safe_to_accept`` is introduced. Both the OLD (pre-materiality) and FINAL routing
    are exposed on the result for debugging/comparison.

    Routing is computed ONLY from: blocking-eligible candidates, review-eligible
    candidates, parse failures, and completeness caps (materiality.route_from_rows) --
    exactly mirroring the offline recalibration path 1:1 (see
    ``tools/tests/test_live_routing_equivalence.py``).
    """
    res = route(candidates, gaps, parse_failures, taxonomy_complete)
    pre_routing, pre_reason = res.routing, res.reason

    live = materiality.route_candidates(candidates, golden_conformance, parse_failures)

    res.routing = live["routing"]
    res.reason = live["reason"]
    res.materiality_applied = True
    res.pre_materiality_routing = pre_routing
    res.pre_materiality_reason = pre_reason
    res.blocking_eligible_ids = live["blocking_eligible_ids"]
    res.review_ids = live["review_ids"]
    res.deep_review_ids = live["deep_review_ids"]
    res.advisory_ids = live["advisory_ids"]
    res.downgraded_ids = live["downgraded_ids"]
    res.static_audit_passed = (res.routing == "static_pass")
    return res
