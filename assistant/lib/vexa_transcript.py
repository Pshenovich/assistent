"""Форматирование транскрипта Vexa с именами участников Zoom."""

from __future__ import annotations

from typing import Any


def participant_names_from_segments(segments: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        name = str(seg.get("speaker") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def formatted_transcript_from_vexa(body: dict[str, Any]) -> tuple[str, str, list[str]] | None:
    """plain, formatted (с именами), список участников."""
    segments = body.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    blocks: list[str] = []
    plain_parts: list[str] = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        speaker = str(seg.get("speaker") or "").strip() or "Участник"
        blocks.append(f"{speaker}:\n{text}")
        plain_parts.append(f"{speaker}: {text}")
    if not blocks:
        return None
    names = participant_names_from_segments(segments)
    return "\n".join(plain_parts), "\n\n".join(blocks), names
