#!/usr/bin/env python3
"""Typed schemas for the Adversarial Verifier Audit (AVA).

stdlib-only (dataclasses). These types mirror the output schema in
``adversarial_audit_verifier.md`` with AVA's v1 fail-closed renames:

* ``AuditResult`` uses ``static_audit_passed`` (NOT ``safe_to_accept``) and adds
  ``taxonomy_coverage_complete`` / ``unresolved_material_gaps`` /
  ``execution_validation_complete`` (always False in v1) / ``material_parse_failures``.
* Candidate ``status`` is computed in code from provenance (see ``adjudication``),
  never trusted from the model.

Validation happens in code (``coerce_*`` + ``validate``), not by trusting prompt
formatting. Parsing is FAIL-CLOSED: malformed candidate records are never silently
dropped -- callers convert them into coverage gaps + a parse-failure flag (see
``parse_candidate_issues``).
"""
from __future__ import annotations

import dataclasses as dc
import json
import re
from typing import Any, Optional

# ---------------------------------------------------------------- enums -------
# Kept as plain frozensets + a coercion helper (no Enum ceremony; matches the
# stdlib-only, greppable style of the rest of the toolage).

INPUT_TRUST = frozenset({"trusted", "agent_controlled", "mixed", "unknown"})
COVERAGE_STRENGTH = frozenset({"strong", "partial", "superficial", "none", "unknown"})
# necessity classification for an acceptance condition (spec: necessary/sufficient/partial/superficial)
NECESSITY = frozenset({"necessary", "sufficient", "partial", "superficial", "unknown"})
REQUIREMENT_STATUS = frozenset({
    "fully_verified", "partially_verified", "not_verified",
    "verified_incorrectly", "verified_by_implementation_detail", "cannot_determine",
})
# For the verifier->instruction direction: is a material verifier assertion
# authorized by the written instruction? (an unauthorized/unstated requirement is
# a misalignment even if the instruction->verifier status looks fine).
AUTHORIZED = frozenset({"yes", "no", "unclear"})
# instruction->verifier statuses that mean a stated requirement is not meaningfully tested
UNTESTED_STATUSES = frozenset({"not_verified", "verified_incorrectly", "verified_by_implementation_detail"})

# ---- v1.1 artifact-alignment enums ----
REQUIREMENT_PROVENANCE = frozenset({
    "explicit_instruction", "reasonably_inferred_from_environment",
    "necessary_domain_context", "unsupported_inference", "cannot_determine",
})
ENVIRONMENT_SUPPORT = frozenset({"supported", "partially_supported", "unsupported", "cannot_determine"})
ENFORCEMENT_MODE = frozenset({
    "direct_verifier_test", "indirect_discriminating_fixture",
    "environment_or_permission_enforced", "harness_isolation_enforced",
    "static_policy_enforced", "not_enforced", "cannot_determine",
})
ALIGNMENT_STATUS = frozenset({
    "aligned", "untested_requirement", "partially_tested_requirement",
    "unstated_test_behavior", "unsupported_environment_inference",
    "golden_solution_violation", "golden_solution_incomplete",
    "environment_unsupported", "ambiguous_contract", "cannot_determine",
})
GOLDEN_CONFORMANCE_STATUS = frozenset({
    "conformant", "nonconformant", "partially_conformant", "not_implemented", "cannot_determine",
    # legacy aliases kept for back-compat with v1.1 output
    "golden_solution_violation", "golden_solution_incomplete",
})
# golden statuses that are material (drive a golden_solution_contract_violation candidate)
GOLDEN_NONCONFORMANT = frozenset({
    "nonconformant", "partially_conformant", "not_implemented",
    "golden_solution_violation", "golden_solution_incomplete",
})
COVERAGE_STATUS = frozenset({"covered", "partially_covered", "not_covered", "not_applicable", "cannot_determine"})
DISCRIMINATING_STATUS = frozenset({
    "discriminating", "restricted_implementation_can_pass",
    "likely_restricted_implementation_can_pass", "cannot_determine",
})
YES_NO_PARTIAL = frozenset({"yes", "no", "partial", "unclear"})
# alignment statuses that are material misalignments (drive candidate synthesis)
MISALIGNED_STATUSES = frozenset({
    "untested_requirement", "partially_tested_requirement", "unstated_test_behavior",
    "unsupported_environment_inference", "golden_solution_violation",
    "golden_solution_incomplete", "environment_unsupported",
})


def _b(val, default: bool = False) -> bool:
    return val if isinstance(val, bool) else default
CANDIDATE_STATUS = frozenset({"confirmed", "supported", "potential", "refuted", "duplicate"})
SEVERITY = frozenset({"major", "minor", "potential_major"})
AGENT_CAN_MODIFY = frozenset({"yes", "no", "uncertain"})
ROUTING = frozenset({"block", "human_review", "static_pass"})
SOURCE_PASS = frozenset({"mapper", "golden_conformance", "breaker", "coverage_critic",
                         "deterministic", "edge_case", "discriminating", "phase_trust", "adjudication"})

