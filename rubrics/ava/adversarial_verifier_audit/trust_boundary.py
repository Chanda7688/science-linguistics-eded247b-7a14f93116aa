#!/usr/bin/env python3
"""Trust-boundary parsing + phase-aware import-resolution risk scanning.

The Mapper pass emits the trust boundary; this module owns its parsing. It also
provides a static, phase-aware scanner for import-resolution / oracle-leak /
root-`/data` risks (feeds `ImportResolutionRisk` records + candidates).
"""
from __future__ import annotations

import re

from .schemas import CandidateIssue, ImportResolutionRisk, TrustBoundaryItem, Witness


def parse(obj) -> list[TrustBoundaryItem]:
    rows = obj.get("trust_boundary") if isinstance(obj, dict) else obj
    out: list[TrustBoundaryItem] = []
    if isinstance(rows, list):
        for r in rows:
            item = TrustBoundaryItem.from_obj(r)
            if item is not None:
                out.append(item)
    return out


def agent_writable(rows: list[TrustBoundaryItem]) -> list[TrustBoundaryItem]:
    """Trusted assets the agent can (or maybe can) modify without integrity protection."""
    return [r for r in rows if r.agent_can_modify in ("yes", "uncertain") and not r.integrity_protection]


# static import/phase-risk signatures (label, regex, witness type)
_IMPORT_CHECKS = [
    ("verifier subprocess/agent code can import the oracle from /tests",
     r"sys\.path\.insert\(\s*0\s*,\s*['\"]/tests", "import_shadowing"),
    ("dynamic module load via importlib.spec_from_file_location",
     r"importlib\.util\.spec_from_file_location", "import_shadowing"),
    ("verifier imports a shadowable helper module (e.g. fixture_builders)",
     r"(?:^|\n)\s*(?:from|import)\s+fixture_builders\b", "import_shadowing"),
    ("verifier imports its oracle module (test_outputs/reference/oracle)",
     r"(?:from|import)\s+(?:test_outputs|reference|oracle)\b", "leaked_answer"),
]


def import_risks(task) -> tuple[list, list]:
    """Static, phase-aware scan of the verifier/solution for import-resolution and
    oracle-leak risks. Returns (import_resolution_risks, candidates). Candidates are
    `runtime_dependent` -> `potential` -> human_review (a fully-traced Breaker/Golden
    witness is what escalates any of these to block)."""
    text = (task.tests_text() or "") + "\n" + (task.solution_text() or "")
    risks: list[ImportResolutionRisk] = []
    candidates: list[CandidateIssue] = []
    for label, pat, wtype in _IMPORT_CHECKS:
        if re.search(pat, text, re.M):
            risks.append(ImportResolutionRisk(
                module_or_asset=label,
                importable_via_syspath="yes" if "sys.path" in pat else "unclear",
                loadable_via_importlib="yes" if "importlib" in pat else "unclear",
                shadowable_via_app_cwd_pythonpath="yes",
                risk="import shadow / oracle leak", detail=label))
            candidates.append(CandidateIssue(
                issue_id=f"IMP-{len(candidates)+1}", source_passes=["phase_trust"],
                severity="potential_major", provenance="runtime_dependent",
                claim=f"import/phase risk: {label}",
                evidence=[{"file": "tests/ or solution/", "location": "import", "detail": label}],
                failure_mode=wtype,
                witness=Witness(type=wtype,
                                submission_or_condition="agent /app module shadows the import or imports the oracle",
                                expected_contract_result="a genuine implementation is required",
                                predicted_verifier_result="bypass earns reward=1",
                                execution_plan="overlay a shadow/oracle-delegating module and re-run the real verifier")))
    # root-writable /data recomputed by the verifier (no USER in the agent Dockerfile)
    if "USER " not in (task.dockerfile or "") and re.search(r"/data\b", text):
        risks.append(ImportResolutionRisk(
            module_or_asset="/data (verifier-trusted, agent-writable as root)",
            shadowable_via_app_cwd_pythonpath="n/a",
            risk="root can mutate source evidence the verifier recomputes expectations from",
            detail="no USER in the agent Dockerfile; verifier reads /data; chmod is root-revertible"))
    return risks, candidates
