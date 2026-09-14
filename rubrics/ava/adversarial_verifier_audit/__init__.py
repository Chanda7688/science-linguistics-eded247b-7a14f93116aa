#!/usr/bin/env python3
"""Adversarial Verifier Audit (AVA) -- v1 (read-only).

A standalone auto-eval component that reconstructs and attacks a task verifier's
acceptance boundary. Complements (does not replace) the rubric-based deep review
and the deterministic scanner.

Public entrypoint: ``audit_task(task_dir, call_llm) -> AuditReport``.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .audit import audit_task   # noqa: E402,F401