# Provenance drives the status ladder (see adjudication.compute_status). This is
# the SOLE input to whether something can be `confirmed` -- the model proposes a
# provenance, code verifies its preconditions.
PROVENANCE = frozenset({
    "executed_exploit",       # a real attack was run and passed the verifier (NOT possible in v1)
    "deterministic_scanner",  # a deterministic_checks pattern fired (confirmable)
    "literal_contradiction",  # instruction<->verifier textual contradiction WITH >=2 cited paths
    "fully_traced_static",    # a complete static exploit path with no unresolved runtime assumption
    "runtime_dependent",      # exploit hinges on an unproven runtime/env assumption
    "incomplete_source_context",  # a material dependency was truncated/omitted/summarized (code-only cap)
    "speculative",            # weakest: no traced path (default cap)
})

# how a source dependency's evidence reached the pass
EVIDENCE_MODE = frozenset({"verbatim", "chunked", "summarized", "reconstructed", "inferred"})
# source-record transformation states (source ledger)
SOURCE_TRANSFORM = frozenset({
    "verbatim", "verbatim_prefix", "chunked_complete", "summary",
    "omitted", "reexpanded", "unreadable",
})

WITNESS_TYPE = frozenset({
    "invalid_but_accepted", "valid_but_rejected", "partial_pass",
    "format_ok_semantics_wrong", "hardcoded_output", "leaked_answer",
    "expected_output_modification", "import_shadowing", "symlink_path",
    "parser_confusion", "numeric_coercion", "nan_inf", "tolerance_boundary",
    "missing_or_extra_fields", "duplicate_records", "ordering", "empty_io",
    "stale_state", "writable_trusted_file", "process_interference",
    "mutable_dependency", "nondeterministic", "unstated_requirement",
    "unverified_requirement", "golden_solution_contract_violation",
    "restricted_implementation_passes", "other",
})

REFUTATION_TYPE = frozenset({
    "requirement_explicitly_stated",           # the allegedly-missing requirement IS present
    "proposed_valid_submission_violates_instruction",
    "verifier_path_rejects_invalid_submission",
    "asset_concretely_protected",              # the supposedly-agent-controlled asset is protected
    "capability_demonstrably_present",
    "witness_contains_specific_error",         # a concrete logical/factual error in the witness
})


def coerce_enum(val: Any, allowed: frozenset, default: str) -> str:
    """Return val if it is an allowed enum member (case/spacing-normalized), else default."""
    if not isinstance(val, str):
        return default
    v = val.strip().lower().replace(" ", "_").replace("-", "_")
    return v if v in allowed else default


def _s(val: Any, default: str = "") -> str:
    return val.strip() if isinstance(val, str) else default


def _list_of_str(val: Any) -> list[str]:
    if isinstance(val, list):
        return [x.strip() for x in val if isinstance(x, str) and x.strip()]
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def _evidence_list(val: Any) -> list[dict]:
    """Evidence entries are {file, location, detail}. Coerce loosely, keep only usable ones."""
    out: list[dict] = []
    if not isinstance(val, list):
        return out
    for e in val:
        if isinstance(e, dict):
            out.append({
                "file": _s(e.get("file")),
                "location": _s(e.get("location") or e.get("symbol_or_lines") or e.get("lines")),
                "detail": _s(e.get("detail") or e.get("note") or e.get("description")),
            })
        elif isinstance(e, str) and e.strip():
            out.append({"file": "", "location": "", "detail": e.strip()})
    return out


# ------------------------------------------------------------ dataclasses -----
@dc.dataclass
class VerifierSummary:
    entrypoints: list[str] = dc.field(default_factory=list)
    reward_derivation: str = ""
    effective_user: str = "unknown"
    agent_controlled_inputs: list[str] = dc.field(default_factory=list)
    trusted_inputs: list[str] = dc.field(default_factory=list)
    external_dependencies: list[str] = dc.field(default_factory=list)

    @staticmethod
    def from_obj(o: Any) -> "VerifierSummary":
        o = o if isinstance(o, dict) else {}
        return VerifierSummary(
            entrypoints=_list_of_str(o.get("entrypoints")),
            reward_derivation=_s(o.get("reward_derivation")),
            effective_user=_s(o.get("effective_user"), "unknown"),
            agent_controlled_inputs=_list_of_str(o.get("agent_controlled_inputs")),
            trusted_inputs=_list_of_str(o.get("trusted_inputs")),
            external_dependencies=_list_of_str(o.get("external_dependencies")),
        )


