#!/usr/bin/env python3
"""Task-package loader with budget controls.

Loads a TB2/Harbor task directory (``instruction.md``, ``tests/``, ``solution/``,
``environment/``, ``task.toml``) into a ``TaskPackage`` for the audit passes.

Budget controls (per the plan) so AVA does not indiscriminately concatenate every
file, blow the context window, or silently lose verifier code:
  * per-file size cap (large files are truncated, NOT dropped, with a marker);
  * binary-file exclusion (listed in the manifest, contents omitted);
  * deterministic file ordering (sorted; verifier-critical files first);
  * an omitted/truncated-file MANIFEST surfaced into the audit so truncation is visible;
  * targeted re-expansion of a helper/module referenced by the verifier but omitted;
  * per-section char/approx-token estimates.
"""
from __future__ import annotations

import dataclasses as dc
import hashlib
import os
import re

from .schemas import SourceRecord

# --- budget knobs -----------------------------------------------------------
# PER_FILE_CAP is now the CHUNK granularity for large files, NOT a hard per-file
# truncation limit: a large verifier file is included COMPLETELY (as consecutive
# chunks covering the whole text) as long as it fits the per-section budget. Only
# when the section budget itself is exhausted is a file truncated -- and that is
# always recorded in the source ledger (complete=False + exact omitted_ranges).
PER_FILE_CAP = 24_000          # chunk size for large files
TESTS_TOTAL_CAP = 400_000      # chars across all tests/ files (raised: include full verifiers)
SOLUTION_TOTAL_CAP = 200_000
INSTRUCTION_CAP = 40_000
REEXPAND_CAP = 200_000         # a verifier-referenced helper (e.g. fixture builders) is
                               # included COMPLETELY up to this bound, not front-sliced
BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip", ".gz",
    ".tar", ".xz", ".7z", ".parquet", ".pkl", ".pickle", ".npy", ".npz",
    ".pt", ".pth", ".bin", ".so", ".dylib", ".o", ".a", ".wav", ".mp3",
    ".mp4", ".jar", ".class", ".pyc", ".db", ".sqlite", ".feather", ".h5",
}
# order tests/ files so the verifier logic comes first even under a cap
TEST_PRIORITY = ("test.sh", "run_tests.sh", "test_outputs.py", "conftest.py")


def approx_tokens(s: str) -> int:
    return len(s) // 4


def _sha(s: str) -> str:
    return "sha256:" + hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:32]


def chunk_list(n: int, size: int = PER_FILE_CAP) -> list:
    """Deterministic consecutive chunks covering [0, n) with stable ids. The union
    of chunk ranges is EXACTLY [0, n) -- no gaps, no overlaps."""
    out: list = []
    if n <= 0:
        return out
    s, i = 0, 0
    while s < n:
        e = min(s + size, n)
        out.append({"id": f"c{i + 1}", "start": s, "end": e})
        s, i = e, i + 1
    return out


def validate_chunk_coverage(total: int, chunks: list) -> bool:
    """True iff `chunks` tile [0, total) exactly: sorted, contiguous, no gap/overlap."""
    if total <= 0:
        return not chunks
    exp = 0
    for ch in chunks:
        if int(ch.get("start", -1)) != exp:
            return False
        e = int(ch.get("end", -1))
        if e <= exp:
            return False
        exp = e
    return exp == total


def _looks_binary(path: str) -> bool:
    if os.path.splitext(path)[1].lower() in BINARY_EXT:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        return b"\x00" in chunk
    except Exception:
        return True


@dc.dataclass
class LoadedFile:
    relpath: str
    text: str
    truncated: bool = False
    omitted: bool = False
    reason: str = ""
    size_bytes: int = 0


