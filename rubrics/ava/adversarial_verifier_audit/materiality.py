#!/usr/bin/env python3
"""Deterministic materiality gate + recalibrated routing (v1.4).

WHY THIS EXISTS
---------------
The v1 policy in ``adjudication.route`` blocks whenever ANY ``supported`` (or
``confirmed``) Major candidate exists. Validated against the 20-task client-labeled
set this over-blocks: it flags every clean task and blocks several broken tasks for
reasons unrelated to the client's actual defect. ``status=supported`` is *evidence
quality*, not *routing authority*.

This module inserts a deterministic MATERIALITY GATE between candidate adjudication
and final routing. A candidate may independently trigger ``block`` only when it is a
concrete, plausible, material acceptance-boundary failure. Missing edge-case
coverage, speculative/hypothetical attacks, protected-secret equality, harmless
schema looseness, provenance-not-enforced (when the final artifact is exactly
checked), and non-distinguishing / semantically-equivalent golden-conformance
differences must NOT independently block.

BLINDNESS / ISOLATION
---------------------
This module is a GENERAL classifier: it keys ONLY on intrinsic fields already
present in an AVA report (status, severity, provenance, witness, failure_mode,
absence_claim, completeness_capped, source passes, and the backing golden record).
It contains NO client labels and never imports the scorer / held-out labels, so it
stays on the blind side of the audit (see ``test_audit_modules_do_not_import_scorer``).

The offline recalibration harness (``tools/ava_recalibrate.py``) uses this module to
reclassify the SAVED reports and compute recalibrated routing; the raw reports are
never mutated. This gate is ALSO wired into the LIVE routing path: ``audit.py`` calls
``adjudication.route_with_materiality``, which calls ``route_candidates`` below over
the in-memory ``CandidateIssue``/``GoldenSolutionConformanceRecord`` objects for the
current audit run. Both entry points share ``route_from_rows`` (the single routing
computation), so live and offline routing are provably identical for the same data
(see ``tools/tests/test_live_routing_equivalence.py``).
"""
from __future__ import annotations

import dataclasses as _dc
import re
from typing import Any

# ---- materiality taxonomy ---------------------------------------------------
# blocking classes (may block when Major + concrete proof)
BLOCKING_CLASSES = frozenset({
    "verified_exploit",              # concrete trust-boundary / reward-path breach that passes
    "verified_false_rejection",      # a contract-valid submission is concretely rejected
    "material_contract_contradiction",  # confirmed instruction<->verifier literal contradiction
    "material_golden_violation",     # golden diverges from required output on a real, present input
})
# review classes (never static_pass, never auto-block): credible but unproven/immaterial-to-block
REVIEW_CLASSES = frozenset({
    "coverage_gap",                  # concrete restricted-impl on a present requirement
    "source_incomplete",            # truncated/absent core source -> cannot confidently pass
    "unproven_material_concern",     # fail-closed default for an unmatched supported/confirmed Major
})
# discard classes (immaterial to routing -> permit static_pass)
DISCARD_CLASSES = frozenset({
    "protected_secret_equality",     # exact equality to an unguessable protected secret, no agent access
    "provenance_not_required",       # method/provenance unenforced but the exact artifact IS checked
    "schema_strictness_gap",         # extra keys / int-vs-float coercion, values otherwise exact
    "minor_precision_gap",           # numeric tolerance near/below the rounding quantum
    "golden_paraphrase",             # golden "nonconformance" that is semantically equivalent
    "speculative_attack",            # untraced branch / hypothetical crash on absent/degenerate data
    "generic_misalignment_flag",     # generic AL-* alignment potentials with no concrete witness
    "refuted_or_inactive",           # refuted/duplicate
})


def _low(x: Any) -> str:
    return x.lower() if isinstance(x, str) else ""


def _issue_text(issue: dict) -> str:
    """All of a candidate's own self-description, lowercased, for pattern tests.
    This is AVA's OWN structured taxonomy (failure_mode/claim/witness), not task
    keywords -- classifying on it is deterministic re-adjudication, not guessing."""
    w = issue.get("witness") or {}
    parts = [issue.get("failure_mode", ""), issue.get("claim", ""),
             w.get("submission_or_condition", ""), w.get("predicted_verifier_result", ""),
             w.get("expected_contract_result", ""), w.get("execution_plan", "")]
    return " ".join(_low(p) for p in parts)


def _predicted(issue: dict) -> str:
    return _low((issue.get("witness") or {}).get("predicted_verifier_result"))


# ---- concreteness of the predicted verifier outcome -------------------------
_ACCEPT_RE = re.compile(r"\b(accept|reward\s*=?\s*1|passes?\b|pass\b)")
_HYPO_RE = re.compile(
    r"(unknown statically|\bunknown\b|\bmay\b|possible|possibly|if a residual|"
    r"desync|if exit|if the drop|until records|regardless|not directly exploitable|"
    r"weak-evidence|statically;|could |might )")
_REJECT_RE = re.compile(r"\b(reject|reward\s*=?\s*0|fail|rejected|errors? out)\b")


def _concrete_accept(issue: dict) -> bool:
    p = _predicted(issue)
    return bool(_ACCEPT_RE.search(p)) and not bool(_HYPO_RE.search(p))