@dc.dataclass
class AcceptanceCondition:
    condition_id: str
    condition: str
    implementation: dict = dc.field(default_factory=dict)   # {file, symbol_or_lines}
    inputs: list[str] = dc.field(default_factory=list)
    input_trust: str = "unknown"
    coverage_strength: str = "unknown"
    necessity: str = "unknown"

    @staticmethod
    def from_obj(o: Any, idx: int) -> Optional["AcceptanceCondition"]:
        if not isinstance(o, dict):
            return None
        impl = o.get("implementation") if isinstance(o.get("implementation"), dict) else {}
        return AcceptanceCondition(
            condition_id=_s(o.get("condition_id"), f"A{idx+1}"),
            condition=_s(o.get("condition")),
            implementation={"file": _s(impl.get("file")),
                            "symbol_or_lines": _s(impl.get("symbol_or_lines") or impl.get("lines"))},
            inputs=_list_of_str(o.get("inputs")),
            input_trust=coerce_enum(o.get("input_trust"), INPUT_TRUST, "unknown"),
            coverage_strength=coerce_enum(o.get("coverage_strength"), COVERAGE_STRENGTH, "unknown"),
            necessity=coerce_enum(o.get("necessity"), NECESSITY, "unknown"),
        )


@dc.dataclass
class RequirementCoverage:
    requirement_id: str
    requirement: str
    instruction_evidence: str = ""
    verifier_evidence: str = ""
    status: str = "cannot_determine"
    direction: str = "instruction_to_verifier"   # or verifier_to_instruction
    authorized_by_instruction: str = "unclear"   # yes|no|unclear (for verifier_to_instruction rows)
    edge_cases_considered: list[str] = dc.field(default_factory=list)

    @staticmethod
    def from_obj(o: Any, idx: int) -> Optional["RequirementCoverage"]:
        if not isinstance(o, dict):
            return None
        d = _s(o.get("direction"), "instruction_to_verifier").lower()
        if d not in ("instruction_to_verifier", "verifier_to_instruction"):
            d = "instruction_to_verifier"
        return RequirementCoverage(
            requirement_id=_s(o.get("requirement_id"), f"R{idx+1}"),
            requirement=_s(o.get("requirement")),
            instruction_evidence=_s(o.get("instruction_evidence")),
            verifier_evidence=_s(o.get("verifier_evidence")),
            status=coerce_enum(o.get("status"), REQUIREMENT_STATUS, "cannot_determine"),
            direction=d,
            authorized_by_instruction=coerce_enum(o.get("authorized_by_instruction"), AUTHORIZED, "unclear"),
            edge_cases_considered=_list_of_str(o.get("edge_cases_considered")),
        )


@dc.dataclass
class TrustBoundaryItem:
    asset: str
    trusted_by: str = ""
    created_by: str = ""
    agent_can_modify: str = "uncertain"
    integrity_protection: str = ""
    risk: str = ""

    @staticmethod
    def from_obj(o: Any) -> Optional["TrustBoundaryItem"]:
        if not isinstance(o, dict) or not _s(o.get("asset")):
            return None
        return TrustBoundaryItem(
            asset=_s(o.get("asset")),
            trusted_by=_s(o.get("trusted_by")),
            created_by=_s(o.get("created_by")),
            agent_can_modify=coerce_enum(o.get("agent_can_modify"), AGENT_CAN_MODIFY, "uncertain"),
            integrity_protection=_s(o.get("integrity_protection")),
            risk=_s(o.get("risk")),
        )


@dc.dataclass
class Witness:
    type: str = "other"
    submission_or_condition: str = ""
    expected_contract_result: str = ""
    predicted_verifier_result: str = ""
    execution_plan: str = ""

    @staticmethod
    def from_obj(o: Any) -> "Witness":
        o = o if isinstance(o, dict) else {}
        return Witness(
            type=coerce_enum(o.get("type"), WITNESS_TYPE, "other"),
            submission_or_condition=_s(o.get("submission_or_condition")),
            expected_contract_result=_s(o.get("expected_contract_result")),
            predicted_verifier_result=_s(o.get("predicted_verifier_result")),
            execution_plan=_s(o.get("execution_plan")),
        )


@dc.dataclass
class Refutation:
    refutation_type: str
    contrary_evidence: dict = dc.field(default_factory=dict)   # {file, location, detail}

    @staticmethod
    def from_obj(o: Any) -> Optional["Refutation"]:
        """A refutation is only structurally valid with an allowed type AND cited
        contrary evidence (file + detail). Returns None for anything softer -- the
        caller then KEEPS the candidate at its prior status (fail-closed)."""
        if not isinstance(o, dict):
            return None
        rtype = coerce_enum(o.get("refutation_type"), REFUTATION_TYPE, "")
        if not rtype:
            return None
        ev = o.get("contrary_evidence")
        ev = ev if isinstance(ev, dict) else {}
        ce = {"file": _s(ev.get("file")), "location": _s(ev.get("location")), "detail": _s(ev.get("detail"))}
        if not ce["detail"] or not ce["file"]:
            return None   # concrete evidence (a cited path + a detail) is mandatory
        return Refutation(refutation_type=rtype, contrary_evidence=ce)