class TaskPackage:
    def __init__(self, task_dir: str, task_id: str = ""):
        self.task_dir = os.path.abspath(task_dir)
        self.task_id = task_id or os.path.basename(self.task_dir)
        self.instruction = ""
        self.task_toml = ""
        self.dockerfile = ""
        self.tests: list[LoadedFile] = []
        self.solution: list[LoadedFile] = []
        self.environment_listing: list[str] = []   # /app-visible file paths (contents not inlined)
        self.agent_visible_support: list[LoadedFile] = []  # small agent-visible .py under environment/
        self.omitted: list[dict] = []              # legacy back-compat list (derived view is the ledger)
        self.source_ledger: list[SourceRecord] = []  # the SINGLE source of truth for completeness
        self._load()

    # ---- helpers ----
    def _read_text(self, path: str) -> str:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return ""

    def _add_source(self, rel: str, kind: str, full_text: str, included_chars: int,
                    complete: bool, transformation: str, reason: str,
                    omitted_ranges: list, chunks: list) -> None:
        """Append one typed ledger row. `included_chars` is the count of REAL source
        chars placed in the prompt (excluding any truncation marker)."""
        inc = full_text[:included_chars]
        self.source_ledger.append(SourceRecord(
            file=rel, source_kind=kind,
            total_bytes=len(full_text.encode("utf-8", "replace")), total_chars=len(full_text),
            included_bytes=len(inc.encode("utf-8", "replace")), included_chars=len(inc),
            complete=complete,
            included_ranges=([[0, len(inc)]] if inc else []),
            omitted_ranges=[list(r) for r in omitted_ranges],
            transformation=transformation, reason=reason,
            content_hash=_sha(full_text), included_content_hash=_sha(inc),
            chunks=chunks or []))

    def _load_dir(self, sub: str, total_cap: int, priority: tuple = ()) -> list[LoadedFile]:
        root = os.path.join(self.task_dir, sub)
        files: list[LoadedFile] = []
        if not os.path.isdir(root):
            return files
        paths = []
        for r, _, fs in os.walk(root):
            for f in fs:
                paths.append(os.path.join(r, f))
        # deterministic ordering: priority basenames first, then sorted relpath
        def key(p):
            b = os.path.basename(p)
            pr = priority.index(b) if b in priority else len(priority)
            return (pr, os.path.relpath(p, self.task_dir))
        paths.sort(key=key)
        kind = "test" if sub == "tests" else ("solution" if sub == "solution" else "other")

        used = 0
        for p in paths:
            rel = os.path.relpath(p, self.task_dir)
            size = os.path.getsize(p) if os.path.isfile(p) else 0
            if _looks_binary(p):
                self.omitted.append({"file": rel, "reason": "binary", "size_bytes": size})
                self._add_source(rel, kind, "", 0, False, "omitted", "binary", [], [])
                files.append(LoadedFile(rel, "", omitted=True, reason="binary", size_bytes=size))
                continue
            full = self._read_text(p)
            n = len(full)
            budget_left = max(0, total_cap - used)
            if n <= budget_left:
                # include the WHOLE file (chunked if large) -> complete source
                transform = "chunked_complete" if n > PER_FILE_CAP else "verbatim"
                self._add_source(rel, kind, full, n, True, transform, "", [], chunk_list(n))
                files.append(LoadedFile(rel, full, truncated=False, size_bytes=size))
                used += n
            elif budget_left <= 0:
                # no room left in this section -> omit entirely (recorded)
                self.omitted.append({"file": rel, "reason": f"{sub}-budget", "size_bytes": size})
                self._add_source(rel, kind, full, 0, False, "omitted", "section_budget", [[0, n]], [])
                files.append(LoadedFile(rel, "", omitted=True, reason=f"{sub}-budget", size_bytes=size))
            else:
                # partial include -> truncated verbatim prefix (recorded, complete=False)
                inc = budget_left
                txt = full[:inc] + f"\n... [TRUNCATED {n - inc} chars; {sub} section budget]"
                self.omitted.append({"file": rel, "reason": f"{sub}-budget", "size_bytes": size})
                self._add_source(rel, kind, full, inc, False, "verbatim_prefix",
                                 "section_budget", [[inc, n]], chunk_list(inc))
                files.append(LoadedFile(rel, txt, truncated=True, size_bytes=size))
                used += inc
        return files

    def _load(self):
        ip = os.path.join(self.task_dir, "instruction.md")
        full_instr = self._read_text(ip)
        if len(full_instr) > INSTRUCTION_CAP:
            self.instruction = full_instr[:INSTRUCTION_CAP] + f"\n... [TRUNCATED {len(full_instr) - INSTRUCTION_CAP} chars]"
            self.omitted.append({"file": "instruction.md", "reason": "instruction_cap", "size_bytes": len(full_instr)})
            self._add_source("instruction.md", "instruction", full_instr, INSTRUCTION_CAP, False,
                             "verbatim_prefix", "instruction_cap", [[INSTRUCTION_CAP, len(full_instr)]],
                             chunk_list(INSTRUCTION_CAP))
        else:
            self.instruction = full_instr
            self._add_source("instruction.md", "instruction", full_instr, len(full_instr), True,
                             "verbatim", "", [], chunk_list(len(full_instr)))
        self.task_toml = self._read_text(os.path.join(self.task_dir, "task.toml"))
        if self.task_toml:
            self._add_source("task.toml", "task_toml", self.task_toml, len(self.task_toml), True,
                             "verbatim", "", [], chunk_list(len(self.task_toml)))
        self.dockerfile = self._read_text(os.path.join(self.task_dir, "environment", "Dockerfile"))
        if self.dockerfile:
            self._add_source("environment/Dockerfile", "dockerfile", self.dockerfile, len(self.dockerfile),
                             True, "verbatim", "", [], chunk_list(len(self.dockerfile)))
        self.tests = self._load_dir("tests", TESTS_TOTAL_CAP, TEST_PRIORITY)
        self.solution = self._load_dir("solution", SOLUTION_TOTAL_CAP)
        self._load_environment()
        self._reexpand_referenced_helpers()

    def _load_environment(self):
        envroot = os.path.join(self.task_dir, "environment")
        if not os.path.isdir(envroot):
            return
        for r, _, fs in os.walk(envroot):
            for f in sorted(fs):
                p = os.path.join(r, f)
                rel = os.path.relpath(p, self.task_dir)
                self.environment_listing.append(rel)
                # inline only SMALL agent-visible .py (helps the mapper without blowing budget)
                if f.endswith(".py") and not _looks_binary(p) and os.path.getsize(p) < 8000:
                    txt = self._read_text(p)
                    self.agent_visible_support.append(LoadedFile(rel, txt, size_bytes=os.path.getsize(p)))
                    self._add_source(rel, "support", txt, len(txt), True, "verbatim", "", [], chunk_list(len(txt)))
        self.environment_listing.sort()

    def _reexpand_referenced_helpers(self):
        """If the verifier text imports/reads a module we omitted or truncated,
        pull that specific file back in (targeted expansion), recording completeness."""
        verifier_text = self.tests_text()
        refs = set(re.findall(r"(?:from|import)\s+([a-zA-Z_][\w]*)", verifier_text))
        refs |= set(re.findall(r"([a-zA-Z_][\w]*)\.py", verifier_text))
        if not refs:
            return
        by_stem = {}
        for r, _, fs in os.walk(self.task_dir):
            if "/.git" in r:
                continue
            for f in fs:
                if f.endswith(".py"):
                    by_stem.setdefault(os.path.splitext(f)[0], os.path.join(r, f))
        have = {os.path.splitext(os.path.basename(lf.relpath))[0] for lf in self.tests + self.solution
                if not lf.omitted and not lf.truncated}
        for mod in sorted(refs):
            if mod in by_stem and mod not in have:
                p = by_stem[mod]
                if _looks_binary(p):
                    continue
                full_h = self._read_text(p)
                rel = os.path.relpath(p, self.task_dir)
                n = len(full_h)
                if n > REEXPAND_CAP:
                    txt = full_h[:REEXPAND_CAP] + f"\n... [TRUNCATED {n - REEXPAND_CAP} chars]"
                    self.omitted.append({"file": rel, "reason": "reexpand_cap", "size_bytes": os.path.getsize(p)})
                    self._add_source(rel, "support", full_h, REEXPAND_CAP, False, "verbatim_prefix",
                                     "reexpand_cap", [[REEXPAND_CAP, n]], chunk_list(REEXPAND_CAP))
                else:
                    txt = full_h
                    transform = "chunked_complete" if n > PER_FILE_CAP else "reexpanded"
                    self._add_source(rel, "support", full_h, n, True, transform, "", [], chunk_list(n))
                self.agent_visible_support.append(
                    LoadedFile(rel, txt, reason="reexpanded-referenced-helper", size_bytes=os.path.getsize(p)))

    # ---- rendered sections for prompts ----
    def tests_text(self) -> str:
        return "\n".join(f"# ==== {lf.relpath} ====\n{lf.text}" for lf in self.tests if not lf.omitted)

    def solution_text(self) -> str:
        return "\n".join(f"# ==== {lf.relpath} ====\n{lf.text}" for lf in self.solution if not lf.omitted)

    def support_text(self) -> str:
        return "\n".join(f"# ==== {lf.relpath} ====\n{lf.text}" for lf in self.agent_visible_support)

    # ---- completeness ledger + manifest ----
    def source_records(self) -> list:
        return list(self.source_ledger)

    def validate_ledger(self) -> tuple:
        """Fail-closed integrity check on the source ledger. Returns (ok, errors).
        Guarantees the reviewed impossible state cannot occur: an incomplete record
        with no omitted_ranges, or included+omitted != total, is an ERROR."""
        errors: list = []
        for sr in self.source_ledger:
            accounted = sr.included_chars + sum(max(0, b - a) for a, b in sr.omitted_ranges)
            if sr.transformation not in ("omitted", "summary", "unreadable") and accounted != sr.total_chars:
                errors.append(f"{sr.file}: included+omitted={accounted} != total={sr.total_chars}")
            if sr.complete and sr.omitted_ranges:
                errors.append(f"{sr.file}: marked complete but has omitted_ranges")
            if (not sr.complete) and not sr.omitted_ranges and sr.transformation not in ("omitted", "unreadable"):
                errors.append(f"{sr.file}: marked incomplete but no omitted_ranges")
            if sr.transformation == "chunked_complete" and not validate_chunk_coverage(sr.included_chars, sr.chunks):
                errors.append(f"{sr.file}: chunked_complete but chunk coverage is not exact")
        return (not errors), errors

    def manifest(self) -> dict:
        ok, errs = self.validate_ledger()
        # omitted_or_truncated is DERIVED from the ledger -> it can never be empty
        # while any source is incomplete (the reviewed impossible state).
        omitted = [
            {"file": sr.file, "reason": sr.reason or sr.transformation, "size_bytes": sr.total_bytes,
             "transformation": sr.transformation, "omitted_ranges": sr.omitted_ranges}
            for sr in self.source_ledger if not sr.complete
        ]
        return {
            "task_id": self.task_id,
            "task_dir": self.task_dir,
            "omitted_or_truncated": omitted,
            "source_ledger": [dc.asdict(sr) for sr in self.source_ledger],
            "ledger_consistent": ok,
            "ledger_errors": errs,
            "all_sources_complete": all(sr.complete for sr in self.source_ledger),
            "n_test_files": len([f for f in self.tests if not f.omitted]),
            "n_solution_files": len([f for f in self.solution if not f.omitted]),
            "environment_files": len(self.environment_listing),
            "approx_tokens": {
                "instruction": approx_tokens(self.instruction),
                "tests": approx_tokens(self.tests_text()),
                "solution": approx_tokens(self.solution_text()),
                "support": approx_tokens(self.support_text()),
            },
        }
