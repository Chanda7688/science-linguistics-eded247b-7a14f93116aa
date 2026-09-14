#!/usr/bin/env python3
"""Render the Adversarial Verifier Audit (AVA) result as an ADVISORY PR sticky comment.

    render_ava_comment.py <ava.json>

AVA statically audits the verifier (4 Opus passes: Mapper / Breaker / Golden-Conformance /
Coverage-Critic) and routes block | human_review | static_pass via a materiality gate. In this
pipeline the audit is ADVISORY — it surfaces verifier-coverage and artifact-alignment risks for the
maintainer and never blocks the PR. Reads the audit JSON written by `ava.py audit --out`.
"""
import json
import sys

HEADER = "## 🔎 Verifier Audit (advisory)"
GH_LIMIT = 65000  # last-resort backstop only — just under GitHub's 65 536-char comment cap


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _witness_block(w):
    if not isinstance(w, dict) or not w:
        return ""
    rows = []
    for label, key in (
        ("Submission/condition", "submission_or_condition"),
        ("Expected (contract)", "expected_contract_result"),
        ("Predicted (verifier)", "predicted_verifier_result"),
        ("How to reproduce", "execution_plan"),
    ):
        v = w.get(key)
        if v:
            rows.append(f"    - _{label}:_ {str(v).strip()}")
    return "\n".join(rows)


def _issue_md(it):
    sev = it.get("severity", "?")
    claim = (it.get("claim") or "").strip() or "(no claim text)"
    out = [f"- **{claim}**"]
    fm = it.get("failure_mode")
    if fm:
        out.append(f"    - _failure mode:_ `{fm}`")
    ev = it.get("evidence") or []
    if isinstance(ev, list) and ev:
        e0 = ev[0]
        if isinstance(e0, dict):
            loc = " ".join(str(e0.get(k, "")) for k in ("file", "location") if e0.get(k)).strip()
            detail = (e0.get("detail") or "").strip()
            if loc or detail:
                out.append(f"    - _evidence:_ {('`'+loc+'` ' if loc else '')}{detail}")
    wb = _witness_block(it.get("witness"))
    if wb:
        out.append(wb)
    prov = it.get("provenance") or ", ".join(it.get("source_passes", []) or [])
    if prov:
        out.append(f"    - _found by:_ {prov}  · severity `{sev}`, status `{it.get('status','?')}`")
    return "\n".join(out)


def main():
    if len(sys.argv) < 2:
        print(f"{HEADER}\n\n_No audit file argument._")
        return
    rep = _load(sys.argv[1])
    lines = [HEADER, ""]

    if "_error" in rep:
        lines += [f"_Audit output could not be read ({rep['_error']}). See the job log._"]
        print("\n".join(lines))
        return

    ar = rep.get("audit_result") or {}
    routing = ar.get("routing", "unknown")
    reason = (ar.get("reason") or "").strip()
    issues = rep.get("candidate_issues") or []
    block_ids = set(ar.get("blocking_eligible_ids") or [])
    review_ids = set(ar.get("review_ids") or []) | set(ar.get("advisory_ids") or [])

    badge = {
        "block": "🟥 **BLOCK** (advisory) — material verifier gap(s) found",
        "human_review": "🟨 **REVIEW** — a maintainer should look",
        "static_pass": "🟩 **static_pass** — no material verifier gap found",
    }.get(routing, f"routing: `{routing}`")
    lines.append(badge)
    if reason:
        lines.append(f"\n{reason}")

    # "Material" == the materiality gate's blocking-eligible set ONLY. On static_pass this is
    # empty (findings were downgraded), so everything falls under Advisory — never contradict the
    # routing badge by promoting a downgraded finding. Advisory is sorted severity-first so a
    # downgraded high-severity finding (e.g. a reflection-invariant verifier) still reads at top.
    _ = review_ids  # (kept for future routing detail; advisory now covers all non-blocking issues)
    material = [it for it in issues if it.get("issue_id") in block_ids]
    advisory = [it for it in issues if it.get("issue_id") not in block_ids]
    sev_order = {"major": 0, "potential_major": 1, "minor": 2}
    advisory.sort(key=lambda it: sev_order.get(it.get("severity"), 3))

    if material:
        lines += ["", f"### Material findings — blocking-eligible ({len(material)})"]
        for it in material:
            lines.append(_issue_md(it))
    if advisory:
        heading = "Advisory notes" if not material else "Other findings"
        lines += ["", f"### {heading} ({len(advisory)})"]
        for it in advisory:
            lines.append(_issue_md(it))
    if not material and not advisory:
        lines += ["", "_No candidate issues surfaced._"]

    n = rep.get("model", "")
    lines += [
        "",
        "<sub>Adversarial Verifier Audit — a read-only static audit of the verifier "
        f"({n or 'Opus'}, 4 passes). **Advisory: never blocks the PR.** `block` means AVA found a "
        "likely invalid-but-accepted / valid-but-rejected submission or a golden-conformance gap — "
        "a maintainer should confirm and, if real, harden `tests/`.</sub>",
    ]

    body = "\n".join(lines)
    if len(body) > GH_LIMIT:
        body = body[:GH_LIMIT].rsplit("\n", 1)[0] + "\n\n_…truncated (see the job log for the full audit)._"
    print(body)


if __name__ == "__main__":
    main()