@dc.dataclass
class SourceDependency:
    """A source artifact a candidate needs to PROVE its claim, plus whether the
    generating pass actually received it COMPLETE. Code (not the model) uses these
    to cap provenance when a proof rests on truncated/omitted/summarized source."""
    file: str = ""
    required_for_claim: bool = True
    ranges_used: list = dc.field(default_factory=list)        # [[start,end],...] char ranges (optional)
    complete_relevant_corpus: bool = True
    evidence_mode: str = "verbatim"                            # EVIDENCE_MODE
    pass_name: str = ""

    @staticmethod
    def from_obj(o: Any) -> Optional["SourceDependency"]:
        if not isinstance(o, dict):
            return None
        rngs = []
        for r in (o.get("ranges_used") or []):
            if isinstance(r, (list, tuple)) and len(r) == 2:
                try:
                    rngs.append([int(r[0]), int(r[1])])
                except Exception:
                    pass
        return SourceDependency(
            file=_s(o.get("file")),
            required_for_claim=_b(o.get("required_for_claim"), True),
            ranges_used=rngs,
            complete_relevant_corpus=_b(o.get("complete_relevant_corpus"), True),
            evidence_mode=coerce_enum(o.get("evidence_mode"), EVIDENCE_MODE, "verbatim"),
            pass_name=_s(o.get("pass") or o.get("pass_name")),
        )


@dc.dataclass
class SourceRecord:
    """One row of the artifact-level source ledger (see task_loader). The single
    source of truth for what the loader actually included vs omitted."""
    file: str
    source_kind: str = "other"        # test|solution|instruction|support|environment|task_toml|dockerfile|other
    total_bytes: int = 0
    total_chars: int = 0
    included_bytes: int = 0
    included_chars: int = 0
    complete: bool = True
    included_ranges: list = dc.field(default_factory=list)     # char ranges [[s,e],...]
    omitted_ranges: list = dc.field(default_factory=list)
    transformation: str = "verbatim"  # SOURCE_TRANSFORM
    reason: str = ""
    content_hash: str = ""
    included_content_hash: str = ""
    chunks: list = dc.field(default_factory=list)   # [{"id":.., "start":.., "end":..}]


@dc.dataclass
class RenderedSourceRef:
    """What a single pass's FINAL rendered prompt actually contained for one file."""
    file: str
    source_kind: str = "other"
    full_hash: str = ""
    included_ranges: list = dc.field(default_factory=list)
    evidence_mode: str = "verbatim"
    included_chars: int = 0
    omitted_chars: int = 0
    complete: bool = True
    prompt_section: str = ""
    post_budget_truncated: bool = False


@dc.dataclass
class RenderedSourceManifest:
    """Completeness of the FINAL prompt submitted to one LLM pass (not just the
    TaskPackage contents). Persisted into the audit JSON + metrics."""
    pass_name: str
    prompt_hash: str = ""
    complete: bool = True
    sources: list = dc.field(default_factory=list)   # list[RenderedSourceRef]


@dc.dataclass
class ConflictRecord:
    """A deterministic record of two+ candidates making contradictory claims. Raw
    candidates are preserved; the conflict decides which may keep Major weight."""
    conflict_id: str
    kind: str = ""                     # absence_vs_presence|single_vs_multi|complete_vs_truncated|mutually_exclusive
    member_candidate_ids: list = dc.field(default_factory=list)
    subject: str = ""
    winner_candidate_id: str = ""
    loser_candidate_id: str = ""
    resolution: str = ""               # refuted_loser|downgraded_all|unresolved_human_review
    detail: str = ""
    material: bool = True


