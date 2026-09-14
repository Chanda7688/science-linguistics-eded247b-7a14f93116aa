#!/usr/bin/env python3
"""Source-completeness safety layer (deterministic; NO LLM call).

Enforces, in code, that AVA's evidence tier reflects what each pass actually saw:

* ``build_pass_manifests`` -- a ``RenderedSourceManifest`` per primary pass, derived
  from the loader source ledger, recording exactly which artifacts (and how complete)
  reached that pass's final prompt.
* ``tag_source_dependencies`` -- attach code-derived ``SourceDependency`` records +
  absence-claim flags to every candidate from its cited evidence and the ledger.
* ``apply_provenance_caps`` -- the SAFETY CAP. A candidate that depends on incomplete
  source (truncated/omitted/summarized), or an absence claim over an incomplete
  corpus / without a concrete restricted implementation, can never remain
  ``supported``/``confirmed``: it is capped to ``potential`` + ``incomplete_source_context``
  and a material coverage gap is emitted. Runs AFTER status computation and BEFORE
  clustering/routing, so nothing can re-promote it.
* ``adjudicate_conflicts`` -- detect absence-vs-presence contradictions, refute the
  weaker claim when the presence record has complete source, otherwise downgrade both
  and route the unresolved conflict to human_review. Contradictory Majors can no
  longer independently survive.

The static-contradiction exception (a positive local contradiction proven by COMPLETE
cited code) is honored naturally: the cap only fires when the candidate's OWN cited
dependency is incomplete (or it is an absence claim), never merely because some
unrelated file in the task was truncated.
"""
from __future__ import annotations

import re

from .schemas import (ConflictRecord, CoverageGap, RenderedSourceManifest,
                      RenderedSourceRef, SourceDependency)

# which loader source_kinds each primary pass renders into its final prompt
PASS_SECTIONS = {
    "mapper": ("instruction", "test", "solution", "support", "dockerfile", "task_toml"),
    "breaker": ("instruction", "test", "solution", "support", "dockerfile"),
    "golden_conformance": ("instruction", "test", "solution", "support", "dockerfile"),
    "coverage_critic": ("instruction", "test"),
}

# witness types that constitute a concrete restricted implementation
_RESTRICTED_WITNESS = frozenset({
    "restricted_implementation_passes", "invalid_but_accepted",
    "format_ok_semantics_wrong", "partial_pass", "hardcoded_output",
})
# failure modes that are inherently absence/coverage claims
_ABSENCE_MODES = frozenset({
    "restricted_implementation_passes", "untested_requirement",
    "partially_tested_requirement", "unstated_test_behavior",
})
_ABSENCE_RE = re.compile(
    r"\b(no test|no fixture|never (?:varied|tested|set|exercised|invoked|used)|"
    r"not (?:varied|tested|exercised|covered)|only one|single (?:deterministic )?pack|"
    r"one (?:fixed )?pack|zero (?:exercising )?fixtur|untested|no (?:exercising )?fixtur|"
    r"absent|does not exist|no discriminating|no .*(?:test|fixture) (?:exercises|varies)|"
    r"every fixture is (?:singleton|degenerate))\b", re.I)

_STOP_SUBJ = frozenset({
    "tests", "fixture", "fixtures", "verifier", "output", "outputs", "values", "value",
    "requirement", "implementation", "solution", "instruction", "candidate", "parameter",
    "reward", "behavior", "behaviour", "verified", "coverage",
})


# ------------------------------------------------------------- helpers --------
def _text(c) -> str:
    w = c.witness
    return " ".join([c.claim or "", c.failure_mode or "", w.submission_or_condition or "",
                     w.expected_contract_result or "", w.predicted_verifier_result or "",
                     c.absence_scope or ""])


def is_absence_claim(c) -> bool:
    if getattr(c, "absence_claim", False):
        return True
    if (c.failure_mode or "").lower() in _ABSENCE_MODES:
        return True
    return bool(_ABSENCE_RE.search(_text(c)))


def _cited_files(c) -> list:
    files = [e.get("file", "") for e in c.evidence if isinstance(e, dict) and e.get("file")]
    files += [d.file for d in c.source_dependencies if getattr(d, "file", "")]
    return [f for f in files if f]


