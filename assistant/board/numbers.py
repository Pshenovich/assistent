"""Безопасный разбор чисел из JSON LLM (часто приходит строка 'null')."""

from __future__ import annotations

from typing import Any

_NULL = {"", "null", "none", "undefined", "nan", "nil", "n/a", "na"}


def safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        try:
            if value != value:  # NaN
                return default
        except Exception:
            return default
        return float(value)
    text = str(value).strip().lower()
    if text in _NULL:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    return int(round(safe_float(value, float(default))))
