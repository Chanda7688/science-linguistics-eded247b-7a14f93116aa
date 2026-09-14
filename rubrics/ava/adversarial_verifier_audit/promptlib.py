#!/usr/bin/env python3
"""Prompt loading + rendering.

The prompt templates embed literal JSON (``{ ... }``) as the required output shape,
so ``str.format`` is unusable. ``render`` does plain literal replacement of the
known ``{token}`` placeholders only, leaving every other brace untouched.
"""
from __future__ import annotations

import os

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


def load(name: str) -> str:
    with open(os.path.join(PROMPTS_DIR, name), encoding="utf-8") as f:
        return f.read()


def render(name: str, **fields: str) -> str:
    tpl = load(name)
    for k, v in fields.items():
        tpl = tpl.replace("{" + k + "}", "" if v is None else str(v))
    return tpl