def _file_match(cited: str, sr) -> bool:
    a = (cited or "").strip().lower().rstrip("/")
    b = (sr.file or "").strip().lower()
    if not a or not b:
        # a bare kind reference (e.g. cited "tests/") handled below
        pass
    if a and b and (a == b or a in b or b in a):
        return True
    if a in ("tests", "test", "fixtures") and sr.source_kind == "test":
        return True
    if a.startswith("instruction") and sr.source_kind == "instruction":
        return True
    if a in ("solution", "golden") and sr.source_kind == "solution":
        return True
    if a in ("support", "environment") and sr.source_kind == "support":
        return True
    return False


def _match_record(cited: str, ledger: list):
    for sr in ledger:
        if _file_match(cited, sr):
            return sr
    return None


def _depends_on_incomplete(c, ledger: list) -> bool:
    for d in c.source_dependencies:
        if d.required_for_claim and (not d.complete_relevant_corpus
                                     or d.evidence_mode in ("summarized", "reconstructed", "inferred")):
            return True
    incomplete = [sr for sr in ledger if not sr.complete]
    if not incomplete:
        return False
    for f in _cited_files(c):
        for sr in incomplete:
            if _file_match(f, sr):
                return True
    return False


def _absence_corpus_incomplete(c, ledger: list) -> bool:
    kinds = {"test"}
    scope = ((c.absence_scope or "") + " " + (c.claim or "")).lower()
    if "instruction" in scope or "spec" in scope:
        kinds.add("instruction")
    if "solution" in scope or "golden" in scope:
        kinds.add("solution")
    return any((not sr.complete) and sr.source_kind in kinds for sr in ledger)


# --------------------------------------------------- rendered manifests -------
def build_pass_manifests(task) -> list:
    """One RenderedSourceManifest per primary pass, derived from the source ledger."""
    ledger = task.source_records()
    out = []
    for pass_name, kinds in PASS_SECTIONS.items():
        refs, complete, section_texts = [], True, []
        for sr in ledger:
            if sr.source_kind not in kinds:
                continue
            refs.append(RenderedSourceRef(
                file=sr.file, source_kind=sr.source_kind, full_hash=sr.content_hash,
                included_ranges=[list(r) for r in sr.included_ranges],
                evidence_mode=("chunked" if sr.transformation == "chunked_complete" else "verbatim"),
                included_chars=sr.included_chars, omitted_chars=sr.total_chars - sr.included_chars,
                complete=sr.complete, prompt_section=sr.source_kind))
            section_texts.append(f"{sr.file}:{sr.included_content_hash}")
            if not sr.complete:
                complete = False
        # the Coverage Critic additionally embeds budget-sliced inter-pass JSON
        if pass_name == "coverage_critic":
            refs.append(RenderedSourceRef(
                file="<inter_pass_outputs>", source_kind="derived", evidence_mode="reconstructed",
                complete=True, prompt_section="prior_pass_json", post_budget_truncated=True))
        from .task_loader import _sha
        out.append(RenderedSourceManifest(
            pass_name=pass_name, prompt_hash=_sha("|".join(section_texts)),
            complete=complete, sources=refs))
    return out


# ------------------------------------------------ candidate dependency tags ---
def tag_source_dependencies(candidates: list, task) -> None:
    ledger = task.source_records()
    for c in candidates:
        if not c.source_dependencies:
            deps = []
            for f in _cited_files(c):
                sr = _match_record(f, ledger)
                if sr is not None:
                    deps.append(SourceDependency(
                        file=sr.file, required_for_claim=True,
                        ranges_used=[], complete_relevant_corpus=sr.complete,
                        evidence_mode=("chunked" if sr.transformation == "chunked_complete" else "verbatim"),
                        pass_name=(c.source_passes[0] if c.source_passes else "")))
            c.source_dependencies = deps
        if not c.absence_claim and is_absence_claim(c):
            c.absence_claim = True
            if not c.absence_scope:
                c.absence_scope = "all tests and fixture builders"


