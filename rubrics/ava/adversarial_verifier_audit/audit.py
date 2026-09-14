#!/usr/bin/env python3
"""AVA orchestrator: the vertical slice.

    Load one task -> Mapper + Breaker (independent) -> Coverage Critic (sees both)
    -> union candidates (+ deterministic bridge) -> compute status from provenance
    -> validate refutations -> nondestructive clustering -> code-computed routing
    -> AuditReport.

Every candidate from every pass is preserved. Nothing is majority-voted away.
Parsing is fail-closed: unparsed passes become material coverage gaps + parse
failures that route the task to human review.
"""
from __future__ import annotations

import concurrent.futures as _cf
import threading as _threading
import time as _time

from . import (adjudication, candidate_clusterer, coverage_critic,
               deterministic_bridge, discriminating_test, edge_case_analysis,
               golden_conformance, requirement_mapper, source_completeness,
               trust_boundary, verifier_breaker, verifier_mapper)
from .llm import CallLLM
from .schemas import AuditReport, CandidateIssue, CoverageGap, Witness
from .task_loader import TaskPackage


# alignment_status -> (witness type, human-readable label)
_ALIGN_WITNESS = {
    "untested_requirement": ("unverified_requirement", "stated/inferable requirement with no meaningful test"),
    "partially_tested_requirement": ("unverified_requirement", "requirement only partially/degenerate-tested"),
    "unstated_test_behavior": ("unstated_requirement", "verifier enforces a behaviour absent from the contract"),
    "unsupported_environment_inference": ("other", "requirement inferred without environment support"),
    "environment_unsupported": ("other", "requirement not supported by the environment"),
    "ambiguous_contract": ("other", "contract is ambiguous for this requirement"),
}


def _alignment_candidates(alignment_records: list) -> list[CandidateIssue]:
    """Synthesize first-class findings from the four-way alignment matrix.

    These are code-synthesized from the Mapper's alignment judgment, so they are
    `runtime_dependent` -> `potential` -> human_review (never auto-blocked). Block
    escalation comes only from a fully-traced Breaker/Golden witness for the same
    issue. Golden-solution statuses are owned by the Golden-Conformance pass and
    skipped here to avoid duplicate findings.
    """
    out: list[CandidateIssue] = []
    for r in requirement_mapper.misaligned(alignment_records):
        st = r.alignment_status
        if st in ("golden_solution_violation", "golden_solution_incomplete"):
            continue
        wt, label = _ALIGN_WITNESS.get(st, ("other", st))
        out.append(CandidateIssue(
            issue_id=f"AL-{r.requirement_id}", source_passes=["mapper"],
            severity="potential_major", provenance="runtime_dependent",
            claim=f"[{st}] {label}: {r.normalized_requirement} "
                  f"(provenance={r.requirement_provenance}, enforcement={r.enforcement_mode})",
            evidence=[{"file": "instruction.md", "location": r.requirement_id,
                       "detail": r.instruction_evidence or r.normalized_requirement},
                      {"file": "tests/", "location": "coverage",
                       "detail": r.verifier_evidence or "no meaningful discriminating coverage"}],
            failure_mode=st,
            witness=Witness(type=wt,
                            submission_or_condition="an implementation that exploits this misalignment",
                            expected_contract_result="the contract-defined outcome",
                            predicted_verifier_result="accepted/rejected contrary to the contract",
                            execution_plan="construct the implementation and run the real verifier to confirm")))
    return out


def _unique_yield(candidates: list) -> dict:
    """Count candidates uniquely attributable to a single source pass."""
    out: dict[str, int] = {}
    for c in candidates:
        srcs = [s for s in c.source_passes if s]
        if len(set(srcs)) == 1:
            out[srcs[0]] = out.get(srcs[0], 0) + 1
    return out


