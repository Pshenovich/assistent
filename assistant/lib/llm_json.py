"""Разбор JSON из ответов LLM (markdown-обёртки)."""

from __future__ import annotations

import re


def strip_json_from_markdown(content: str) -> str:
    s = (content or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()
