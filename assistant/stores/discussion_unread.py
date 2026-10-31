"""Непрочитанное в обсуждениях: карточка в Фокусе, пока человек не открыл обсуждение.

Строка на (человек, обсуждение): последнее сообщение и сколько их пришло. Пишется, когда
сообщение приходит участнику, у которого обсуждение не открыто; стирается, когда он его
открывает (присутствие) или скрывает карточку.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Optional

from assistant.lib import sqlite_conn

MAX_AGE_SEC = 7 * 24 * 3600  # старше недели — в Фокусе уже не нужно
MAX_CARDS = 3

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite_conn.connect(path)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS discussion_unread (
            user_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            count INTEGER NOT NULL DEFAULT 0,
            chat_id INTEGER,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, kind, item_id)
        )
        """
    )
    _CONN.commit()
    return _CONN


def bump(
    user_id: Any,
    *,
    owner_user_id: Any,
    kind: str,
    item_id: Any,
    title: str,
    author: str,
    preview: str,
    chat_id: Optional[int] = None,
) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO discussion_unread
                (user_id, kind, item_id, owner_user_id, title, author, preview, count, chat_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(user_id, kind, item_id) DO UPDATE SET
                title = excluded.title, author = excluded.author, preview = excluded.preview,
                count = discussion_unread.count + 1, chat_id = excluded.chat_id,
                updated_at = excluded.updated_at
            """,
            (
                str(user_id),
                str(kind),
                str(item_id),
                str(owner_user_id),
                str(title or "")[:200],
                str(author or "")[:120],
                str(preview or "")[:300],
                int(chat_id) if chat_id is not None else None,
                time.time(),
            ),
        )
        conn.commit()


def clear(user_id: Any, kind: str, item_id: Any) -> bool:
    """Обсуждение открыли — убрать карточку. True, если она была."""
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM discussion_unread WHERE user_id = ? AND kind = ? AND item_id = ?",
            (str(user_id), str(kind), str(item_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def list_for(user_id: Any, *, limit: int = MAX_CARDS) -> list[dict[str, Any]]:
    cutoff = time.time() - MAX_AGE_SEC
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM discussion_unread WHERE user_id = ? AND updated_at < ?", (str(user_id), cutoff)
        )
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM discussion_unread WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
            (str(user_id), int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]
