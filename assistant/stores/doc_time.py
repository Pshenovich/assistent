"""Время работы в документах: минуты, когда человек был в открытом документе и что-то делал.

Отметка приходит с присутствием в документе (POST /notes/local/{id}/collab, раз в 1,5–15 с, только
пока вкладка видна); пишем минуту, только если клиент сообщил активность (ввод, прокрутка, клики за
последние 2 минуты), — открытый и забытый документ часы не накручивает. Одна строка на
(человек, документ, минута). Отдельная база data/doc_time.sqlite: частые записи не мешают базе документов.
Читает учёт часов по проектам (services/project_hours.py). Хранится полгода.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from assistant.lib import sqlite_conn

KEEP_DAYS = 190
_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: str = ""
_LAST_PURGE = [0.0]


def _db_path() -> Path:
    p = os.getenv("DOC_TIME_DB_PATH", "").strip()
    if p:
        return Path(p)
    notes = os.getenv("NOTES_DB_PATH", "").strip()
    base = Path(notes).parent if notes else Path(__file__).resolve().parents[2] / "data"
    return base / "doc_time.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(_db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite_conn.connect(path)
    _CONN_PATH = path
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS doc_minutes (user_id TEXT NOT NULL, note_id INTEGER NOT NULL, "
        "minute INTEGER NOT NULL, PRIMARY KEY (user_id, note_id, minute))"
    )
    _CONN.execute("CREATE INDEX IF NOT EXISTS idx_doc_minutes_user ON doc_minutes(user_id, minute)")
    _CONN.commit()
    return _CONN


def touch(user_id: Any, note_id: Any, *, at: float | None = None) -> None:
    """Человек активен в документе в эту минуту."""
    minute = int((at or time.time()) // 60)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT OR IGNORE INTO doc_minutes(user_id, note_id, minute) VALUES (?, ?, ?)",
            (str(int(user_id)), int(note_id), minute),
        )
        if time.time() - _LAST_PURGE[0] > 3600:
            _LAST_PURGE[0] = time.time()
            conn.execute("DELETE FROM doc_minutes WHERE minute < ?", (minute - KEEP_DAYS * 1440,))
        conn.commit()


def minutes(user_ids: Iterable[Any], start_ts: float, end_ts: float) -> list[tuple[str, int, int]]:
    """(человек, документ, минута с начала эпохи) за [start, end)."""
    ids = sorted({str(int(u)) for u in user_ids})
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    with _LOCK:
        rows = _conn().execute(
            f"SELECT user_id, note_id, minute FROM doc_minutes WHERE user_id IN ({q}) AND minute >= ? AND minute < ?",
            (*ids, int(start_ts // 60), int(end_ts // 60)),
        ).fetchall()
    return [(str(r[0]), int(r[1]), int(r[2])) for r in rows]
