#!/usr/bin/env python3
"""Pass 3 -- Coverage Critic: add candidates + explicit coverage gaps.

Receives BOTH the Mapper and Breaker outputs, hunts for what is unanalyzed and
where the two passes disagree. It only ADDS; preservation of prior candidates is
enforced in code by the orchestrator (this pass never sees a delete path).
"""
from __future__ import annotations

import dataclasses as dc
import json

from . import promptlib
from .llm import CallLLM
from .schemas import CoverageGap, call_llm_json, parse_candidate_issues
from .task_loader import TaskPackage


@dc.dataclass
class CriticResult:
    candidates: list
    coverage_gaps: list
    parse_failures: int
    raw: str
    parsed: bool


def run(task: TaskPackage, mapper_obj: dict, breaker_obj: dict, call_llm: CallLLM,
        golden_obj: dict = None) -> CriticResult:
    prompt = promptlib.render(
        "coverage_critic.md",
        task_id=task.task_id,
        instruction=task.instruction or "(none)",
        mapper_json=json.dumps(mapper_obj)[:40000] if mapper_obj else "(mapper produced no parseable output)",
        breaker_json=json.dumps(breaker_obj)[:40000] if breaker_obj else "(breaker produced no parseable output)",
        golden_json=json.dumps(golden_obj)[:20000] if golden_obj else "(golden-conformance produced no parseable output)",
        tests=task.tests_text() or "(none)",
    )
    raw, obj = call_llm_json(call_llm, prompt)
    if obj is None:
        # fail-closed: the critic itself could not be parsed. Record a material gap
        # so the task cannot silently pass on the strength of the first two passes.
        gap = CoverageGap(area="coverage_critic_unparsed",
                          reason_not_resolved="Coverage Critic output could not be parsed",
                          risk_if_wrong="untested verifier branches may remain",
                          recommended_probe="re-run the Coverage Critic pass", material=True)
        return CriticResult([], [gap], 1, raw, False)
    cands, bad = parse_candidate_issues(obj, "coverage_critic")
    gaps = []
    for g in (obj.get("coverage_gaps") or []):
        cg = CoverageGap.from_obj(g)
        if cg is not None:
            gaps.append(cg)
    return CriticResult(cands, gaps, bad, raw, True)