def _hypothetical(issue: dict) -> bool:
    p = _predicted(issue)
    wt = _low((issue.get("witness") or {}).get("type"))
    if _HYPO_RE.search(p):
        return True
    # a witness typed "other" with no concrete accept is not a demonstrated exploit
    return wt == "other" and not _ACCEPT_RE.search(p)


# ---- mechanism predicates (keyed on AVA's own text) -------------------------
# A concrete trust-boundary / reward-path breach: the exploit reaches protected
# truth or the reward path WITHOUT needing to solve the task.
_TRUST_MECH_RE = re.compile(
    r"(/tests|read the oracle|reads? the oracle|reference_report|reference_trust|"
    r"reference_stream|reference_decode|reference implementation from|expected_report|"
    r"import-?shadow|importlib|exec_module|monkeypatch|sys\.path|inputs\.hidden|"
    r"renamed original|leaked original|reads leaked|symlink|/logs|extract .*key|"
    r"read .*\bkey\b|vbyte|before the verifier derives|mutate.* input|"
    r"overwrite .*bytes|zero .*(row|n_rows)|rewrite .*bytes|pre-seed)"
)
# The exploit presupposes already knowing/producing the protected answer/target,
# so it is not a plausible bypass (unless the defect IS that the answer is exposed).
_REQUIRES_ANSWER_RE = re.compile(
    r"(reconstruct.*target|apply_delta|provenance-forg|provenance-not-enforced|"
    r"provenance not enforced|already know|already reconstruct|copied from any leak|"
    r"if the constant is known|reproduc.*literal|write.*literal|base_blob_sha|"
    r"any blob that|non-unique|does not certify|method.*not.*enforc)"
)
# Exact equality to an unguessable protected secret (the deliverable IS the answer),
# with the verifier merely storing the literal internally.
_SECRET_RE = re.compile(
    r"(hardcoded_expected_output|copied from any leak|verifier accepts the literal|"
    r"string equality to a hardcoded literal|writes this literal|equal to the literal|"
    r"literal .*with zero task work|without solving the challenge|hardcoded string literal|"
    r"literal answer string)"
)
# Grade-time candidate execution reading a protected source (turns a "leaked answer"
# claim into a real exploit); distinguishes b2a64fd from 1fe18bd/b32d924.
_GRADE_TIME_EXEC_RE = re.compile(
    r"(executed at verify time|execute.*at verify|read the oracle from /tests|"
    r"reads /tests|import-?shadow|importlib|exec_module|monkeypatch|delegate)"
)
# Harmless numeric-tolerance / schema-looseness.
_SCHEMA_RE = re.compile(
    r"(tolerance|sub-quantization|rounding|int/float|int/-float|float==int|"
    r"extra-?field|missing-?field|extra keys|extra top-level|coercion|"
    r"non-int|schema looseness|missing-field-strength|numeric tolerance)"
)
# Speculative / hypothetical / untraced / cross-pass (no concrete passing exploit).
_SPECULATIVE_RE = re.compile(
    r"(untraced|coverage-weakening|speculativ|hypothetical|over-blocks if|"
    r"ever degenerate|non-canonical|empty-collection|latent_unsound|"
    r"cross-pass_contradiction|missing_golden_conformance_records|fail-closed crash|"
    r"unverifiable_conformance|environment_isolation_unresolved|"
    r"operation-not-universally-scoped|masked_failure|valid_work_blocked)"
)
# A coverage gap / verifier-incompleteness: a requirement lacks a discriminating
# fixture, a field is never asserted, or conformance rests on a non-distinguishing
# counterfactual, so a restricted/wrong implementation could pass. This is verifier
# INCOMPLETENESS (not a trust-boundary breach) -> human_review, never block.
_COVERAGE_RE = re.compile(
    r"(no fixture|no test|not tested|untested|omit|restricted implementation|"
    r"restricted/hardcod|structure-hardcod|hardcode the fixed|never varied|"
    r"no discriminating fixture|partial.*implementation passes|cardinality|"
    r"restricted_implementation_passes|unverified_requirement|"
    r"partially_tested_requirement|unstated_test_behavior|untested_requirement|"
    r"never asserted|never checked|never validated|not asserted by any test|"
    r"no test asserts|unasserted|with no penalty|fields tolerated|"
    r"non-distinguishing|shared-code-path)"
)
# ---- structured golden-counterfactual properties (replaces the old blanket
# "constructed input -> discard" heuristic) --------------------------------
# A counterfactual environment is OUT-OF-DOMAIN / not reachable when it describes
# something that itself violates the task's own declared naming/schema/contract (NOT
# merely "constructed" -- multiple valid rows, an extra valid record, or a fresh but
# schema-conforming file are all perfectly reachable). This is a general, structural
# signal (the counterfactual violates its own stated shape), not a per-task keyword.
_GOLDEN_OUT_OF_DOMAIN_RE = re.compile(
    r"(not matching|does not match|doesn'?t match|mismatched name|misnamed|"
    r"wrong file ?name|invalid file ?name|unexpected filename|non-?conforming name|"
    r"outside the declared (domain|schema|environment)|violates the (declared )?schema|"
    r"violates the (declared )?naming|not a valid input|impossible under|"
    r"cannot occur under the declared|not expressible|would require modifying the verifier|"
    r"no valid agent action produces|not achievable by|hypothetical alternate)"
)
# an alleged "required" reading that is itself not authorized by the contract
_GOLDEN_OUT_OF_CONTRACT_RE = re.compile(
    r"(not authorized by the (instruction|contract)|not a required reading|"
    r"unsupported interpretation of the contract)"
)
# evidence that the exact counterfactual configuration is already present among the
# shipped/committed/graded artifacts (vs a configuration that would need constructing)
_GOLDEN_SHIPPED_RE = re.compile(
    r"(the shipped|the committed|the public fixture|already present|the existing (test|fixture)|"
    r"the graded input|the actual corpus|the visible fixture)"
)
# an explicit statement that the divergence has no bearing on the graded/required output
_GOLDEN_NO_CONSEQUENCE_RE = re.compile(
    r"(does not affect the (graded|required) output|does not change verifier truth|"
    r"output remains correct|no material consequence)"
)


