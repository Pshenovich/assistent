"""Сегменты транскрипта (кто, когда, что) — отдельно от meta заметки.

В meta не кладём: часовая встреча — сотни сегментов, а meta читается
при каждом списке и поиске по транскрипциям.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS transcript_segments (
            note_id INTEGER PRIMARY KEY,
            segments_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        )
        """
    )
    return conn


def save(note_id: int, segments: Any) -> None:
    segs = [dict(s) for s in (segments or []) if isinstance(s, dict)]
    if not segs:
        return
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO transcript_segments (note_id, segments_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                segments_json = excluded.segments_json,
                updated_at = excluded.updated_at
            """,
            (int(note_id), json.dumps(segs, ensure_ascii=False), notes_store._now_iso()),  # type: ignore[attr-defined]
        )
        conn.commit()


def get(note_id: int) -> Optional[list[dict[str, Any]]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT segments_json FROM transcript_segments WHERE note_id = ?",
            (int(note_id),),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0] or "[]")
    except (TypeError, ValueError):
        return None
    return [s for s in data if isinstance(s, dict)] or None


def delete(note_id: int) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM transcript_segments WHERE note_id = ?", (int(note_id),))
        conn.commit()