@dc.dataclass
class CandidateIssue:
    issue_id: str
    source_passes: list[str] = dc.field(default_factory=list)
    status: str = "potential"
    severity: str = "potential_major"
    provenance: str = "speculative"
    claim: str = ""
    evidence: list[dict] = dc.field(default_factory=list)     # [{file, location, detail}]
    failure_mode: str = ""
    witness: Witness = dc.field(default_factory=Witness)
    assumptions: list[str] = dc.field(default_factory=list)
    refutation: Optional[Refutation] = None
    # provenance-claimed-but-unproven: recorded so a downgrade is auditable
    claimed_provenance: str = ""
    # source-completeness (code-owned): dependencies + absence-claim accounting
    source_dependencies: list = dc.field(default_factory=list)   # list[SourceDependency]
    absence_claim: bool = False
    absence_scope: str = ""
    absence_search_complete: bool = False
    completeness_capped: bool = False    # set True by the code-computed provenance cap

    @staticmethod
    def from_obj(o: Any, idx: int, source_pass: str) -> Optional["CandidateIssue"]:
        """Parse ONE candidate. Returns None only when the record is unusable
        (no claim AND no witness) -- the caller converts that into a coverage gap
        rather than dropping it silently."""
        if not isinstance(o, dict):
            return None
        claim = _s(o.get("claim"))
        witness = Witness.from_obj(o.get("witness"))
        if not claim and not (witness.submission_or_condition or witness.execution_plan):
            return None
        claimed = coerce_enum(o.get("provenance"), PROVENANCE, "speculative")
        srcs = _list_of_str(o.get("source_passes")) or [source_pass]
        srcs = [coerce_enum(s, SOURCE_PASS, "") or s for s in srcs]
        return CandidateIssue(
            issue_id=_s(o.get("issue_id"), f"{source_pass[:1].upper()}{idx+1}"),
            source_passes=srcs,
            # NOTE: status/severity are recomputed by adjudication from provenance;
            # the model's own values are advisory only.
            status="potential",
            severity=coerce_enum(o.get("severity"), SEVERITY, "potential_major"),
            provenance=claimed,
            claimed_provenance=claimed,
            claim=claim,
            evidence=_evidence_list(o.get("evidence")),
            failure_mode=_s(o.get("failure_mode")) or _s(witness.type),
            witness=witness,
            assumptions=_list_of_str(o.get("assumptions")),
            refutation=Refutation.from_obj(o.get("refutation_evidence") or o.get("refutation")),
            source_dependencies=[d for d in (SourceDependency.from_obj(x)
                                             for x in (o.get("source_dependencies") or [])) if d is not None],
            absence_claim=_b(o.get("absence_claim")),
            absence_scope=_s(o.get("absence_scope")),
            absence_search_complete=_b(o.get("absence_search_complete")),
        )


@dc.dataclass
class CandidateCluster:
    cluster_id: str
    member_candidate_ids: list[str] = dc.field(default_factory=list)
    normalized_failure_mode: str = ""
    combined_status: str = "potential"


@dc.dataclass
class CoverageGap:
    area: str
    reason_not_resolved: str = ""
    risk_if_wrong: str = ""
    recommended_probe: str = ""
    material: bool = True

    @staticmethod
    def from_obj(o: Any) -> Optional["CoverageGap"]:
        if isinstance(o, str) and o.strip():
            return CoverageGap(area=o.strip())
        if not isinstance(o, dict) or not _s(o.get("area")):
            return None
        mat = o.get("material")
        return CoverageGap(
            area=_s(o.get("area")),
            reason_not_resolved=_s(o.get("reason_not_resolved")),
            risk_if_wrong=_s(o.get("risk_if_wrong")),
            recommended_probe=_s(o.get("recommended_probe")),
            material=(True if not isinstance(mat, bool) else mat),
        )


@dc.dataclass
class ArtifactAlignmentRecord:
    """Four-way alignment: instruction -> environment -> golden solution -> verifier/fixtures."""
    requirement_id: str
    normalized_requirement: str = ""
    instruction_evidence: str = ""
    requirement_provenance: str = "cannot_determine"
    inference_source: str = ""
    environment_support: str = "cannot_determine"
    environment_evidence: str = ""
    golden_solution_behavior: str = ""
    golden_solution_evidence: str = ""
    verifier_coverage: str = "cannot_determine"      # REQUIREMENT_STATUS vocabulary
    verifier_evidence: str = ""
    fixtures: list[str] = dc.field(default_factory=list)
    edge_cases_tested: list[str] = dc.field(default_factory=list)
    enforcement_mode: str = "cannot_determine"
    alignment_status: str = "cannot_determine"
    candidate_issue_ids: list[str] = dc.field(default_factory=list)

    @staticmethod
    def from_obj(o: Any, idx: int) -> Optional["ArtifactAlignmentRecord"]:
        if not isinstance(o, dict):
            return None
        return ArtifactAlignmentRecord(
            requirement_id=_s(o.get("requirement_id"), f"AR{idx+1}"),
            normalized_requirement=_s(o.get("normalized_requirement") or o.get("requirement")),
            instruction_evidence=_s(o.get("instruction_evidence")),
            requirement_provenance=coerce_enum(o.get("requirement_provenance"), REQUIREMENT_PROVENANCE, "cannot_determine"),
            inference_source=_s(o.get("inference_source")),
            environment_support=coerce_enum(o.get("environment_support"), ENVIRONMENT_SUPPORT, "cannot_determine"),
            environment_evidence=_s(o.get("environment_evidence")),
            golden_solution_behavior=_s(o.get("golden_solution_behavior")),
            golden_solution_evidence=_s(o.get("golden_solution_evidence")),
            verifier_coverage=coerce_enum(o.get("verifier_coverage"), REQUIREMENT_STATUS, "cannot_determine"),
            verifier_evidence=_s(o.get("verifier_evidence")),
            fixtures=_list_of_str(o.get("fixtures")),
            edge_cases_tested=_list_of_str(o.get("edge_cases_tested")),
            enforcement_mode=coerce_enum(o.get("enforcement_mode"), ENFORCEMENT_MODE, "cannot_determine"),
            alignment_status=coerce_enum(o.get("alignment_status"), ALIGNMENT_STATUS, "cannot_determine"),
            candidate_issue_ids=_list_of_str(o.get("candidate_issue_ids")),
        )


