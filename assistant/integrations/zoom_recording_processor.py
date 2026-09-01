"""Заглушка обработки облачных записей Zoom."""

from __future__ import annotations

from typing import Any, Iterator


def _iter_recording_file_dicts(obj: dict[str, Any]) -> list[dict[str, Any]]:
    files = obj.get("recording_files")
    out: list[dict[str, Any]] = []
    if isinstance(files, list):
        for f in files:
            if isinstance(f, dict):
                out.append(f)
    return out


def transcribe_enabled() -> bool:
    return False


def process_recording_async(*_a, **_k) -> None:
    return None
