#!/usr/bin/env python3
"""Requirement coverage + four-way artifact-alignment parsing.

The Mapper pass emits both the (legacy) requirement matrix and the v1.1
`ArtifactAlignmentRecord` matrix (Instruction -> Environment -> Golden solution ->
Verifier/fixtures). This module owns parsing/validation for both.
"""
from __future__ import annotations

from .schemas import (MISALIGNED_STATUSES, UNTESTED_STATUSES,
                      ArtifactAlignmentRecord, RequirementCoverage)


def parse(obj) -> list[RequirementCoverage]:
    rows = obj.get("requirement_coverage") if isinstance(obj, dict) else obj
    out: list[RequirementCoverage] = []
    if isinstance(rows, list):
        for i, r in enumerate(rows):
            rc = RequirementCoverage.from_obj(r, i)
            if rc is not None and (rc.requirement or rc.verifier_evidence):
                out.append(rc)
    return out


def parse_alignment(obj) -> list[ArtifactAlignmentRecord]:
    rows = obj.get("artifact_alignment") if isinstance(obj, dict) else obj
    out: list[ArtifactAlignmentRecord] = []
    if isinstance(rows, list):
        for i, r in enumerate(rows):
            rec = ArtifactAlignmentRecord.from_obj(r, i)
            if rec is not None and rec.normalized_requirement:
                out.append(rec)
    return out


def misaligned(records: list[ArtifactAlignmentRecord]) -> list[ArtifactAlignmentRecord]:
    """Alignment records whose status is a material misalignment (candidate source)."""
    return [r for r in records if r.alignment_status in MISALIGNED_STATUSES]


# legacy both-direction helpers (still used where the alignment matrix is absent)
def untested_requirements(rows: list[RequirementCoverage]) -> list[RequirementCoverage]:
    return [r for r in rows
            if r.direction == "instruction_to_verifier" and r.status in UNTESTED_STATUSES]


def unstated_requirements(rows: list[RequirementCoverage]) -> list[RequirementCoverage]:
    return [r for r in rows
            if r.direction == "verifier_to_instruction" and r.authorized_by_instruction == "no"]
