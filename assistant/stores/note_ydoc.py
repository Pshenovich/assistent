"""Журнал обновлений Yjs по заметке (отдельная база data/note_ydoc.sqlite).

Сервер не разбирает Yjs: хранит бинарные обновления как есть, клиенты их сливают
и время от времени присылают сжатый снимок. ``base_revision`` — ревизия заметки,
на которой HTML последний раз сохранён из Yjs, ``base_html`` — тот HTML. Если ревизия
ушла вперёд (правка мимо Yjs — ИИ, бот, старое приложение), правку base_html → текущий
HTML вливают в документ слиянием (не затирая правок, ещё не попавших в HTML).
"""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH = ""


def _db_path() -> Path:
    raw = (os.getenv("NOTE_YDOC_DB_PATH") or "").strip()
    from assistant.config import ROOT

    if raw:
        p = Path(raw)
        return p if p.is_absolute() else ROOT / p
    notes_raw = (os.getenv("NOTES_DB_PATH") or "").strip()
    if notes_raw:
        notes_path = Path(notes_raw)
        if not notes_path.is_absolute():
            notes_path = ROOT / notes_path
        # Тесты — рядом с временной базой заметок; в установке — data/.
        if notes_path.resolve().parent != ROOT.resolve():
            return notes_path.with_name("note_ydoc.sqlite")
    return ROOT / "data" / "note_ydoc.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(_db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(path, check_same_thread=False)
    _CONN_PATH = path
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS note_ydoc (
            note_id INTEGER PRIMARY KEY,
            updates BLOB NOT NULL,
            base_revision INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
        """
    )
    cols = {r[1] for r in _CONN.execute("PRAGMA table_info(note_ydoc)").fetchall()}
    if "base_html" not in cols:
        _CONN.execute("ALTER TABLE note_ydoc ADD COLUMN base_html TEXT")
    _CONN.commit()
    return _CONN


def _pack(updates: list[bytes]) -> bytes:
    out = bytearray()
    for u in updates:
        out += struct.pack(">I", len(u))
        out += u
    return bytes(out)


def _unpack(blob: bytes) -> list[bytes]:
    out: list[bytes] = []
    i = 0
    n = len(blob or b"")
    while i + 4 <= n:
        (size,) = struct.unpack(">I", blob[i : i + 4])
        i += 4
        out.append(bytes(blob[i : i + size]))
        i += size
    return out


def load(note_id: int) -> tuple[list[bytes], int, Optional[str]]:
    """(обновления, base_revision, base_html); пусто — документа ещё нет."""
    with _LOCK:
        row = _conn().execute(
            "SELECT updates, base_revision, base_html FROM note_ydoc WHERE note_id = ?",
            (int(note_id),),
        ).fetchone()
    if row is None:
        return [], 0, None
    return _unpack(row[0]), int(row[1] or 0), row[2]


def save(note_id: int, updates: list[bytes], base_revision: int, base_html: Optional[str]) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with _LOCK:
        _conn().execute(
            """
            INSERT INTO note_ydoc (note_id, updates, base_revision, base_html, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(note_id) DO UPDATE SET
                updates = excluded.updates, base_revision = excluded.base_revision,
                base_html = excluded.base_html, updated_at = excluded.updated_at
            """,
            (int(note_id), _pack(updates), int(base_revision), base_html, ts),
        )
        _conn().commit()


def set_base(note_id: int, base_revision: int, base_html: str) -> None:
    """HTML сохранён из Yjs на этой ревизии."""
    with _LOCK:
        _conn().execute(
            "UPDATE note_ydoc SET base_revision = ?, base_html = ? WHERE note_id = ?",
            (int(base_revision), base_html, int(note_id)),
        )
        _conn().commit()


def delete(note_id: int) -> None:
    with _LOCK:
        _conn().execute("DELETE FROM note_ydoc WHERE note_id = ?", (int(note_id),))
        _conn().commit()
