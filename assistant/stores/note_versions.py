"""История версий заметок и листов (отдельная база data/note_versions.sqlite).

Перед перезаписью текста сохраняем ПРЕЖНЕЕ состояние, не чаще раза в 10 минут
на заметку/лист, но всегда — при крупном изменении (текст сократился/вырос на треть),
при восстановлении версии и перед удалением листа или заметки. Запись версии
никогда не мешает сохранению: все ошибки глотаются.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PRIMARY_SHEET = 0  # «Основная» вкладка заметки
THROTTLE = timedelta(minutes=10)
KEEP_PER_ITEM = 60
KEEP_PER_ITEM_LARGE = 10
LARGE_BODY = 300_000

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None


def _db_path() -> Path:
    raw = (os.getenv("NOTE_VERSIONS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    notes_raw = (os.getenv("NOTES_DB_PATH") or "").strip()
    if notes_raw:
        # Тесты и нестандартные установки: рядом с базой заметок.
        return Path(notes_raw).with_name("note_versions.sqlite")
    from assistant.config import ROOT

    return ROOT / "data" / "note_versions.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(_db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(path, check_same_thread=False)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS note_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            sheet_id INTEGER NOT NULL DEFAULT 0,
            sheet_title TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 0,
            reason TEXT NOT NULL DEFAULT 'edit',
            author_user_id TEXT NOT NULL DEFAULT '',
            saved_at TEXT NOT NULL,
            state_at TEXT NOT NULL DEFAULT '',
            sheet_kind TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_versions_item ON note_versions(note_id, sheet_id, saved_at DESC)"
    )
    _CONN.commit()
    return _CONN


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(ts: str) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _big_change(old_body: str, new_body: Optional[str]) -> bool:
    if new_body is None:
        return False
    a, b = len(old_body or ""), len(new_body or "")
    if not a:
        return False
    return abs(a - b) >= max(200, a // 3)


def record(
    *,
    owner_user_id: Any,
    note_id: Any,
    title: str,
    body: str,
    revision: Any = 0,
    state_at: str = "",
    sheet_id: int = PRIMARY_SHEET,
    sheet_title: str = "",
    sheet_kind: str = "",
    reason: str = "edit",
    author_user_id: Any = "",
    new_body: Optional[str] = None,
    force: bool = False,
) -> Optional[int]:
    """Сохранить прежнее состояние перед перезаписью. Возвращает id версии или None."""
    try:
        nid = int(note_id)
        sid = int(sheet_id or 0)
        body = str(body or "")
        if not body.strip() and not str(title or "").strip():
            return None
        with _LOCK:
            conn = _conn()
            last = conn.execute(
                "SELECT body, title, saved_at FROM note_versions WHERE note_id = ? AND sheet_id = ? ORDER BY id DESC LIMIT 1",
                (nid, sid),
            ).fetchone()
            if last is not None and last["body"] == body and last["title"] == str(title or ""):
                return None  # такое состояние уже сохранено
            if not force and last is not None and not _big_change(body, new_body):
                at = _parse(last["saved_at"])
                if at and _now() - at < THROTTLE:
                    return None
            cur = conn.execute(
                """
                INSERT INTO note_versions (
                    owner_user_id, note_id, sheet_id, sheet_title, title, body,
                    revision, reason, author_user_id, saved_at, state_at, sheet_kind
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(owner_user_id or ""),
                    nid,
                    sid,
                    str(sheet_title or ""),
                    str(title or ""),
                    body,
                    int(revision or 0),
                    str(reason or "edit"),
                    str(author_user_id or ""),
                    _now().isoformat(),
                    str(state_at or ""),
                    str(sheet_kind or ""),
                ),
            )
            vid = int(cur.lastrowid)
            keep = KEEP_PER_ITEM_LARGE if len(body) > LARGE_BODY else KEEP_PER_ITEM
            conn.execute(
                """
                DELETE FROM note_versions
                WHERE note_id = ? AND sheet_id = ? AND id NOT IN (
                    SELECT id FROM note_versions WHERE note_id = ? AND sheet_id = ?
                    ORDER BY id DESC LIMIT ?
                )
                """,
                (nid, sid, nid, sid, keep),
            )
            conn.commit()
            return vid
    except Exception as e:  # никогда не мешаем сохранению заметки
        print(f"[note_versions] record failed note={note_id} sheet={sheet_id} err={e!r}")
        return None


def list_versions(note_id: Any, *, limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT id, note_id, sheet_id, sheet_title, sheet_kind, title, length(body) AS size,
                   revision, reason, author_user_id, saved_at, state_at
            FROM note_versions WHERE note_id = ? ORDER BY id DESC LIMIT ?
            """,
            (int(note_id), max(1, min(int(limit), 500))),
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(note_id: Any, version_id: Any) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM note_versions WHERE note_id = ? AND id = ?",
            (int(note_id), int(version_id)),
        ).fetchone()
    return dict(row) if row else None
