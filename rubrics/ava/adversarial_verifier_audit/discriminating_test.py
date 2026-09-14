#!/usr/bin/env python3
"""Discriminating-test analysis (derived, no dedicated LLM call).

For each candidate whose witness describes a restricted/partial/hardcoded
implementation that still passes, DERIVE a `DiscriminatingTestAnalysis` record
(the structured "could a restricted implementation pass?" answer). This produces
RECORDS only -- the underlying candidate is already the finding, so we do not add
new candidates here (avoids double-counting).
"""
from __future__ import annotations

from .schemas import DiscriminatingTestAnalysis

_RESTRICTED_WITNESS = {
    "restricted_implementation_passes", "invalid_but_accepted",
    "format_ok_semantics_wrong", "partial_pass", "hardcoded_output",
}


def derive(candidates: list) -> list:
    recs: list[DiscriminatingTestAnalysis] = []
    for c in candidates:
        if c.witness.type not in _RESTRICTED_WITNESS:
            continue
        # a fully-traced supported/confirmed finding => a restricted impl demonstrably passes;
        # a potential finding => it likely can (unresolved runtime assumption).
        status = ("restricted_implementation_can_pass"
                  if c.status in ("supported", "confirmed")
                  else "likely_restricted_implementation_can_pass")
        recs.append(DiscriminatingTestAnalysis(
            requirement_id=c.issue_id,
            required_general_behavior=c.claim[:300],
            restricted_implementation=c.witness.submission_or_condition,
            why_it_violates_the_contract=c.witness.expected_contract_result,
            fixtures_that_fail_to_distinguish_it=[e.get("location", "") for e in c.evidence if e.get("location")],
            verifier_path_that_still_passes=c.witness.predicted_verifier_result,
            minimal_new_fixture="",
            predicted_expected_result=c.witness.expected_contract_result,
            predicted_restricted_result=c.witness.predicted_verifier_result,
            execution_plan=c.witness.execution_plan,
            analysis_status=status,
        ))
    return recs