@dc.dataclass
class GoldenSolutionConformanceRecord:
    requirement_id: str
    required_behavior: str = ""
    required_operation: str = ""
    instruction_evidence: str = ""
    golden_code_location: str = ""
    solution_file: str = ""
    solution_symbol_or_lines: str = ""
    actual_operation: str = ""
    operation_boundary: str = ""
    # Part A operation + data-flow trace (contract op -> entrypoint -> call site ->
    # arguments -> downstream dependency -> postprocessing). Advisory; code decides.
    operation_sensitive: str = "unclear"        # yes|no|partial|unclear
    required_call_or_effect: str = ""
    required_argument_constraints: str = ""
    actual_call_site: str = ""
    actual_call: str = ""
    actual_argument_flow: str = ""
    downstream_dependency: str = ""
    postprocessing: str = ""
    follows_semantics: str = "unclear"          # yes|no|partial|unclear
    omissions: list[str] = dc.field(default_factory=list)
    hidden_or_privileged_inputs: list[str] = dc.field(default_factory=list)
    hardcoded_rules: list[str] = dc.field(default_factory=list)
    works_only_due_to_incomplete_verifier: bool = False
    wrong_operation_then_repairs_output: bool = False
    verifier_would_catch: str = "unclear"       # yes|no|unclear
    # required-vs-actual operation comparison + counterfactual (v1.2 hardening)
    operation_semantically_equivalent: str = "unclear"   # yes|no|partial|unclear
    actual_operation_matches_required: str = "unclear"   # yes|no|partial|unclear
    postprocessing_after_operation: str = ""
    postprocessing_used_to_mask_difference: bool = False
    counterfactual_environment: str = ""
    counterfactual_required_outcome: str = ""
    counterfactual_actual_outcome: str = ""
    difference_observable_under: str = ""
    # code recomputes this from the two outcomes; the model's value is advisory only
    counterfactual_is_distinguishing: str = "unclear"    # yes|no|partial|unclear
    equivalence_proof: str = ""
    universal_equivalence_proof: str = ""
    contract_equivalence_evidence: str = ""
    equivalence_assumptions: list[str] = dc.field(default_factory=list)
    verifier_has_discriminating_fixture: str = "unclear"  # yes|no|unclear
    minimal_discriminating_fixture: str = ""
    status: str = "cannot_determine"
    severity: str = ""
    explanation: str = ""
    evidence: list[dict] = dc.field(default_factory=list)
    candidate_issue_ids: list[str] = dc.field(default_factory=list)

    @staticmethod
    def from_obj(o: Any, idx: int) -> Optional["GoldenSolutionConformanceRecord"]:
        if not isinstance(o, dict):
            return None
        return GoldenSolutionConformanceRecord(
            requirement_id=_s(o.get("requirement_id"), f"GS{idx+1}"),
            required_behavior=_s(o.get("required_behavior")),
            required_operation=_s(o.get("required_operation")),
            instruction_evidence=_s(o.get("instruction_evidence")),
            golden_code_location=_s(o.get("golden_code_location")),
            solution_file=_s(o.get("solution_file")),
            solution_symbol_or_lines=_s(o.get("solution_symbol_or_lines")),
            actual_operation=_s(o.get("actual_operation")),
            operation_boundary=_s(o.get("operation_boundary")),
            operation_sensitive=coerce_enum(o.get("operation_sensitive"), YES_NO_PARTIAL, "unclear"),
            required_call_or_effect=_s(o.get("required_call_or_effect")),
            required_argument_constraints=_s(o.get("required_argument_constraints")),
            actual_call_site=_s(o.get("actual_call_site")),
            actual_call=_s(o.get("actual_call")),
            actual_argument_flow=_s(o.get("actual_argument_flow")),
            downstream_dependency=_s(o.get("downstream_dependency")),
            postprocessing=_s(o.get("postprocessing")),
            follows_semantics=coerce_enum(o.get("follows_semantics"), YES_NO_PARTIAL, "unclear"),
            omissions=_list_of_str(o.get("omissions")),
            hidden_or_privileged_inputs=_list_of_str(o.get("hidden_or_privileged_inputs")),
            hardcoded_rules=_list_of_str(o.get("hardcoded_rules")),
            works_only_due_to_incomplete_verifier=_b(o.get("works_only_due_to_incomplete_verifier")),
            wrong_operation_then_repairs_output=_b(o.get("wrong_operation_then_repairs_output")),
            verifier_would_catch=coerce_enum(o.get("verifier_would_catch"), YES_NO_PARTIAL, "unclear"),
            operation_semantically_equivalent=coerce_enum(o.get("operation_semantically_equivalent"), YES_NO_PARTIAL, "unclear"),
            actual_operation_matches_required=coerce_enum(o.get("actual_operation_matches_required"), YES_NO_PARTIAL, "unclear"),
            postprocessing_after_operation=_s(o.get("postprocessing_after_operation")),
            postprocessing_used_to_mask_difference=_b(o.get("postprocessing_used_to_mask_difference")),
            counterfactual_environment=_s(o.get("counterfactual_environment")),
            counterfactual_required_outcome=_s(o.get("counterfactual_required_outcome")),
            counterfactual_actual_outcome=_s(o.get("counterfactual_actual_outcome")),
            difference_observable_under=_s(o.get("difference_observable_under")),
            counterfactual_is_distinguishing=coerce_enum(o.get("counterfactual_is_distinguishing"), YES_NO_PARTIAL, "unclear"),
            equivalence_proof=_s(o.get("equivalence_proof")),
            universal_equivalence_proof=_s(o.get("universal_equivalence_proof")),
            contract_equivalence_evidence=_s(o.get("contract_equivalence_evidence")),
            equivalence_assumptions=_list_of_str(o.get("equivalence_assumptions")),
            verifier_has_discriminating_fixture=coerce_enum(o.get("verifier_has_discriminating_fixture"), YES_NO_PARTIAL, "unclear"),
            minimal_discriminating_fixture=_s(o.get("minimal_discriminating_fixture")),
            status=coerce_enum(o.get("status"), GOLDEN_CONFORMANCE_STATUS, "cannot_determine"),
            severity=_s(o.get("severity")),
            explanation=_s(o.get("explanation")),
            evidence=_evidence_list(o.get("evidence")),
            candidate_issue_ids=_list_of_str(o.get("candidate_issue_ids")),
        )


