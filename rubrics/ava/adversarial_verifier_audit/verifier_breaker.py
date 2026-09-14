#!/usr/bin/env python3
"""Pass 2 -- Verifier Breaker: construct concrete witnessed attacks.

Runs INDEPENDENTLY of the Mapper (works straight from the task package) so an
incorrect acceptance map cannot induce a shared blind spot.
"""
from __future__ import annotations

import dataclasses as dc

from . import promptlib
from .llm import CallLLM
from .schemas import call_llm_json, parse_candidate_issues
from .task_loader import TaskPackage
from .verifier_mapper import common_fields


@dc.dataclass
class BreakerResult:
    candidates: list
    parse_failures: int
    raw: str
    parsed: bool
    obj: dict


def run(task: TaskPackage, call_llm: CallLLM) -> BreakerResult:
    fields = common_fields(task)
    # Breaker prompt does not take solution/task_toml placeholders it doesn't use;
    # render() ignores unknown extras and leaves unused placeholders only if present.
    prompt = promptlib.render("breaker.md", **fields)
    raw, obj = call_llm_json(call_llm, prompt)
    if obj is None:
        # fail-closed: could not parse the breaker at all -> 1 material parse failure.
        return BreakerResult([], 1, raw, False, {})
    cands, bad = parse_candidate_issues(obj, "breaker")
    return BreakerResult(cands, bad, raw, True, obj)