def _golden_counterfactual_properties(g: dict) -> dict:
    """Derive the 5 structured counterfactual properties for a golden-conformance
    record. ``distinguishing`` reads the CODE-COMPUTED field (golden_conformance.py's
    ``_validate`` recomputes it from the traced outcomes, not merely the model's claim).
    """
    op_equiv = _low(g.get("operation_semantically_equivalent"))
    cf_distinguishing = _low(g.get("counterfactual_is_distinguishing"))
    verifier_fixture = _low(g.get("verifier_has_discriminating_fixture"))
    req_op = (g.get("required_operation") or g.get("required_behavior") or "")
    combined = _low(g.get("difference_observable_under")) + " " + _low(g.get("counterfactual_environment"))

    # NOTE: a code-traced operation mismatch that reaches SOME downstream_dependency/
    # operation_boundary text is NOT by itself a reliable "this happens on real graded
    # data" signal -- in practice almost every op-sensitive record populates these Part-A
    # trace fields, so using their mere presence to escalate straight to `block` reproduces
    # exactly the over-blocking this milestone corrects (verified against the real 20-task
    # corpus, where a materially-identical shape is a credible-but-unconfirmed
    # generalization defect, not a proven exploit). Only `verifier_has_discriminating_fixture
    # == "yes"` or explicit shipped/committed-fixture evidence justifies `block`; a code-
    # traced mismatch without that evidence is `human_review`, matched by the fall-through
    # below.
    distinguishing = (cf_distinguishing == "yes")
    return {
        "op_equiv": op_equiv,
        "distinguishing": distinguishing,
        "asserted_nonequivalent": (op_equiv == "no"),
        "environment_reachable": not bool(_GOLDEN_OUT_OF_DOMAIN_RE.search(combined)),
        "contract_valid": bool(req_op.strip()) and not bool(_GOLDEN_OUT_OF_CONTRACT_RE.search(combined)),
        "affects_required_output": distinguishing and not bool(_GOLDEN_NO_CONSEQUENCE_RE.search(combined)),
        "present_in_shipped_fixture": (verifier_fixture == "yes") or bool(_GOLDEN_SHIPPED_RE.search(combined)),
    }


def classify_golden_counterfactual(g: dict) -> dict:
    """Classify a golden-conformance record into (materiality_class, blocking,
    review, basis, reason) using the structured properties, per policy:

      valid + reachable + distinguishing + material consequence -> block or
        human_review according to evidence (present in a shipped fixture -> block;
        otherwise a credible, unconfirmed generalization defect -> review)
      valid but unresolved materiality/reachability                -> human_review
      impossible, out-of-contract, or non-distinguishing            -> spurious
      semantic paraphrase (operation_semantically_equivalent=yes)   -> spurious

    A contract-valid CONSTRUCTED input (multiple rows, an extra record, a fresh but
    schema-conforming file) is NEVER auto-discarded merely for being constructed --
    only genuinely out-of-domain/out-of-contract/non-distinguishing counterfactuals are.
    """
    p = _golden_counterfactual_properties(g)
    if p["op_equiv"] == "yes" and not p["distinguishing"]:
        # a genuinely CODE-COMPUTED distinguishing outcome (golden_conformance.py's
        # `_validate` recomputes this from traced required/actual outcome text) takes
        # precedence over a possibly-stale `operation_semantically_equivalent=yes` claim
        # left over from before the record's status was flipped to nonconformant.
        return {"cls": "golden_paraphrase", "blocking": False, "review": False, "basis": "",
                "reason": "golden 'nonconformance' is semantically equivalent to the required operation "
                          "(paraphrase, not a behavioral difference)", "properties": p}
    if not p["distinguishing"]:
        if p["asserted_nonequivalent"]:
            return {"cls": "material_golden_violation", "blocking": False, "review": True,
                    "basis": "golden_unproven_divergence", "properties": p,
                    "reason": "golden asserts operation_semantically_equivalent=no but no distinguishing "
                              "counterfactual was established -> credible but unproven, human review"}
        return {"cls": "golden_paraphrase", "blocking": False, "review": False, "basis": "", "properties": p,
                "reason": "the counterfactual does not distinguish required from actual outcome; "
                          "not a material defect"}
    if not p["environment_reachable"]:
        return {"cls": "golden_paraphrase", "blocking": False, "review": False, "basis": "", "properties": p,
                "reason": "the counterfactual describes an out-of-domain / impossible construction (violates "
                          "the task's own declared naming/schema/contract); not reachable by a normal agent"}
    if not p["contract_valid"]:
        return {"cls": "golden_paraphrase", "blocking": False, "review": False, "basis": "", "properties": p,
                "reason": "the alleged 'required' reading is not itself authorized by the contract"}
    if not p["affects_required_output"]:
        return {"cls": "golden_paraphrase", "blocking": False, "review": False, "basis": "", "properties": p,
                "reason": "the divergence does not affect the required output / verifier truth"}
    # valid, reachable, distinguishing, affects output -- a contract-valid constructed
    # input is NOT discarded here even though it may not be present in a shipped fixture.
    if p["present_in_shipped_fixture"]:
        return {"cls": "material_golden_violation", "blocking": True, "review": False,
                "basis": "golden_semantic_divergence", "properties": p,
                "reason": "golden/reference solution semantically diverges from the required output on a "
                          "present, shipped, distinguishing input -> material artifact contradiction"}
    return {"cls": "material_golden_violation", "blocking": False, "review": True,
            "basis": "golden_divergence", "properties": p,
            "reason": "golden/reference solution plausibly diverges from the required output on a "
                      "contract-valid, reachable, distinguishing input not yet confirmed against a shipped "
                      "fixture -> credible generalization defect, human review"}