def audit_task(task_dir: str, call_llm: CallLLM, task_id: str = "",
               model: str = "", include_deterministic: bool = True,
               parallel: bool = True, sem=None, max_inflight: int = 3) -> AuditReport:
    task = TaskPackage(task_dir, task_id)

    # Task-local, concurrency-safe LLM metrics: wrap call_llm so each invocation is
    # recorded into a PER-TASK list (correct even when one client is shared across a
    # parallel batch -- no cross-task accumulation). An optional semaphore bounds the
    # total number of in-flight LLM calls to avoid overloading the machine.
    local_calls: list[dict] = []
    _mlock = _threading.Lock()
    if sem is None and parallel:
        sem = _threading.Semaphore(max_inflight)

    def tracked(prompt: str) -> str:
        t0 = _time.time()
        if sem is not None:
            sem.acquire()
        try:
            out = call_llm(prompt)
        finally:
            if sem is not None:
                sem.release()
        with _mlock:
            local_calls.append({"prompt_chars": len(prompt), "reply_chars": len(out),
                                "approx_prompt_tokens": len(prompt) // 4,
                                "approx_reply_tokens": len(out) // 4,
                                "seconds": round(_time.time() - t0, 1)})
        return out

    # ---- four primary model calls ----
    # Mapper, Breaker, and Golden-Conformance are INDEPENDENT (each reads only the
    # task package) -> run them concurrently; the Coverage Critic depends on all
    # three so it runs afterward. Parallelism changes only timing, not inputs/results.
    if parallel:
        with _cf.ThreadPoolExecutor(max_workers=3) as ex:
            f_map = ex.submit(verifier_mapper.run, task, tracked)
            f_brk = ex.submit(verifier_breaker.run, task, tracked)
            f_gold = ex.submit(golden_conformance.run, task, tracked)
            mapper, breaker, golden = f_map.result(), f_brk.result(), f_gold.result()
    else:
        mapper = verifier_mapper.run(task, tracked)
        breaker = verifier_breaker.run(task, tracked)
        golden = golden_conformance.run(task, tracked)
    critic = coverage_critic.run(task, mapper.obj, breaker.obj, tracked, golden.obj)

    # ---- union candidates (preserve ALL) ----
    candidates = list(breaker.candidates) + list(golden.candidates) + list(critic.candidates)
    # four-way artifact-alignment misalignments as first-class findings
    candidates += _alignment_candidates(mapper.artifact_alignment)
    # static-first edge-case / fixture-diversity + phase-aware import risks
    fixture_diversity, edge_records, edge_cands = edge_case_analysis.derive(task)
    candidates += edge_cands
    import_risks, import_cands = trust_boundary.import_risks(task)
    candidates += import_cands
    if include_deterministic:
        candidates += deterministic_bridge.scan(task.task_dir)

    # ---- coverage gaps + fail-closed accounting ----
    gaps: list[CoverageGap] = list(critic.coverage_gaps)
    parse_failures = breaker.parse_failures + critic.parse_failures + golden.parse_failures
    if not mapper.parsed:
        gaps.append(CoverageGap(
            area="verifier_mapping_unparsed",
            reason_not_resolved="Mapper produced no parseable acceptance-boundary reconstruction",
            risk_if_wrong="the acceptance boundary is unknown; unseen conditions may accept bad work",
            recommended_probe="re-run the Mapper pass / inspect the verifier manually", material=True))
        parse_failures += 1
    # fail-closed: golden pass returned zero records for a nonempty solution -> never treat clean.
    # This is analogous to an unparsed pass (a required pass produced no usable output), so it
    # ALSO bumps parse_failures (the materiality gate's routing only reads blocking/review/
    # parse_failures/completeness-caps, so a bare material-gap entry would otherwise be invisible
    # to live routing).
    if getattr(golden, "zero_records", False):
        gaps.append(CoverageGap(
            area="golden_conformance_zero_records",
            reason_not_resolved="Golden-Conformance produced no per-requirement record for a nonempty solution",
            risk_if_wrong="a golden-solution contract violation may be undetected",
            recommended_probe="re-run Golden-Conformance / inspect the reference solution manually", material=True))
        parse_failures += 1
    # operation-sensitive golden records assessed without a counterfactual (fail-closed).
    # This is analogous to an incomplete/unparsed judgment (the pass could not establish
    # whether the requirement is met), so it ALSO bumps parse_failures for the same reason
    # as the zero_records case above -- the materiality gate's routing does not read the
    # generic coverage_gaps list, only blocking/review/parse_failures/completeness-caps.
    golden_nd_gaps = list(getattr(golden, "coverage_gaps", []) or [])
    gaps += golden_nd_gaps
    parse_failures += len(golden_nd_gaps)
    # agent-writable trusted assets as gaps (requirement<->test misalignments are
    # emitted as first-class candidate findings above, not gaps)
    for t in trust_boundary.agent_writable(mapper.trust_boundary):
        gaps.append(CoverageGap(
            area=f"trust:{t.asset[:60]}",
            reason_not_resolved=f"trusted asset '{t.asset[:60]}' is agent-modifiable ({t.agent_can_modify}) without integrity protection",
            risk_if_wrong="agent could tamper with a value the verifier trusts",
            recommended_probe="overlay a mutated asset and re-run the real verifier", material=True))

    # ---- status from provenance (code decides), then typed refutations ----
    for c in candidates:
        adjudication.compute_status(c)
    adjudication.apply_refutations(candidates)

    # ---- source-completeness safety (code-computed, AFTER status/refutation, BEFORE
    # clustering/routing so nothing can re-promote): tag each candidate's source
    # dependencies, cap supported/confirmed claims that rest on incomplete source or
    # on an unproven absence, then adjudicate absence-vs-presence conflicts.
    source_completeness.tag_source_dependencies(candidates, task)
    source_completeness.apply_provenance_caps(candidates, task, gaps)
    conflicts = source_completeness.adjudicate_conflicts(candidates, gaps)
    rendered_source_manifests = source_completeness.build_pass_manifests(task)

    # ---- discriminating-test records (derived from finalized candidates) ----
    discriminating_records = discriminating_test.derive(candidates)

    # ---- nondestructive clustering ----
    clusters = candidate_clusterer.cluster(candidates)

    # ---- taxonomy coverage: every pass produced parseable output ----
    taxonomy_complete = (mapper.parsed and breaker.parsed and golden.parsed
                         and critic.parsed and parse_failures == 0)

    # ---- routing (fail-closed, computed in code): the materiality + ownership gate
    # is the FINAL authority; adjudication.route()'s legacy "any supported Major
    # blocks" computation is preserved only as a pre_materiality_routing debug field.
    result = adjudication.route_with_materiality(candidates, gaps, parse_failures,
                                                  taxonomy_complete, golden.records)

    # ---- metrics / calibration hooks ----
    by_source: dict[str, int] = {}
    for c in candidates:
        for s in c.source_passes:
            by_source[s] = by_source.get(s, 0) + 1
    metrics = {
        "candidate_count": len(candidates),
        "candidates_by_source_pass": by_source,
        "unique_yield_by_pass": _unique_yield(candidates),
        "refuted_during_adjudication": sum(1 for c in candidates if c.status == "refuted"),
        "status_counts": {
            s: sum(1 for c in candidates if c.status == s)
            for s in ("confirmed", "supported", "potential", "refuted", "duplicate")
        },
        "parse_failures": parse_failures,
        "passes_parsed": {"mapper": mapper.parsed, "breaker": breaker.parsed,
                          "golden_conformance": golden.parsed, "critic": critic.parsed},
        "match_by": "failure_mode",   # calibration matches on failure mode, not exact category
    }
    # LLM metrics are task-local (built from the tracked wrapper), so they are correct
    # under a shared client / parallel batch -- no reliance on client-side accumulation.
    metrics["llm"] = {
        "model": model or getattr(call_llm, "model", ""),
        "llm_calls": len(local_calls),
        "approx_total_tokens": sum(c["approx_prompt_tokens"] + c["approx_reply_tokens"] for c in local_calls),
        "total_seconds": round(sum(c["seconds"] for c in local_calls), 1),
        "per_call": local_calls,
    }

    return AuditReport(
        task_id=task.task_id,
        model=model,
        verifier_summary=mapper.summary,
        acceptance_conditions=mapper.acceptance_conditions,
        requirement_coverage=mapper.requirement_coverage,
        trust_boundary=mapper.trust_boundary,
        candidate_issues=candidates,
        candidate_clusters=clusters,
        coverage_gaps=gaps,
        artifact_alignment=mapper.artifact_alignment,
        golden_conformance=golden.records,
        edge_case_coverage=edge_records,
        fixture_diversity=fixture_diversity,
        discriminating_tests=discriminating_records,
        import_resolution_risks=import_risks,
        audit_result=result,
        metrics=metrics,
        loader_manifest=task.manifest(),
        source_ledger=task.source_records(),
        rendered_source_manifests=rendered_source_manifests,
        conflicts=conflicts,
    )
