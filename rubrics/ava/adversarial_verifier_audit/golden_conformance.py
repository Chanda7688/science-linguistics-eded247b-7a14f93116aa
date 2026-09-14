#!/usr/bin/env python3
"""Pass 4 -- Golden Solution Conformance (v1.2 hardened).

Compares instruction *semantics* against the golden solution's actual operations,
per material requirement. Statuses: conformant / nonconformant / partially_conformant
/ not_implemented / cannot_determine.

Root-cause note (the invalid semantic-equivalence decision this pass now guards):
the earlier validator only checked that a counterfactual *string* was present. A
model could still mark an operation-sensitive requirement `conformant` by (a) picking
a NON-DISTINGUISHING counterfactual -- one whose required and actual outcomes coincide
because the benign fixture/dependency cannot observe the difference -- and (b)
restating the shared code path as an "equivalence proof" (e.g. "Identity: the same
handler runs for A and B"). Executing the same function/path is NOT semantic
equivalence when the path receives different methods, modes, configuration values,
arguments, data sources, permissions, or state.

This validator now recomputes whether the counterfactual is distinguishing IN CODE,
rejects free-form shared-path "proofs", and requires an operation-sensitive
`conformant` verdict to be EARNED by a contract-equivalence rule or a genuine
universal proof. Otherwise it downgrades: `nonconformant` (a code-synthesized
supported Major) when the trace shows a direct operation contradiction, else
`cannot_determine` plus a material coverage gap (fail-closed -> human_review).
Blocks are gated on an explicit traced mismatch so a clean control cannot be
force-blocked by fuzzy, model-authored outcome strings.

The model's labels are advisory only: status is validated in code (`_validate`) and
candidates are synthesized in code from nonconformant records.
"""
from __future__ import annotations

import dataclasses as dc
import re

from . import promptlib
from .llm import CallLLM
from .schemas import (GOLDEN_NONCONFORMANT, CandidateIssue, CoverageGap,
                      GoldenSolutionConformanceRecord, Witness,
                      call_llm_json)
from .task_loader import TaskPackage
from .verifier_mapper import common_fields

# a requirement is "operation-sensitive" (a specific causal operation matters, not
# just the final output) when it touches methods/protocols/order/state/deps/config
_OP_SENSITIVE = re.compile(
    r"\b(head|get|post|put|delete|method|upstream|origin|read|write|import|shadow|"
    r"mutat|tamper|order|sequence|before|after|override|default|env|environ|state|"
    r"side.?effect|filter|dependency|protocol|call|recompute|derive)\b", re.I)

# free-form phrases that are NOT a universal-equivalence proof: they assert a shared
# code path / current-fixture coincidence, not agreement over EVERY allowed environment.
_BOGUS_PROOF = re.compile(
    r"\b(identity"
    r"|same (pipeline|function|code ?path|path|route|framing|handler|method|relay|logic)"
    r"|shares? the same"
    r"|equivalent on( the)? current fixtures|current fixtures only|on current fixtures"
    r"|no divergence|cannot imagine|can'?t imagine|could ?n'?t imagine)\b", re.I)

# actual-branch outcome text signalling the two branches coincide (non-distinguishing)
_COINCIDE = re.compile(r"\b(match(es|ed)?|identical|coincid\w*|same as required|"
                       r"same result|no difference|unchanged|equivalent)\b", re.I)


def _operation_sensitive(rec: GoldenSolutionConformanceRecord) -> bool:
    if rec.operation_sensitive == "yes":
        return True
    if rec.operation_sensitive == "no":
        return False
    blob = " ".join([rec.required_operation, rec.required_behavior, rec.actual_operation,
                     rec.operation_boundary, rec.required_call_or_effect, rec.actual_call,
                     rec.downstream_dependency])
    return bool(_OP_SENSITIVE.search(blob))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _counterfactual_distinguishing(rec: GoldenSolutionConformanceRecord) -> bool:
    """True only when a counterfactual with BOTH outcomes was constructed and those
    outcomes genuinely differ. A counterfactual whose required and actual outcomes
    coincide (or whose actual branch says it 'matches') is NON-distinguishing and is
    not evidence of equivalence (spec Part B)."""
    req = _norm(rec.counterfactual_required_outcome)
    act = _norm(rec.counterfactual_actual_outcome)
    if not req or not act:
        return False
    if req == act:
        return False
    dou = (rec.difference_observable_under or "").lower()
    if any(w in dou for w in ("none", "coincide", "no divergence", "same outcome", "identical")):
        return False
    if _COINCIDE.search(rec.counterfactual_actual_outcome or ""):
        return False
    return True