def _is_generic_alignment(issue: dict) -> bool:
    """The Mapper's four-way alignment matrix synthesizes generic AL-* potentials
    whose witness is the boilerplate 'an implementation that exploits this
    misalignment'. These carry no concrete restricted implementation."""
    iid = _low(issue.get("issue_id"))
    sub = _low((issue.get("witness") or {}).get("submission_or_condition"))
    if iid.startswith("al-"):
        return True
    return "an implementation that exploits this misalignment" in sub


def _golden_record_for(issue: dict, report: dict) -> dict:
    """Best-effort lookup of the golden-conformance record backing a
    golden_solution_contract_violation candidate (issue_id like 'GC-GS1')."""
    iid = issue.get("issue_id") or ""
    m = re.search(r"(gs\d+)", iid.lower())
    if not m:
        return {}
    rid = m.group(1).upper()
    for g in report.get("golden_conformance", []) or []:
        if (g.get("requirement_id") or "").upper() == rid:
            return g
    return {}


def _is_golden(issue: dict) -> bool:
    wt = _low((issue.get("witness") or {}).get("type"))
    srcs = [s for s in (issue.get("source_passes") or [])]
    return wt == "golden_solution_contract_violation" or srcs == ["golden_conformance"]


# Structured WITNESS_TYPE signals (schema enum, not free text) for candidates whose
# ``status`` alone does not (yet) prove concreteness. A `confirmed` status (deterministic
# scanner pattern or >=2-cross-cited literal contradiction) already IS the proof for these
# mechanism types, so no additional predicted-accept text is required. For NOT-YET-confirmed
# (`potential`) candidates, these types are still credible enough for human_review.
_CONFIRMED_EXPLOIT_WTYPES = frozenset({
    "leaked_answer", "expected_output_modification", "writable_trusted_file",
    "import_shadowing", "symlink_path", "nan_inf",
})
_CREDIBLE_MECHANISM_WTYPES = frozenset({
    "leaked_answer", "expected_output_modification", "writable_trusted_file",
    "import_shadowing", "symlink_path", "nan_inf", "restricted_implementation_passes",
})


# ---- scope ownership (AVA's production role) --------------------------------
# AVA is the pre-pass for exactly TWO issue families:
#   (1) verifier coverage / soundness
#   (2) artifact alignment
# Instruction ambiguity, coherent-contract, information-theoretic solvability /
# uniqueness, benchmark realism, runnable/domain quality, exhaustive theoretical
# edge cases, and stylistic schema/tolerance belong to the separate DEEP-REVIEW
# stage. AVA may retain those as ADVISORY evidence but must never block or route on
# them. A candidate may keep routing authority ONLY when scope_owner=="ava"; a block
# additionally requires scope_family in {verifier_coverage, artifact_alignment}.
# Ambiguity is AVA-relevant only when artifacts resolve it inconsistently (verifier
# enforces one reading, golden contradicts verifier, a valid reading is rejected) --
# those already surface as the concrete verifier/artifact classes below.
_DEEPREVIEW_RE = re.compile(
    r"(ambigu|underspecified|under-specified|coherent[- ]contract|contract is incomplete|"
    r"contract (is )?ambiguous|not uniquely (solv|determin|identif)|uniqueness|"
    r"information-theoretic|not identifiable|infinitely many|unsatisfiable|cannot both|"
    r"mutually (un)?satisf|two incompatible|incompatible complete|benchmark realism|"
    r"task interest|domain-quality|runnable-quality|not a meaningful|is not uniquely)"
)
# signals that a concern IS about verifier/fixture/artifact behaviour (keeps it AVA-owned
# even if the word 'ambiguous' appears -- an artifact resolving ambiguity inconsistently)
_AVA_SIGNAL_RE = re.compile(
    r"(verifier|fixture|/tests|test_outputs|oracle|golden|expected|reward|accept|reject|"
    r"protected|agent-writable|agent-controlled|symlink|import|monkeypatch|mutate|"
    r"discriminat|assert|coverage|acceptance boundary|reference_)"
)
# information-theoretic uniqueness / solvability -> deep-review-owned by the production
# split, even when framed with verifier words. Applied ONLY to non-routing findings so it
# can never change routing (it re-labels advisory evidence as a deep-review handoff).
_SOLVABILITY_RE = re.compile(
    r"(uniqueness|non-unique|not unique|does not uniquely|information-theoretic|"
    r"infinitely many|spurious parameter|not uniquely (solv|identif|determin)|"
    r"second planted|multiple .*(satisfy|solutions))"
)
SCOPE_BY_CLASS = {
    "verified_exploit": ("ava", "verifier_coverage"),
    "verified_false_rejection": ("ava", "verifier_coverage"),
    "coverage_gap": ("ava", "verifier_coverage"),
    "source_incomplete": ("ava", "verifier_coverage"),
    "protected_secret_equality": ("ava", "verifier_coverage"),
    "provenance_not_required": ("ava", "verifier_coverage"),
    "material_contract_contradiction": ("ava", "artifact_alignment"),
    "material_golden_violation": ("ava", "artifact_alignment"),
    "golden_paraphrase": ("ava", "artifact_alignment"),
    "schema_strictness_gap": ("advisory", "other"),
    "minor_precision_gap": ("advisory", "other"),
    "speculative_attack": ("advisory", "other"),
    "refuted_or_inactive": ("advisory", "other"),
    "generic_misalignment_flag": ("advisory", "artifact_alignment"),
    "unproven_material_concern": ("ava", "verifier_coverage"),  # refined below
}