@dc.dataclass
class EdgeCaseCoverageRecord:
    requirement_id: str
    edge_dimension: str = ""
    applicability: str = "cannot_determine"      # applicable|not_applicable|cannot_determine
    fixtures_exercising_it: list[str] = dc.field(default_factory=list)
    assertions_exercising_it: list[str] = dc.field(default_factory=list)
    observed_values: list[str] = dc.field(default_factory=list)
    minimum_cardinality: int = 0
    maximum_cardinality: int = 0
    distinct_value_count: int = 0
    non_default_value_count: int = 0
    coverage_status: str = "cannot_determine"
    missing_case: str = ""
    minimal_proposed_fixture: str = ""
    restricted_implementation: str = ""
    predicted_failure_mode: str = ""
    execution_plan: str = ""


@dc.dataclass
class FixtureDiversityFinding:
    requirement_id: str
    degeneracy_type: str = ""                     # e.g. singleton_collection, default_only, matching_only
    detail: str = ""
    evidence: list[dict] = dc.field(default_factory=list)
    restricted_implementation: str = ""
    candidate_issue_ids: list[str] = dc.field(default_factory=list)


@dc.dataclass
class DiscriminatingTestAnalysis:
    requirement_id: str
    required_general_behavior: str = ""
    restricted_implementation: str = ""
    why_it_violates_the_contract: str = ""
    fixtures_that_fail_to_distinguish_it: list[str] = dc.field(default_factory=list)
    verifier_path_that_still_passes: str = ""
    minimal_new_fixture: str = ""
    predicted_expected_result: str = ""
    predicted_restricted_result: str = ""
    execution_plan: str = ""
    analysis_status: str = "cannot_determine"


@dc.dataclass
class PhaseAvailability:
    asset: str
    available_agent_phase: str = "unclear"
    available_verifier_phase: str = "unclear"
    readable_by_agent_code_at_verify: str = "unclear"
    writable_agent_phase: str = "unclear"
    writable_verifier_phase: str = "unclear"
    protected_by: str = ""
    notes: str = ""


@dc.dataclass
class ImportResolutionRisk:
    module_or_asset: str
    importable_normal_resolution: str = "unclear"
    importable_via_syspath: str = "unclear"
    shadowable_via_app_cwd_pythonpath: str = "unclear"
    loadable_via_importlib: str = "unclear"
    risk: str = ""
    detail: str = ""
    candidate_issue_ids: list[str] = dc.field(default_factory=list)


