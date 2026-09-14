#!/usr/bin/env python3
"""Pass 1 -- Verifier Mapper: reconstruct the acceptance boundary."""
from __future__ import annotations

import dataclasses as dc
import json

from . import promptlib, requirement_mapper, trust_boundary
from .llm import CallLLM
from .schemas import (AcceptanceCondition, VerifierSummary, call_llm_json)
from .task_loader import TaskPackage


def common_fields(task: TaskPackage) -> dict:
    """Rendered prompt fields shared by all passes (single source of truth)."""
    return {
        "task_id": task.task_id,
        "instruction": task.instruction or "(none)",
        "tests": task.tests_text() or "(none)",
        "solution": task.solution_text() or "(none)",
        "dockerfile": task.dockerfile or "(none)",
        "support": task.support_text() or "(none)",
        "env_listing": "\n".join(task.environment_listing) or "(none)",
        "task_toml": task.task_toml or "(none)",
        "omitted": json.dumps(task.omitted) if task.omitted else "(none)",
    }


@dc.dataclass
class MapperResult:
    summary: VerifierSummary
    acceptance_conditions: list
    requirement_coverage: list
    trust_boundary: list
    artifact_alignment: list
    raw: str
    parsed: bool
    obj: dict


def run(task: TaskPackage, call_llm: CallLLM) -> MapperResult:
    prompt = promptlib.render("mapper.md", **common_fields(task))
    raw, obj = call_llm_json(call_llm, prompt)
    if obj is None:
        # fail-closed: no reconstruction. Empty summary; caller records a coverage gap.
        return MapperResult(VerifierSummary(), [], [], [], [], raw, False, {})
    ac = []
    for i, c in enumerate(obj.get("acceptance_conditions", []) if isinstance(obj, dict) else []):
        parsed = AcceptanceCondition.from_obj(c, i)
        if parsed is not None and parsed.condition:
            ac.append(parsed)
    return MapperResult(
        summary=VerifierSummary.from_obj(obj.get("verifier_summary")),
        acceptance_conditions=ac,
        requirement_coverage=requirement_mapper.parse(obj),
        trust_boundary=trust_boundary.parse(obj),
        artifact_alignment=requirement_mapper.parse_alignment(obj),
        raw=raw,
        parsed=True,
        obj=obj,
    )