def classify_scope(cls: str, blob: str, concrete: bool = False) -> tuple:
    """Return (in_scope, scope_owner, scope_family, scope_reason).

    A pure deep-review concern (instruction ambiguity / coherent-contract /
    solvability / realism) with NO concrete verifier or artifact consequence is
    handed to deep review as advisory and never drives AVA routing.

    ``concrete`` must be a REAL demonstrated consequence (a concrete predicted
    accept/reject outcome), not merely the casual presence of a word like
    'verifier'/'acceptance boundary' in the narration -- a pure solvability/
    uniqueness/ambiguity concern that happens to MENTION the verifier or the
    acceptance boundary, but demonstrates no concrete disagreement, is still
    deep-review-owned.
    """
    owner, family = SCOPE_BY_CLASS.get(cls, ("advisory", "other"))
    # Demote to deep-review only for non-AVA-routing classes whose dominant signal is
    # out-of-scope; a candidate with a REAL concrete verifier/artifact consequence is
    # never demoted merely for using verifier-adjacent vocabulary.
    if cls in ("unproven_material_concern", "speculative_attack", "generic_misalignment_flag"):
        if _DEEPREVIEW_RE.search(blob) and not (concrete and _AVA_SIGNAL_RE.search(blob)):
            fam = "instruction_ambiguity" if re.search(r"ambigu", blob) else "other"
            return (False, "deep_review", fam,
                    "out-of-scope for AVA (instruction ambiguity / coherent-contract / solvability / "
                    "realism) with no concrete verifier or artifact-alignment consequence; hand to deep review")
    reason = {
        "ava": "AVA-owned: bears on verifier coverage/soundness or artifact alignment",
        "advisory": "retained as advisory evidence; AVA is not the routing owner",
        "deep_review": "handed to the deep-review stage",
    }[owner]
    return (owner == "ava", owner, family, reason)


def _apply_scope(base: dict, issue: dict) -> dict:
    """Overlay the scope decision + ownership gate onto a base materiality row.

    Decisive gate: a candidate keeps blocking_eligible/routes_to_review ONLY when
    scope_owner=="ava" (blocking also requires an AVA scope_family). Candidate status
    (supported/confirmed) never determines routing.
    """
    blob = _issue_text(issue)
    concrete = bool(base.get("has_concrete_witness"))
    in_scope, owner, family, sreason = classify_scope(base["materiality_class"], blob, concrete=concrete)
    pre_block, pre_review = base["blocking_eligible"], base["routes_to_review"]
    # non-routing uniqueness/solvability concerns are deep-review-owned (advisory handoff);
    # gated on non-routing so routing is provably unchanged.
    if owner in ("ava", "advisory") and not pre_block and not pre_review and _SOLVABILITY_RE.search(blob):
        in_scope, owner, family = False, "deep_review", "other"
        sreason = ("out-of-scope for AVA (information-theoretic uniqueness/solvability); no concrete "
                   "verifier/artifact routing consequence -> advisory handoff to deep review")
    block = bool(pre_block and owner == "ava" and family in ("verifier_coverage", "artifact_alignment"))
    review = bool(pre_review and owner == "ava")
    if block:
        tier = "blocking"
    elif review or owner == "deep_review":
        tier = "review"
    elif base["materiality_class"] in ("schema_strictness_gap", "minor_precision_gap"):
        tier = "minor"
    else:
        tier = "spurious"
    base.update({
        "in_scope": in_scope,
        "scope_owner": owner,
        "scope_family": family,
        "scope_reason": sreason,
        "materiality_tier": tier,
        "blocking_eligible": block,
        "routes_to_review": review,
        "pre_scope_blocking": pre_block,
        "pre_scope_review": pre_review,
        "downgraded_by_scope": bool((pre_block and not block) or (pre_review and not review)),
        "deep_review_handoff": owner == "deep_review",
        "advisory": owner == "advisory",
    })
    return base


