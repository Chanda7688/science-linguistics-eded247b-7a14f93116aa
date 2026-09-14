#!/usr/bin/env python3
"""Nondestructive candidate clustering.

Unlike a dedup step, this NEVER removes a candidate. It keeps every raw record
(so a distinct witness or stronger evidence is never discarded) and layers
``CandidateCluster`` objects on top that group likely-equivalent candidates by a
normalized failure-mode signature. ``source_passes`` are unioned onto the members.

A single-pass finding therefore always survives: even if only one pass produced
it, it remains its own (singleton) cluster and stays in ``candidate_issues``.
"""
from __future__ import annotations

import re

from .schemas import CandidateCluster, CandidateIssue

_STOP = re.compile(r"[^a-z0-9]+")

# rank for choosing a cluster's combined_status (strongest wins for display only;
# the raw members keep their own statuses)
_STATUS_RANK = {"confirmed": 4, "supported": 3, "potential": 2, "duplicate": 1, "refuted": 0}


def _norm_mode(c: CandidateIssue) -> str:
    base = (c.failure_mode or c.witness.type or c.claim[:40]).lower()
    return _STOP.sub("_", base).strip("_")


def cluster(candidates: list[CandidateIssue]) -> list[CandidateCluster]:
    """Group by normalized failure mode. Returns cluster objects; does NOT mutate
    the candidate list except to union source_passes across exact-signature twins."""
    groups: dict[str, list[CandidateIssue]] = {}
    for c in candidates:
        groups.setdefault(_norm_mode(c), []).append(c)

    clusters: list[CandidateCluster] = []
    for i, (sig, members) in enumerate(sorted(groups.items())):
        # union source_passes across members (transparency; no record removed)
        all_src = sorted({s for m in members for s in m.source_passes})
        for m in members:
            m.source_passes = sorted(set(m.source_passes) | set(all_src))
        combined = max((m.status for m in members), key=lambda s: _STATUS_RANK.get(s, 0))
        clusters.append(CandidateCluster(
            cluster_id=f"CL{i+1}",
            member_candidate_ids=[m.issue_id for m in members],
            normalized_failure_mode=sig,
            combined_status=combined,
        ))
    return clusters
