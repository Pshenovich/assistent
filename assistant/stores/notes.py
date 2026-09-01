"""Локальные заметки пользователя (SQLite). Опциональная связь с Todoist task id."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    raw = (os.getenv("NOTES_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "notes.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS local_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            todoist_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_notes_user ON local_notes(user_id)"
    )
    _CONN.commit()
    return _CONN


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _plain_note_text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").replace("\xa0", " ").strip()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "title": str(row["title"] or ""),
        "body": str(row["body"] or ""),
        "description": str(row["body"] or ""),
        "content": str(row["title"] or ""),
        "todoist_id": row["todoist_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source": "local",
    }


def search_notes(
    user_id: int | str, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Поиск по заголовку и тексту (все слова запроса должны встретиться)."""
    uid = str(int(user_id))
    q = (query or "").strip()
    if not q:
        return []
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if not words:
        words = [q.lower()]
    lim = max(1, min(int(limit), 20))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for note in list_notes(uid, limit=500):
        hay = f"{note.get('title') or ''} {note.get('body') or ''}".lower()
        score = sum(1 for w in words if w in hay)
        if score > 0:
            try:
                updated_key = int(
                    str(note.get("updated_at") or note.get("created_at") or "")
                    .replace("-", "")
                    .replace(":", "")
                    .replace("T", "")
                    .replace("+", "")[:14]
                    or "0"
                )
            except ValueError:
                updated_key = 0
            scored.append((score, updated_key, note))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [n for _, _, n in scored[:lim]]


def list_notes(user_id: int | str, *, limit: int = 200) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    lim = max(1, min(int(limit), 500))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM local_notes
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (uid, lim),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_note(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM local_notes WHERE user_id = ? AND id = ?",
            (uid, int(note_id)),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def create_note(
    user_id: int | str,
    title: str,
    body: str = "",
    *,
    todoist_id: Optional[str] = None,
) -> dict[str, Any]:
    uid = str(int(user_id))
    title = (title or "").strip() or "(без названия)"
    body = (body or "").strip()
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO local_notes (user_id, title, body, todoist_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (uid, title, body, todoist_id, ts, ts),
        )
        _conn().commit()
        nid = int(cur.lastrowid)
    out = get_note(uid, nid)
    assert out is not None
    return out


def update_note(
    user_id: int | str,
    note_id: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    todoist_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    existing = get_note(uid, note_id)
    if not existing:
        return None
    new_title = existing["title"] if title is None else (title or "").strip() or "(без названия)"
    new_body = existing["body"] if body is None else (body or "").strip()
    # Автосохранение пустого редактора не должно затирать текст заметки.
    if body is not None and not _plain_note_text(new_body) and _plain_note_text(existing["body"] or ""):
        new_body = existing["body"]
    tid = existing.get("todoist_id") if todoist_id is None else todoist_id
    ts = _now_iso()
    with _LOCK:
        _conn().execute(
            """
            UPDATE local_notes
            SET title = ?, body = ?, todoist_id = ?, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (new_title, new_body, tid, ts, uid, int(note_id)),
        )
        _conn().commit()
    return get_note(uid, note_id)


def delete_note(user_id: int | str, note_id: int) -> bool:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM local_notes WHERE user_id = ? AND id = ?",
            (uid, int(note_id)),
        )
        _conn().commit()
        return cur.rowcount > 0


def upsert_by_todoist_id(
    user_id: int | str,
    todoist_id: str,
    title: str,
    body: str = "",
) -> dict[str, Any]:
    """Связать локальную заметку с задачей Todoist (создать или обновить)."""
    uid = str(int(user_id))
    tid = (todoist_id or "").strip()
    with _LOCK:
        cur = _conn().execute(
            "SELECT id FROM local_notes WHERE user_id = ? AND todoist_id = ?",
            (uid, tid),
        )
        row = cur.fetchone()
    if row:
        return update_note(uid, int(row["id"]), title=title, body=body, todoist_id=tid) or {}
    return create_note(uid, title, body, todoist_id=tid)