# ---- the classifier ---------------------------------------------------------
def classify_issue(issue: dict, report: dict) -> dict:
    """Classify a candidate's materiality, then apply the scope-ownership gate."""
    return _apply_scope(_classify_material(issue, report), issue)


def _classify_material(issue: dict, report: dict) -> dict:
    """Assign a materiality class + blocking-eligibility to ONE candidate issue.

    Returns a row with:
      materiality_class, blocking_eligible, routes_to_review, blocking_basis,
      has_concrete_witness, witness_is_passing_exploit, acceptance_boundary_proof,
      reason.

    Ordering matters: specific discard predicates (protected-secret, requires-answer,
    schema, golden-paraphrase, speculative) are checked BEFORE the generic exploit /
    coverage rules so an immaterial finding cannot masquerade as an exploit, and the
    fail-closed default routes any unmatched supported/confirmed Major to review.
    """
    status = _low(issue.get("status"))
    sev = _low(issue.get("severity"))
    prov = _low(issue.get("provenance"))
    wt = _low((issue.get("witness") or {}).get("type"))
    blob = _issue_text(issue)
    pred = _predicted(issue)
    is_major = sev in ("major", "potential_major")
    live = status in ("confirmed", "supported", "potential")
    concrete_accept = _concrete_accept(issue)

    def row(cls, blocking=False, review=False, basis="", proof="", reason=""):
        return {
            "issue_id": issue.get("issue_id", ""),
            "ava_status": status, "ava_severity": sev, "ava_provenance": prov,
            "witness_type": wt,
            "materiality_class": cls,
            "blocking_eligible": bool(blocking),
            "routes_to_review": bool(review),
            "blocking_basis": basis,
            "has_concrete_witness": bool(concrete_accept or _REJECT_RE.search(pred)),
            "witness_is_passing_exploit": bool(concrete_accept),
            "acceptance_boundary_proof": proof,
            "reason": reason,
        }

    # 0) inactive
    if status in ("refuted", "duplicate"):
        return row("refuted_or_inactive", reason="refuted/duplicate: not live evidence")

    # 1) confirmed instruction<->verifier literal contradiction => material contract
    #    contradiction (a contract-valid answer is mandated wrong / rejected).
    if status == "confirmed" and prov == "literal_contradiction" and is_major:
        return row("material_contract_contradiction", blocking=True,
                   basis="literal_contradiction",
                   proof=(issue.get("witness") or {}).get("predicted_verifier_result", ""),
                   reason="confirmed instruction<->verifier contradiction with >=2 cross-cited paths; "
                          "a contract-valid submission is mandated wrong")

    # 2) protected-secret exact equality (deliverable IS an unguessable secret,
    #    verifier stores the literal, no grade-time candidate execution reads it).
    if _SECRET_RE.search(blob) and not _GRADE_TIME_EXEC_RE.search(blob):
        return row("protected_secret_equality",
                   reason="exact equality to an unguessable protected answer fully checks the required "
                          "artifact; the proposed bypass requires already knowing the answer")

    # 3) provenance/method not enforced while the exact (unguessable) artifact IS
    #    checked, or the 'exploit' requires already reconstructing the target.
    if _REQUIRES_ANSWER_RE.search(blob) and not _GRADE_TIME_EXEC_RE.search(blob):
        return row("provenance_not_required",
                   reason="the verifier checks the exact final artifact; the proposed bypass enforces "
                          "derivation/provenance the contract does not require, or presupposes the target")

    # 4) harmless schema looseness / numeric tolerance
    if _SCHEMA_RE.search(blob):
        cls = "minor_precision_gap" if ("toler" in blob or "rounding" in blob) else "schema_strictness_gap"
        return row(cls, reason="tolerance near/below the rounding quantum or extra-key/int-vs-float "
                               "looseness with otherwise-exact values; not a material acceptance-boundary defect")

    # 5) golden-conformance: classified via the structured 5-property counterfactual
    #    schema (contract_valid, environment_reachable, distinguishing,
    #    affects_required_output, present_in_shipped_fixture). A contract-valid
    #    CONSTRUCTED input is never auto-discarded merely for being constructed.
    if _is_golden(issue):
        g = _golden_record_for(issue, report)
        gc = classify_golden_counterfactual(g)
        return row(gc["cls"], blocking=gc["blocking"], review=gc["review"], basis=gc["basis"],
                   proof=(g.get("difference_observable_under", "") if gc["blocking"] else ""),
                   reason=gc["reason"])

    # 6) speculative / hypothetical / untraced / cross-pass (no concrete passing exploit)
    if _SPECULATIVE_RE.search(blob) or (is_major and _hypothetical(issue) and wt == "other"):
        return row("speculative_attack",
                   reason="hypothetical crash / untraced branch / undetermined-runtime / cross-pass concern "
                          "on absent or degenerate data; no concrete passing wrong implementation demonstrated")

    # 7) generic alignment-matrix potentials (no concrete witness)
    if _is_generic_alignment(issue):
        return row("generic_misalignment_flag",
                   reason="generic four-way alignment flag with no concrete restricted implementation")

    # 7.55) a NOT-YET-confirmed (typically `potential`/runtime_dependent) candidate
    #     whose WITNESS TYPE is a structurally recognized trust-boundary/reward-path
    #     or restricted-implementation mechanism is credible even without concrete
    #     predicted-accept text -> human review (never block without proof). This is
    #     keyed on the schema's own WITNESS_TYPE enum, not free text, so it is general,
    #     and is checked BEFORE the generic text-based coverage rule below so a
    #     structurally recognized mechanism is never demoted to non-reviewing just
    #     because it ALSO happens to match generic coverage-gap wording.
    if is_major and wt in _CREDIBLE_MECHANISM_WTYPES and status not in ("supported", "confirmed"):
        return row("coverage_gap", review=True, basis="credible_mechanism_unproven",
                   reason=f"witness type '{wt}' identifies a recognized trust-boundary/reward-path or "
                          "restricted-implementation mechanism that is credible but not yet concretely proven "
                          "at runtime -> human review, not block")

    # 7.5) coverage gap / field-omission / non-distinguishing conformance -> the
    #     verifier is INCOMPLETE (missing fixture, unasserted field, or conformance
    #     resting on a non-distinguishing counterfactual) so a restricted/wrong
    #     implementation could pass. Checked BEFORE the exploit rule so a coverage
    #     gap is never upgraded to a block merely because a fixture is missing.
    if _COVERAGE_RE.search(blob):
        if status in ("supported", "confirmed") and is_major:
            return row("coverage_gap", review=True, basis="coverage_gap",
                       reason="verifier incompleteness (missing discriminating fixture / unasserted field / "
                              "non-distinguishing counterfactual); a restricted or wrong implementation could "
                              "pass, but there is no breach of protected truth -> human review, not block")
        return row("coverage_gap", reason="unproven / non-major coverage gap")

    # 8) verified exploit: a concrete trust-boundary / reward-path breach that passes,
    #    that a normal agent (or a trivial adversary) can build without the answer.
    #    A `confirmed` status (deterministic scanner pattern, re-checkable without
    #    execution) with a structurally recognized witness type is ALREADY the proof --
    #    it does not additionally need matching free-text or predicted-accept wording.
    if status in ("supported", "confirmed") and sev == "major":
        if status == "confirmed" and wt in _CONFIRMED_EXPLOIT_WTYPES:
            return row("verified_exploit", blocking=True, basis="deterministic_confirmed_exploit",
                       proof=pred or "deterministic, re-checkable pattern confirms the mechanism "
                                     "(no execution required)",
                       reason="a deterministic re-checkable pattern (status=confirmed) demonstrates a "
                              "trust-boundary/reward-path breach; the pattern itself is the proof")
        if _TRUST_MECH_RE.search(blob) and concrete_accept and not _REQUIRES_ANSWER_RE.search(blob):
            return row("verified_exploit", blocking=True, basis="trust_boundary_reward_path",
                       proof=(issue.get("witness") or {}).get("predicted_verifier_result", ""),
                       reason="concrete, plausible submission reaches protected truth / the reward path and is "
                              "accepted (reward=1) without solving the task")

    # 9) verified false rejection: a contract-valid submission is concretely rejected,
    #    with affirmative contract permission for the rejected behavior (or a
    #    `confirmed` deterministic pattern with a structurally recognized witness type).
    if status in ("supported", "confirmed") and sev == "major" and wt == "valid_but_rejected":
        if status == "confirmed":
            return row("verified_false_rejection", blocking=True, basis="deterministic_confirmed_false_rejection",
                       proof=pred or "deterministic pattern confirms a contract-valid submission is rejected",
                       reason="a deterministic re-checkable pattern (status=confirmed) demonstrates the verifier "
                              "rejects a contract-valid submission")
        if (_REJECT_RE.search(pred) and prov in ("literal_contradiction", "fully_traced_static")
                and re.search(r"(instruction|contract|spec).*(permit|allow|require|says|defines|mandate)", blob)):
            return row("verified_false_rejection", blocking=True, basis="contract_valid_rejected",
                       proof=pred,
                       reason="a contract-permitted submission is rejected by the verifier (affirmative "
                              "permission cited)")

    # 11) source-incompleteness signals
    if issue.get("completeness_capped") or prov == "incomplete_source_context":
        return row("source_incomplete", review=True, basis="source_incomplete",
                   reason="finding rests on truncated/omitted source; cannot confidently block or pass -> review")

    # 12) fail-closed DEFAULT: an unmatched supported/confirmed Major is a credible,
    #     unproven material concern -> human_review (never silently accepted, never block).
    if status in ("supported", "confirmed") and is_major:
        return row("unproven_material_concern", review=True, basis="unclassified_major",
                   reason="supported/confirmed Major that matches no discard predicate and no concrete exploit "
                          "-> human review (fail-closed); not a proven blocking-eligible defect")

    # everything else (potential/minor, no concrete mechanism) is non-routing
    return row("speculative_attack",
               reason="non-major / potential concern without a concrete acceptance-boundary consequence")


