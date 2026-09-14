# Adversarial Verifier Audit (AVA) — v1 (read-only)

A standalone auto-eval component that **reconstructs and attacks a task verifier's
acceptance boundary**. It complements — does not replace — the existing rubric-based
deep review (`.harbor/deep-review/`) and the deterministic scanner
(`deterministic_checks.py`).

- **Rubric-Based Task Review** asks: *does the task match known quality criteria and
  defect patterns?*
- **Adversarial Verifier Audit** asks: *what does the verifier actually accept, and
  what is the smallest invalid/adversarial submission that satisfies it?*

## Where it integrates
AVA is a sibling tool in the CF toolage. It reuses the billing-safe `claude -p`
pattern (`llm.py`) and consumes the deterministic scanner's findings
(`deterministic_bridge.py`). It does **not** modify `.harbor/`, the 25 rubric
criteria, or the deep-review harness. Its structured JSON output is designed to be
fused later with deep-review results (fusion is out of scope for v1).

## Pipeline (per task)
```
load task -> Verifier Mapper + Verifier Breaker (independent)
          -> Coverage Critic (sees both, adds gaps/candidates, never removes)
          -> union candidates (+ deterministic bridge)
          -> status from PROVENANCE (code decides; LLM cannot self-confirm)
          -> typed-refutation validation
          -> nondestructive clustering
          -> materiality + ownership gate (materiality.py)
          -> code-computed routing -> AuditReport (JSON)
```

## Fail-closed design (v1)
- The scaled gate routing (`route_from_rows`) is **binary `block | static_pass`**:
  only a Major (`blocking_eligible`) finding — or a parse-failure fail-closed (an
  unparseable audit) — blocks. Minor (`routes_to_review`) findings no longer block,
  including truncated/incomplete source (`completeness_capped` / provenance
  `incomplete_source_context`, the `source_incomplete` class); they are retained as
  non-blocking **advisories** (`review_ids`). There is **no `safe_to_accept`**;
  `static_pass` is NOT proof of soundness — real acceptance requires the future
  execution-backed phase (`execution_validation_complete` is always `false` in v1).
- Malformed model output is never silently dropped: it is retried, preserved, and
  converted to a coverage gap + `material_parse_failures` that routes to human review.
- Provenance ladder (the precise definition of `confirmed`): deterministic-scanner
  finding → `confirmed`; literal instruction↔verifier contradiction with ≥2 cited
  paths → `confirmed`; fully-traced static exploit → `supported`; runtime-dependent
  → `potential`. An LLM-only speculative exploit can never be `confirmed`.

## v1.4: materiality + ownership gate (`materiality.py`)
`status=supported`/`confirmed` is evidence quality, not routing authority. A prior
policy ("any supported-or-confirmed Major blocks") over-blocked badly when validated
against a 20-task client-labeled set: 18/20 blocked, all 4 client-clean tasks falsely
blocked, and 14/18 blocks were for a reason unrelated to the client's actual defect.

`materiality.py` inserts a deterministic gate between candidate adjudication and
routing:
- **Materiality classes** — a candidate only becomes `blocking_eligible` when it is a
  concrete, plausible, material acceptance-boundary failure (`verified_exploit`,
  `verified_false_rejection`, `material_contract_contradiction`,
  `material_golden_violation`). Missing-fixture coverage gaps, speculative/hypothetical
  attacks, protected-secret equality, harmless schema looseness, and
  semantically-equivalent golden-conformance paraphrases route to `human_review` (or
  are discarded as immaterial) instead of auto-blocking.
- **Golden-conformance counterfactuals** are classified via 5 structured properties
  (`contract_valid`, `environment_reachable`, `distinguishing`, `affects_required_output`,
  `present_in_shipped_fixture`) rather than a blanket "constructed input → discard"
  heuristic — a contract-valid constructed input (extra row, fresh schema-conforming
  file) is never auto-discarded merely for being constructed; only genuinely
  out-of-domain/out-of-contract/non-distinguishing counterfactuals are.