# ------------------------------------------------------- the safety cap -------
def apply_provenance_caps(candidates: list, task, gaps: list) -> int:
    """Cap supported/confirmed candidates that rest on incomplete source or on an
    unproven absence. Mutates candidates + appends gaps. Returns the cap count."""
    ledger = task.source_records()
    capped = 0
    for c in candidates:
        if c.status in ("refuted", "duplicate"):
            continue
        absence = is_absence_claim(c)
        inc_dep = _depends_on_incomplete(c, ledger)
        corpus_inc = absence and _absence_corpus_incomplete(c, ledger)
        reason = None
        if absence and c.status in ("supported", "confirmed"):
            corpus_ok = (not corpus_inc) and (not inc_dep)
            w = c.witness
            has_restricted = bool(w.submission_or_condition) and (
                w.type in _RESTRICTED_WITNESS or bool(w.execution_plan))
            has_vpath = bool(w.predicted_verifier_result)
            if not (corpus_ok and has_restricted and has_vpath):
                reason = "incomplete_source_context" if (corpus_inc or inc_dep) else "runtime_dependent"
        elif (not absence) and inc_dep and c.status in ("supported", "confirmed"):
            # static-contradiction exception: only cap when the incomplete file is a
            # REQUIRED cited dependency (which _depends_on_incomplete already checks).
            reason = "incomplete_source_context"
        if reason:
            c.status = "potential"
            c.severity = "potential_major"
            c.provenance = reason
            c.completeness_capped = True
            c.assumptions = list(c.assumptions) + [
                f"[completeness_cap] claim rests on incomplete/unproven source; capped to potential ({reason})"]
            if reason == "incomplete_source_context":
                gaps.append(CoverageGap(
                    area=f"incomplete_source:{c.issue_id}",
                    reason_not_resolved=("candidate depends on truncated/omitted/summarized source, or is an "
                                         "absence claim over an incomplete corpus"),
                    risk_if_wrong="a supported/confirmed tier would rest on source the pass never fully received",
                    recommended_probe="re-run with complete verifier + fixture-builder source and re-adjudicate",
                    material=True))
            capped += 1
    return capped


# ------------------------------------------------- conflict adjudication ------
def _subject(c) -> str:
    text = (c.claim or "") + " " + (c.absence_scope or "")
    ups = re.findall(r"\b([A-Z][A-Z0-9_]{3,})\b", text)
    if ups:
        return ups[0].lower()
    snakes = [x for x in re.findall(r"\b([a-z][a-z0-9_]{5,})\b", text)
              if "_" in x and x not in _STOP_SUBJ]
    return snakes[0] if snakes else ""


def adjudicate_conflicts(candidates: list, gaps: list) -> list:
    """Detect absence-vs-presence contradictions between candidates. Refute the
    absence when a presence record has complete source; else downgrade both to
    potential and emit a material gap (-> human_review). Raw records are preserved
    (status may change; nothing is deleted)."""
    conflicts: list = []
    live = [c for c in candidates if c.status not in ("refuted", "duplicate")]
    absences = [(c, _subject(c)) for c in live if is_absence_claim(c)]
    presences = [c for c in live if not is_absence_claim(c)]
    n = 0
    for a, sa in absences:
        if not sa:
            continue
        for p in presences:
            if p is a:
                continue
            pt = _text(p).lower()
            if sa == _subject(p) or sa in pt or sa.upper() in _text(p):
                n += 1
                cid = f"CF{n}"
                p_complete = not getattr(p, "completeness_capped", False)
                if p_complete and p.status in ("supported", "confirmed", "potential"):
                    a.status = "refuted"
                    a.assumptions = list(a.assumptions) + [
                        f"[conflict:{cid}] refuted by complete-source presence {p.issue_id} for '{sa}'"]
                    conflicts.append(ConflictRecord(
                        conflict_id=cid, kind="absence_vs_presence",
                        member_candidate_ids=[a.issue_id, p.issue_id], subject=sa,
                        winner_candidate_id=p.issue_id, loser_candidate_id=a.issue_id,
                        resolution="refuted_loser",
                        detail=f"absence '{(a.claim or '')[:70]}' vs presence '{(p.claim or '')[:70]}'"))
                else:
                    for x in (a, p):
                        if x.status in ("supported", "confirmed"):
                            x.status, x.severity = "potential", "potential_major"
                    gaps.append(CoverageGap(
                        area=f"unresolved_conflict:{cid}",
                        reason_not_resolved=f"contradictory claims about '{sa}' with no complete-source winner",
                        risk_if_wrong="one of two mutually exclusive Majors is wrong",
                        recommended_probe="resolve with complete source / execution", material=True))
                    conflicts.append(ConflictRecord(
                        conflict_id=cid, kind="absence_vs_presence",
                        member_candidate_ids=[a.issue_id, p.issue_id], subject=sa,
                        resolution="downgraded_all",
                        detail=f"absence '{(a.claim or '')[:70]}' vs presence '{(p.claim or '')[:70]}'"))
                break
    return conflicts