def classify_report(report: dict) -> list[dict]:
    return [classify_issue(i, report) for i in (report.get("candidate_issues") or [])]


def route_from_rows(rows: list[dict], parse_failures: int, any_capped: bool) -> dict:
    """THE single routing computation, shared by every entry point (offline
    ``recalibrated_route`` over a saved report dict, and live ``route_candidates``
    over in-memory ``CandidateIssue``/``GoldenSolutionConformanceRecord`` objects).
    Routing is computed ONLY from: blocking-eligible rows, review-eligible rows,
    parse failures, and completeness caps -- never from raw candidate status alone.

    Precedence:
      1. any blocking_eligible issue, or a parse-failure fail-closed     -> block
      2. otherwise (incl. Minor routes_to_review + truncated-source advisories) -> static_pass
    """
    blocking = [r for r in rows if r["blocking_eligible"]]
    review = [r for r in rows if r["routes_to_review"]]
    deep_review = [r for r in rows if r.get("deep_review_handoff")]
    advisory = [r for r in rows if r.get("advisory")]
    downgraded = [r for r in rows if r.get("downgraded_by_scope")]
    handoff_note = f" | {len(deep_review)} finding(s) handed to deep review" if deep_review else ""

    # POLICY: binary block/pass. Only a Major (blocking_eligible) AVA-owned finding,
    # or a parse-failure fail-closed (a malformed audit that could not be adjudicated),
    # blocks. "Minor" concerns (routes_to_review: coverage_gap / source_incomplete /
    # unproven_material_concern / unconfirmed golden divergence) do NOT block -- this
    # INCLUDES truncated/incomplete source (completeness_capped), which classifies as the
    # Minor `source_incomplete` class, so capped source and provenance=incomplete_source_context
    # behave identically. All such findings fall through to static_pass and are surfaced as
    # non-blocking advisories (see `review_ids`). IMMATERIAL/advisory classes (schema/tolerance/
    # protected-secret/paraphrase/speculative/generic) and deep-review handoffs also pass.
    # (`any_capped` is retained in the signature for back-compat but no longer forces a block.)
    fail_closed = parse_failures > 0
    if blocking or fail_closed:
        routing = "block"
        bits = []
        if blocking:
            bits.append("major: " + "; ".join(f"{r['issue_id']}:{r['materiality_class']}" for r in blocking))
        if parse_failures > 0:
            bits.append(f"{parse_failures} parse failure(s)")
        if review:
            bits.append("minor (advisory): " + "; ".join(f"{r['issue_id']}:{r['materiality_class']}" for r in review))
        reason = " | ".join(bits) + handoff_note
    else:
        routing = "static_pass"
        minor_note = (" | minor advisory: " + "; ".join(f"{r['issue_id']}:{r['materiality_class']}" for r in review)) if review else ""
        reason = ("no AVA-owned Major blocking issue; concerns are minor/advisory (coverage/tolerance/"
                  "protected-secret/paraphrase/speculative/truncated-source) or out-of-scope" + minor_note + handoff_note)

    return {
        "routing": routing,
        "reason": reason,
        "rows": rows,
        "blocking_eligible_ids": [r["issue_id"] for r in blocking],
        "review_ids": [r["issue_id"] for r in review],
        "deep_review_ids": [r["issue_id"] for r in deep_review],
        "advisory_ids": [r["issue_id"] for r in advisory],
        "downgraded_ids": [r["issue_id"] for r in downgraded],
    }


