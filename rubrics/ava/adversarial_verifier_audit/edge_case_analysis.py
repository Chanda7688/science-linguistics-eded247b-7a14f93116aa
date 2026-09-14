#!/usr/bin/env python3
"""Edge-case / fixture-diversity analysis (static-first, no dedicated LLM call).

DERIVES `FixtureDiversityFinding` + `EdgeCaseCoverageRecord` from the task package
(static fixture inspection) and the Breaker/Mapper outputs. Detects fixture
degeneracy that lets a restricted implementation pass. High-signal static checks
run here; an extra LLM call is only warranted when a material dimension cannot be
resolved statically (left to the orchestrator to request; not done by default).
"""
from __future__ import annotations

import re

from .schemas import CandidateIssue, FixtureDiversityFinding, Witness

# env var read by the solution/instruction (e.g. RISK_REPORTING_CUTOFF)
_ENV_READ = re.compile(r"(?:os\.environ\.get\(|os\.environ\[|getenv\()\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]")
# env var actually set to a NON-default in the verifier. Traces the concrete ways a
# test/fixture supplies a value: monkeypatch.setenv / m.setenv, os.environ[..]=,
# os.environ.update({..}), setdefault, patch.dict(os.environ, {..}), env={..}.
_ENV_SET = re.compile(
    r"setenv\(\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
    r"|environ\[\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]\s*\]\s*="
    r"|environ\.(?:update|setdefault)\(\s*\{?\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
    r"|patch\.dict\(\s*os\.environ[^)]*?['\"]([A-Z][A-Z0-9_]{2,})['\"]"
    r"|env\s*=\s*\{[^}]*['\"]([A-Z][A-Z0-9_]{2,})['\"]")


def _env_read(text: str) -> set:
    return set(_ENV_READ.findall(text))


def _env_set(text: str) -> set:
    out = set()
    for m in _ENV_SET.finditer(text):
        out |= {g for g in m.groups() if g}
    return out


def derive(task) -> tuple[list, list, list]:
    """Return (fixture_diversity_findings, edge_case_records, candidates)."""
    tests = task.tests_text()
    sol = task.solution_text()
    instr = task.instruction or ""
    findings: list[FixtureDiversityFinding] = []
    candidates: list[CandidateIssue] = []

    # --- env-override-not-varied: read by solution/instruction, never set non-default in tests ---
    unvaried = sorted((_env_read(sol) | _env_read(instr)) - _env_set(tests))
    for v in unvaried:
        findings.append(FixtureDiversityFinding(
            requirement_id=f"env:{v}", degeneracy_type="env_override_not_varied",
            detail=f"{v} is read by the solution/instruction but no test sets a non-default value",
            evidence=[{"file": "tests/", "location": v, "detail": "no setenv/env=/environ[..]= for this var"}],
            restricted_implementation=f"an implementation that ignores {v} and uses the default"))
        candidates.append(CandidateIssue(
            issue_id=f"EC-{v}", source_passes=["edge_case"], severity="potential_major",
            provenance="runtime_dependent",
            claim=f"env override {v} is never varied by the tests; an implementation that ignores it passes",
            evidence=[{"file": "tests/", "location": v, "detail": "tests use only default oracle values"}],
            failure_mode="restricted_implementation_passes",
            absence_claim=True,
            absence_scope=f"every test/fixture/monkeypatch that could set {v}",
            witness=Witness(type="restricted_implementation_passes",
                            submission_or_condition=f"implementation that ignores {v}",
                            expected_contract_result="must honor the override",
                            predicted_verifier_result="passes (default value only)",
                            execution_plan=f"set a non-default {v} and re-run the real verifier; expect reward=0")))

    # --- matching-only conditional (HTTP If-None-Match style): 304 tested only with a matching validator ---
    if re.search(r"if[-_]none[-_]match", tests, re.I) and re.search(r"\b304\b", tests):
        if not re.search(r"non[-_ ]?match|does ?not match|mismatch|wrong (etag|tag|validator)", tests, re.I):
            findings.append(FixtureDiversityFinding(
                requirement_id="conditional:if_none_match", degeneracy_type="matching_only_conditional",
                detail="conditional-request tests only use a MATCHING validator; no non-matching -> 200 case",
                evidence=[{"file": "tests/", "location": "If-None-Match", "detail": "no non-matching validator fixture"}],
                restricted_implementation="always return 304 when If-None-Match is present, without comparing tags"))
            candidates.append(CandidateIssue(
                issue_id="EC-inm", source_passes=["edge_case"], severity="potential_major",
                provenance="runtime_dependent",
                claim="conditional handling is tested only with matching validators; an always-304 implementation passes",
                evidence=[{"file": "tests/", "location": "If-None-Match", "detail": "no present-but-non-matching case expecting 200"}],
                failure_mode="restricted_implementation_passes",
                witness=Witness(type="restricted_implementation_passes",
                                submission_or_condition="return 304 for any present If-None-Match without tag comparison",
                                expected_contract_result="a non-matching validator must yield 200",
                                predicted_verifier_result="passes (no non-matching fixture)",
                                execution_plan="add a present, non-matching validator on GET/HEAD and re-run")))

    return findings, [], candidates
