#!/usr/bin/env python3
"""Billing-safe LLM client for AVA.

Reuses the exact pattern from ``run_deepreview_local.py`` / ``semantic_verifier.py``:
shell out to the Claude Code CLI (``claude -p``) with the TEAM key injected as
``ANTHROPIC_API_KEY`` for the subprocess only + bare mode
(``CLAUDE_CODE_SIMPLE=1``) so it CANNOT fall back to a personal subscription.
Aborts loudly if the team key env var is unset.

The audit passes depend on a ``call_llm(prompt) -> text`` callable. In production
that is ``ClaudeCLI(model=...)``; unit tests inject a fake callable so the whole
pipeline runs with ZERO team-key spend.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import claude_costs  # noqa: E402

EVAL_KEY_ENV = "ANTHROPIC_API_KEY"   # the SAME key the other checks (deep_review, pass@k grading) use
DEFAULT_MODEL = "claude-opus-4-8"

# A CallLLM is any callable that maps a prompt string to a raw model reply string.
CallLLM = Callable[[str], str]


def require_team_key(env_var: str = EVAL_KEY_ENV) -> str:
    """Return the API key to bill, or abort. AVA bills $ANTHROPIC_API_KEY -- the SAME key the other
    checks (deep_review, pass@k grading) use -- so it works from the keys already configured for the
    pipeline. Used in bare mode (CLAUDE_CODE_SIMPLE=1, see _claude_env), so it bills the key itself,
    never a personal Claude subscription."""
    key = os.environ.get(env_var, "").strip()
    if not key:
        sys.exit(
            f"ABORT: no API key for AVA. Set ${env_var} (the same key the other checks use):\n"
            f"    export {env_var}='sk-ant-...'\n"
        )
    return key


def _claude_env(api_key: str) -> dict:
    env = dict(os.environ)
    env["ANTHROPIC_API_KEY"] = api_key
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    env["CLAUDE_CODE_SIMPLE"] = "1"        # bare mode: key-only auth, no keychain/subscription fallback
    return env


class ClaudeCLI:
    """Production ``CallLLM``: a billing-safe ``claude -p`` invocation.

    Records a coarse call log for calibration and, when CLAUDE_COST_DIR is set,
    writes the CLI's cost estimate to the workflow's private cost artifact.
    """

    def __init__(self, model: str = DEFAULT_MODEL, env_var: str = EVAL_KEY_ENV,
                 timeout: int = 900, retries: int = 3):
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self._api_key = require_team_key(env_var)
        self.calls: list[dict] = []

    def __call__(self, prompt: str) -> str:
        out = ""
        t0 = time.time()
        for attempt in range(self.retries):
            try:
                r = subprocess.run(
                    ["claude", "-p", "--model", self.model, "--permission-mode", "dontAsk",
                     "--output-format", "json"],
                    capture_output=True, text=True, timeout=self.timeout,
                    input=prompt, env=_claude_env(self._api_key),
                )
                raw = r.stdout or ""
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    out = payload.get("result", "") or ""
                    cost_dir = os.environ.get("CLAUDE_COST_DIR", "").strip()
                    if cost_dir:
                        try:
                            claude_costs.write_record(
                                cost_dir,
                                claude_costs.build_cli_record(
                                    payload,
                                    stage=os.environ.get("CLAUDE_COST_STAGE", "ava-review"),
                                    model=self.model,
                                ),
                            )
                        except Exception as cost_error:  # noqa: BLE001 - telemetry is non-gating
                            print(
                                f"warning: could not write Claude cost record: {cost_error}",
                                file=sys.stderr,
                            )
                else:
                    out = raw
            except subprocess.TimeoutExpired:
                out = ""
            if out.strip():
                break
            if attempt < self.retries - 1:
                time.sleep(min(60, 5 * (2 ** attempt)))
        self.calls.append({
            "prompt_chars": len(prompt),
            "reply_chars": len(out),
            "approx_prompt_tokens": len(prompt) // 4,
            "approx_reply_tokens": len(out) // 4,
            "seconds": round(time.time() - t0, 1),
        })
        return out

    def reset(self) -> None:
        """Clear the per-call log so metrics are TASK-LOCAL when one ClaudeCLI
        instance is reused across a batch (audit_task calls this at the start)."""
        self.calls = []

    def metrics(self) -> dict:
        return {
            "model": self.model,
            "llm_calls": len(self.calls),
            "approx_total_tokens": sum(c["approx_prompt_tokens"] + c["approx_reply_tokens"] for c in self.calls),
            "total_seconds": round(sum(c["seconds"] for c in self.calls), 1),
            "per_call": self.calls,
        }