def recalibrated_route(report: dict) -> dict:
    """OFFLINE entry point: recompute routing from a saved report dict (does NOT
    mutate the report). Delegates to ``route_from_rows`` -- the exact same function
    the LIVE entry point (``route_candidates``) uses, so offline and live routing
    are provably identical for the same underlying candidate/golden data.
    """
    rows = classify_report(report)
    ar = report.get("audit_result", {}) or {}
    parse_failures = int(ar.get("material_parse_failures", 0) or 0)
    any_capped = any(i.get("completeness_capped") for i in (report.get("candidate_issues") or []))
    return route_from_rows(rows, parse_failures, any_capped)


# ---- live-routing entry points (dataclass objects, not saved-report dicts) ---
def _to_dict(obj):
    """Coerce a dict, a dataclass instance, or None into a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if _dc.is_dataclass(obj):
        return _dc.asdict(obj)
    return dict(obj) if hasattr(obj, "keys") else {}


def classify_candidates(candidates, golden_conformance) -> list[dict]:
    """LIVE entry point: classify a list of ``CandidateIssue`` objects (or plain
    dicts) against a list of ``GoldenSolutionConformanceRecord`` objects (or plain
    dicts), WITHOUT requiring a saved-report JSON. Used by ``audit.py`` /
    ``adjudication.route_with_materiality`` to apply the identical classifier used
    offline. Never imports client labels or the post-run scorer/held-out mapping.
    """
    report = {"golden_conformance": [_to_dict(g) for g in (golden_conformance or [])]}
    return [classify_issue(_to_dict(c), report) for c in (candidates or [])]


def route_candidates(candidates, golden_conformance, parse_failures: int) -> dict:
    """LIVE entry point used by ``adjudication.route_with_materiality``. Converts
    ``CandidateIssue``/``GoldenSolutionConformanceRecord`` dataclass objects to dicts
    and reuses the EXACT SAME ``classify_issue``/``route_from_rows`` logic as the
    offline path (``recalibrated_route``), so live and offline routing are provably
    identical for the same underlying data (see the equivalence regression test).
    """
    dict_candidates = [_to_dict(c) for c in (candidates or [])]
    report = {"golden_conformance": [_to_dict(g) for g in (golden_conformance or [])]}
    rows = [classify_issue(c, report) for c in dict_candidates]
    any_capped = any(c.get("completeness_capped") for c in dict_candidates)
    return route_from_rows(rows, parse_failures, any_capped)