@dc.dataclass
class AuditResult:
    routing: str = "human_review"            # block | human_review | static_pass (FINAL, materiality-gated)
    confirmed_major_count: int = 0
    supported_major_count: int = 0
    potential_major_count: int = 0
    minor_count: int = 0
    material_coverage_gaps: int = 0
    material_parse_failures: int = 0
    taxonomy_coverage_complete: bool = False
    unresolved_material_gaps: bool = True
    execution_validation_complete: bool = False    # ALWAYS False in read-only v1
    static_audit_passed: bool = False              # replaces safe_to_accept; static_pass only
    reason: str = ""
    # ---- materiality/ownership gate (debug/audit trail; NEVER a new terminal route --
    # the only terminal routes remain {block, human_review, static_pass}) ----
    materiality_applied: bool = False
    pre_materiality_routing: str = ""       # the OLD v1 policy's routing, for debugging/comparison only
    pre_materiality_reason: str = ""
    blocking_eligible_ids: list = dc.field(default_factory=list)
    review_ids: list = dc.field(default_factory=list)
    deep_review_ids: list = dc.field(default_factory=list)
    advisory_ids: list = dc.field(default_factory=list)
    downgraded_ids: list = dc.field(default_factory=list)


@dc.dataclass
class AuditReport:
    task_id: str
    schema_version: str = "ava-1"
    model: str = ""
    verifier_summary: VerifierSummary = dc.field(default_factory=VerifierSummary)
    acceptance_conditions: list[AcceptanceCondition] = dc.field(default_factory=list)
    requirement_coverage: list[RequirementCoverage] = dc.field(default_factory=list)
    trust_boundary: list[TrustBoundaryItem] = dc.field(default_factory=list)
    candidate_issues: list[CandidateIssue] = dc.field(default_factory=list)
    candidate_clusters: list[CandidateCluster] = dc.field(default_factory=list)
    coverage_gaps: list[CoverageGap] = dc.field(default_factory=list)
    artifact_alignment: list = dc.field(default_factory=list)
    golden_conformance: list = dc.field(default_factory=list)
    edge_case_coverage: list = dc.field(default_factory=list)
    fixture_diversity: list = dc.field(default_factory=list)
    discriminating_tests: list = dc.field(default_factory=list)
    phase_availability: list = dc.field(default_factory=list)
    import_resolution_risks: list = dc.field(default_factory=list)
    audit_result: AuditResult = dc.field(default_factory=AuditResult)
    metrics: dict = dc.field(default_factory=dict)
    loader_manifest: dict = dc.field(default_factory=dict)
    source_ledger: list = dc.field(default_factory=list)              # list[SourceRecord]
    rendered_source_manifests: list = dc.field(default_factory=list)  # list[RenderedSourceManifest]
    conflicts: list = dc.field(default_factory=list)                  # list[ConflictRecord]

    def to_dict(self) -> dict:
        d = dc.asdict(self)
        # refutation of None serializes as null already; nothing else special.
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# --------------------------------------------------- LLM JSON extraction ------
def extract_json_block(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply.

    Tolerates a ```json ... ``` fence, a bare fence, or a raw object. Returns a
    dict or None (None => caller triggers fail-closed handling, NOT a silent pass).
    """
    if not text:
        return None
    # fenced block first
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = []
    if m:
        candidates.append(m.group(1))
    # fall back to the widest brace span
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first:last + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


def call_llm_json(call_llm, prompt, attempts: int = 2):
    """Call the LLM and extract a JSON object, RE-ASKING on an empty/unparseable
    reply. Returns ``(raw, obj)`` where ``obj`` is a dict, or ``(raw, None)`` after
    ``attempts`` tries.

    This absorbs TRANSIENT failures -- an empty reply (a timed-out ``claude -p`` call
    returns "") or a non-empty but malformed/truncated JSON body -- that would
    otherwise make a pass fail-closed to a spurious ``parse_failure`` and BLOCK a good
    task. Reliability only: a reply that never parses still yields ``obj=None`` after
    ``attempts``, so the caller's existing fail-closed handling is unchanged
    (retry-then-fail-closed). Structural signals (zero golden records, non-distinguishing
    gaps, per-record ``parse_candidate_issues`` misses) never route through here and are
    unaffected.
    """
    raw = ""
    for _ in range(max(1, attempts)):
        raw = call_llm(prompt)
        obj = extract_json_block(raw)
        if obj is not None:
            return raw, obj
    return raw, None


def parse_candidate_issues(obj: Any, source_pass: str) -> tuple[list[CandidateIssue], int]:
    """Parse a list of candidate dicts FAIL-CLOSED.

    Returns (candidates, unparseable_count). Records that cannot be turned into a
    usable candidate are counted (never discarded): the caller converts each into
    a coverage gap and sets material_parse_failures so the task routes to human
    review. A malformed record may carry the only real Major.
    """
    out: list[CandidateIssue] = []
    bad = 0
    if isinstance(obj, dict):
        obj = obj.get("candidate_issues") or obj.get("candidates") or obj.get("issues") or []
    if not isinstance(obj, list):
        return out, (1 if obj else 0)
    for i, rec in enumerate(obj):
        c = CandidateIssue.from_obj(rec, i, source_pass)
        if c is None:
            bad += 1
        else:
            out.append(c)
    return out, bad