def _valid_universal_proof(rec: GoldenSolutionConformanceRecord) -> bool:
    """A universal-equivalence proof must be substantive and must not be a shared-path /
    current-fixture restatement."""
    proof = (rec.universal_equivalence_proof or rec.equivalence_proof or "").strip()
    if len(proof) < 15:
        return False
    return not bool(_BOGUS_PROOF.search(proof))


def _direct_operation_contradiction(rec: GoldenSolutionConformanceRecord) -> bool:
    """A statically-established operation mismatch: the model's own trace says the actual
    operation does NOT match the required one (or declares them not equivalent), the
    differing value reaches a downstream dependency/boundary, and the contract does not
    declare the two interchangeable. Blocks are gated on this explicit signal so a clean
    control with fuzzy, model-authored outcome strings can never be force-blocked."""
    if not (rec.required_operation.strip() and rec.actual_operation.strip()):
        return False
    if rec.contract_equivalence_evidence.strip():
        return False
    traced_mismatch = (rec.actual_operation_matches_required == "no"
                       or rec.operation_semantically_equivalent == "no"
                       or rec.follows_semantics == "no")
    reaches = bool(rec.downstream_dependency.strip() or rec.operation_boundary.strip())
    return traced_mismatch and reaches


def _non_distinguishing_gap(rec: GoldenSolutionConformanceRecord) -> CoverageGap:
    return CoverageGap(
        area=f"golden_no_counterfactual:{rec.requirement_id}",
        reason_not_resolved="operation-sensitive requirement cleared without a distinguishing "
                            "counterfactual: the required and actual outcomes coincide (or rest on a "
                            "shared code path) and no contract rule or universal equivalence proof was given",
        risk_if_wrong="a method-sensitive or dependency-sensitive environment was never exercised, so an "
                      "upstream operation mismatch masked by a benign fixture would be undetected",
        recommended_probe=rec.minimal_discriminating_fixture
        or "construct a method-sensitive / dependency-sensitive environment in which the required and "
           "actual operations diverge, then re-run the real verifier",
        material=True)


def _validate(rec: GoldenSolutionConformanceRecord) -> tuple[list[str], "CoverageGap | None"]:
    """Enforce the equivalence rules in code; mutate rec.status; return (reasons, gap).

    The model's `conformant` label is only a proposal. For operation-sensitive
    requirements, `conformant` must be EARNED (a contract-equivalence rule or a genuine
    universal proof); a non-distinguishing counterfactual or a shared-code-path "proof"
    can never establish it.
    """
    reasons: list[str] = []
    gap: "CoverageGap | None" = None

    # code-owned distinguishing verdict, recorded for transparency (advisory field)
    if rec.counterfactual_required_outcome.strip() or rec.counterfactual_actual_outcome.strip():
        rec.counterfactual_is_distinguishing = "yes" if _counterfactual_distinguishing(rec) else "no"

    if rec.status != "conformant":
        return reasons, gap

    # 1) explicit self-contradiction: conformant but operations declared not equivalent
    if rec.operation_semantically_equivalent == "no":
        rec.status = "nonconformant"
        reasons.append("conformant asserted with operation_semantically_equivalent=no")
        return reasons, gap

    # 2) post-processing used to mask an upstream difference is not evidence of conformance
    if rec.postprocessing_used_to_mask_difference:
        rec.status = "cannot_determine"
        reasons.append("postprocessing masks an upstream difference; not evidence of conformance")
        return reasons, gap

    contract_ev = bool(rec.contract_equivalence_evidence.strip())
    valid_proof = _valid_universal_proof(rec)

    # 3) operation-sensitive conformance must be EARNED by a contract rule or a universal
    #    proof -- never by a non-distinguishing counterfactual or a shared-code-path claim.
    if _operation_sensitive(rec):
        if contract_ev or valid_proof:
            return reasons, gap                       # genuinely supported -> stays conformant
        if _direct_operation_contradiction(rec):
            rec.status = "nonconformant"
            reasons.append("direct operation contradiction: the traced actual operation does not match the "
                           "required operation and reaches a downstream dependency; equivalence unproven")
        else:
            rec.status = "cannot_determine"
            reasons.append("operation-sensitive equivalence rests on a non-distinguishing counterfactual or a "
                           "shared code path without a contract rule or universal proof")
            gap = _non_distinguishing_gap(rec)
        return reasons, gap

    # 4) non-operation-sensitive: preserve the prior lenient behavior
    if rec.equivalence_assumptions and not (rec.equivalence_proof.strip() or valid_proof or contract_ev):
        rec.status = "cannot_determine"
        reasons.append("unresolved equivalence_assumptions without an equivalence_proof")
    return reasons, gap


