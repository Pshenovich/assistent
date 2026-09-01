"""Извлечение URL из текста."""

from __future__ import annotations

import re


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://\S+", text or "", flags=re.IGNORECASE)