- **Scope ownership**: AVA is the pre-pass for exactly two families — **verifier
  coverage/soundness** and **artifact alignment**. Pure instruction ambiguity,
  coherent-contract concerns, and information-theoretic solvability/uniqueness are
  deep-review-owned; they are retained as advisory/handed-off findings (visible in the
  report) but never independently block or route AVA. Ambiguity is AVA-relevant only
  when an artifact resolves it inconsistently (verifier enforces one reading, golden
  contradicts verifier, a valid reading is rejected) — that surfaces as a concrete
  materiality class, not a bare "ambiguous" label. Every candidate carries explicit
  scope fields: `in_scope`, `scope_owner` (`ava|deep_review|advisory`), `scope_family`
  (`verifier_coverage|artifact_alignment|instruction_ambiguity|other`), `scope_reason`.
- **Live-wired**: `adjudication.route_with_materiality` (called from `audit.py`) is the
  routing authority for every real audit run — not just the offline recalibration
  harness. `AuditResult` exposes both the FINAL routing and, for debugging, the
  pre-gate legacy policy's routing (`pre_materiality_routing`/`pre_materiality_reason`),
  plus `blocking_eligible_ids` / `review_ids` / `deep_review_ids` / `advisory_ids` /
  `downgraded_ids`. Terminal routes remain exactly `{block, human_review, static_pass}`;
  no new terminal state was introduced. Live and offline routing are proven identical
  by `tools/tests/test_live_routing_equivalence.py` (both paths funnel through the same
  `materiality.route_from_rows`).
- **Validated result** (20-task client-labeled corpus): routing distribution moved from
  `18 block / 2 human_review / 0 static_pass` to `4 block / 8 human_review /
  8 static_pass`; clean-task false-block rate `4/4 → 0/4`; wrong-reason block rate
  `14/18 → 0/4`. Recall is now measured honestly per AVA-owned family via a
  mechanism-level miss ledger (`tools/ava_recalibrate.py` → `mechanism_miss_ledger.json`):
  combined AVA-owned exact-mechanism recall is `5/18` (`verifier_coverage` `4/10`,
  `artifact_alignment` `1/8`) — a real recall gap for future prompt work to close, no
  longer masked by task-level "rejected the same task" scoring.
- **Offline recalibration harness**: `tools/ava_recalibrate.py` re-scores the saved
  20-task corpus against `review_outcomes_20_tasks.json` (client labels) +
  `recalibration/client_issue_map.json` (reviewer-authored ground-truth mechanism map,
  used ONLY for offline metrics — never imported by any production/routing module; see
  `tools/tests/test_recalibration.py::TestProductionImportSafety`). Run:
  `python3 tools/ava_recalibrate.py`.

## Usage
```
export ANTHROPIC_API_KEY=...   # same key the other checks use; the tool aborts if unset
python3 ava.py audit <task_dir> --out audit.json
python3 ava.py batch --in gt_phase14_targets.jsonl --out ava_out/
```

## v1.1 additions
Four primary model calls per task: **Mapper**, **Golden-Solution Conformance**, **Breaker**, **Coverage Critic**. On top of these, code DERIVES (no extra calls by default):
- a four-way **artifact-alignment matrix** (Instruction → Environment → Golden solution → Verifier/fixtures) with provenance + enforcement mode;
- **Golden-Solution Conformance**: the reference solution is checked against instruction *semantics* — a violation is Major even when the output matches and the verifier passes;
- **edge-case / fixture-diversity** detection (e.g. an env override never varied by tests) and **discriminating-test** analysis (could a restricted/hardcoded implementation still pass?);
- **phase-aware trust / import-resolution** risks (oracle import from `/tests`, shadowable helper, root-writable `/data`).

Blind evaluation is enforced: run with `--config blind_ava_only` (no deterministic seeds) or `--config ava_with_deterministic_seeds`. The production gate is **blind and audit-only** (`audit` / `batch` -> materiality routing). The offline recall scorer and its held-out client labels are intentionally **NOT shipped in this bundle** -- recall measurement is a separate, out-of-repo workstream, so no ground-truth answer key travels with the gate.
## Tests
The LLM-free unit suite is maintained in the AVA source repo and is **not vendored** in this
production bundle. This vendored copy is validated by import + `py_compile` (pure stdlib; loads
with zero model calls); the union gate's routing is exercised via `scaled_eval/gate.py`.