@dc.dataclass
class GoldenResult:
    records: list
    candidates: list
    parse_failures: int
    raw: str
    parsed: bool
    obj: dict
    zero_records: bool = False
    coverage_gaps: list = dc.field(default_factory=list)


def _candidate_from_record(rec: GoldenSolutionConformanceRecord) -> CandidateIssue:
    loc = rec.golden_code_location or (rec.solution_file + (":" + rec.solution_symbol_or_lines if rec.solution_symbol_or_lines else ""))
    traced = bool(loc and (rec.evidence or rec.counterfactual_environment)
                  and rec.verifier_has_discriminating_fixture != "yes")
    prov = "fully_traced_static" if traced else "runtime_dependent"
    ev = rec.evidence or [{"file": loc or "solution/", "location": rec.requirement_id,
                           "detail": rec.explanation or rec.actual_operation or "golden diverges from required semantics"}]
    return CandidateIssue(
        issue_id=f"GC-{rec.requirement_id}", source_passes=["golden_conformance"],
        severity="major", provenance=prov,
        claim=f"golden solution violates required semantics for {rec.requirement_id}: "
              f"required='{rec.required_operation[:120]}' actual='{rec.actual_operation[:120]}' ({rec.status})",
        evidence=ev, failure_mode="golden_solution_contract_violation",
        witness=Witness(type="golden_solution_contract_violation",
                        submission_or_condition=rec.actual_operation or "golden performs the wrong operation",
                        expected_contract_result=rec.counterfactual_required_outcome or rec.required_operation or "the contract-required operation",
                        predicted_verifier_result=rec.counterfactual_actual_outcome
                        or ("passes anyway (no discriminating fixture)" if rec.verifier_has_discriminating_fixture != "yes" else "verifier may catch it"),
                        execution_plan=rec.minimal_discriminating_fixture
                        or f"exercise the counterfactual ({rec.counterfactual_environment or rec.difference_observable_under or 'a method/dependency-sensitive case'}) and re-run the real verifier"))


def run(task: TaskPackage, call_llm: CallLLM) -> GoldenResult:
    prompt = promptlib.render("golden_conformance.md", **common_fields(task))
    raw, obj = call_llm_json(call_llm, prompt)
    if obj is None:
        return GoldenResult([], [], 1, raw, False, {}, zero_records=False)
    records = []
    for i, r in enumerate(obj.get("golden_conformance", []) if isinstance(obj, dict) else []):
        rec = GoldenSolutionConformanceRecord.from_obj(r, i)
        if rec is not None:
            records.append(rec)
    gaps: list = []
    for rec in records:
        # _validate may downgrade an unsupported `conformant` and emit a material gap
        # (operation-sensitive equivalence resting on a non-distinguishing counterfactual
        # or a shared code path, with no contract rule or universal proof -> fail-closed)
        _, gap = _validate(rec)
        if gap is not None:
            gaps.append(gap)
    candidates = [_candidate_from_record(r) for r in records if r.status in GOLDEN_NONCONFORMANT]
    zero = (len(records) == 0) and bool(task.solution_text().strip())
    return GoldenResult(records, candidates, 0, raw, True, obj, zero_records=zero, coverage_gaps=gaps)
