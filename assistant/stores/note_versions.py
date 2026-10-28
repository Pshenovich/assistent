"""История версий заметок и листов (отдельная база data/note_versions.sqlite).

Перед перезаписью текста сохраняем ПРЕЖНЕЕ состояние, не чаще раза в 10 минут
на заметку/лист, но всегда — при крупном изменении (текст сократился/вырос на треть),
при восстановлении версии и перед удалением листа или заметки. Запись версии
никогда не мешает сохранению: все ошибки глотаются.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from assistant.lib import sqlite_conn

PRIMARY_SHEET = 0  # «Основная» вкладка заметки
THROTTLE = timedelta(minutes=10)
KEEP_PER_ITEM = 60
KEEP_PER_ITEM_LARGE = 10
LARGE_BODY = 300_000

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None
TRASH_DAYS = 30

# Кто и как сейчас правит (ставится на запрос мини-приложения): (user_id, source).
# source: "" — правка человека, "ai" — правка ИИ из обсуждения/запроса к заметке.
_EDIT_CTX: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar("leo_note_edit_ctx", default=("", ""))


def set_edit_context(actor_user_id: Any, source: str = "") -> None:
    src = str(source or "").strip().lower()
    _EDIT_CTX.set((str(actor_user_id or ""), src if src in ("ai",) else ""))


def _db_path() -> Path:
    raw = (os.getenv("NOTE_VERSIONS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    notes_raw = (os.getenv("NOTES_DB_PATH") or "").strip()
    if notes_raw:
        notes_path = Path(notes_raw)
        if not notes_path.is_absolute():
            notes_path = ROOT / notes_path
        # Тесты (база заметок во временной папке) — рядом с ней; в установке — всегда data/
        # (data/*.sqlite деплой не трогает и не затирает).
        if notes_path.resolve().parent != ROOT.resolve():
            return notes_path.with_name("note_versions.sqlite")
    return ROOT / "data" / "note_versions.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    path = str(_db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite_conn.connect(path)
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
    cols = {r[1] for r in _CONN.execute("PRAGMA table_info(note_versions)").fetchall()}
    if "source" not in cols:
        _CONN.execute("ALTER TABLE note_versions ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    if "extra_json" not in cols:
        _CONN.execute("ALTER TABLE note_versions ADD COLUMN extra_json TEXT NOT NULL DEFAULT ''")
    # Автор текущего состояния каждой вкладки: при следующей перезаписи он станет автором версии.
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS item_state (
            note_id INTEGER NOT NULL,
            sheet_id INTEGER NOT NULL DEFAULT 0,
            author_user_id TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (note_id, sheet_id)
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_versions_owner_reason ON note_versions(owner_user_id, reason, saved_at)"
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
    extra: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """Сохранить прежнее состояние перед перезаписью. Возвращает id версии или None.

    Автор версии — тот, кто написал это (прежнее) состояние; после записи автором
    текущего состояния становится тот, кто правит сейчас (кроме удаления).
    """
    vid = _record(
        owner_user_id=owner_user_id, note_id=note_id, title=title, body=body,
        revision=revision, state_at=state_at, sheet_id=sheet_id, sheet_title=sheet_title,
        sheet_kind=sheet_kind, reason=reason, author_user_id=author_user_id,
        new_body=new_body, force=force, extra=extra,
    )
    if reason not in ("note_deleted", "sheet_deleted"):
        mark_current(note_id, sheet_id)
    return vid


def mark_current(note_id: Any, sheet_id: Any = PRIMARY_SHEET) -> None:
    """Запомнить, кто написал текущее состояние вкладки."""
    actor, source = _EDIT_CTX.get()
    if not actor:
        return
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                """
                INSERT INTO item_state (note_id, sheet_id, author_user_id, source, at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(note_id, sheet_id) DO UPDATE SET
                    author_user_id = excluded.author_user_id, source = excluded.source, at = excluded.at
                """,
                (int(note_id), int(sheet_id or 0), actor, source, _now().isoformat()),
            )
            conn.commit()
    except Exception as e:
        print(f"[note_versions] mark_current failed note={note_id} sheet={sheet_id} err={e!r}")


def _record(
    *,
    owner_user_id: Any,
    note_id: Any,
    title: str,
    body: str,
    revision: Any,
    state_at: str,
    sheet_id: int,
    sheet_title: str,
    sheet_kind: str,
    reason: str,
    author_user_id: Any,
    new_body: Optional[str],
    force: bool,
    extra: Optional[dict[str, Any]],
) -> Optional[int]:
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
            if last is not None and last["body"] == body and last["title"] == str(title or "") and not force:
                return None  # такое состояние уже сохранено
            st = conn.execute(
                "SELECT author_user_id, source FROM item_state WHERE note_id = ? AND sheet_id = ?",
                (nid, sid),
            ).fetchone()
            author = str((st["author_user_id"] if st else "") or author_user_id or "")
            source = str((st["source"] if st else "") or "")
            if not force and last is not None and not _big_change(body, new_body):
                at = _parse(last["saved_at"])
                if at and _now() - at < THROTTLE:
                    return None
            cur = conn.execute(
                """
                INSERT INTO note_versions (
                    owner_user_id, note_id, sheet_id, sheet_title, title, body,
                    revision, reason, author_user_id, saved_at, state_at, sheet_kind,
                    source, extra_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    author,
                    _now().isoformat(),
                    str(state_at or ""),
                    str(sheet_kind or ""),
                    source,
                    json.dumps(extra, ensure_ascii=False) if extra else "",
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
                   revision, reason, author_user_id, source, saved_at, state_at
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


def current_states(note_id: Any) -> dict[int, dict[str, Any]]:
    """Кто написал текущее состояние каждой вкладки заметки: {sheet_id: {...}}."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT sheet_id, author_user_id, source, at FROM item_state WHERE note_id = ?",
            (int(note_id),),
        ).fetchall()
    return {int(r["sheet_id"]): dict(r) for r in rows}


def list_deleted_notes(owner_user_id: Any, *, days: int = TRASH_DAYS) -> list[dict[str, Any]]:
    """Удалённые заметки владельца за последние days дней (последний снимок «Основной»)."""
    since = (_now() - timedelta(days=days)).isoformat()
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT v.id, v.note_id, v.title, v.saved_at, length(v.body) AS size,
                   substr(v.body, 1, 4000) AS head,
                   (SELECT COUNT(DISTINCT s.sheet_id) FROM note_versions s
                    WHERE s.note_id = v.note_id AND s.reason = 'note_deleted' AND s.sheet_id != 0) AS sheets
            FROM note_versions v
            WHERE v.owner_user_id = ? AND v.reason = 'note_deleted' AND v.sheet_id = 0 AND v.saved_at >= ?
              AND v.id = (SELECT MAX(id) FROM note_versions x
                          WHERE x.note_id = v.note_id AND x.sheet_id = 0 AND x.reason = 'note_deleted')
            ORDER BY v.saved_at DESC
            LIMIT 200
            """,
            (str(owner_user_id), since),
        ).fetchall()
    return [dict(r) for r in rows]


def deleted_note_snapshot(owner_user_id: Any, note_id: Any) -> Optional[dict[str, Any]]:
    """Снимок удалённой заметки: «Основная» + листы (последний снимок каждого)."""
    nid = int(note_id)
    with _LOCK:
        conn = _conn()
        main = conn.execute(
            """
            SELECT * FROM note_versions
            WHERE owner_user_id = ? AND note_id = ? AND sheet_id = 0 AND reason = 'note_deleted'
            ORDER BY id DESC LIMIT 1
            """,
            (str(owner_user_id), nid),
        ).fetchone()
        if main is None:
            return None
        sheets = conn.execute(
            """
            SELECT * FROM note_versions v
            WHERE note_id = ? AND sheet_id != 0 AND reason = 'note_deleted'
              AND id = (SELECT MAX(id) FROM note_versions x
                        WHERE x.note_id = v.note_id AND x.sheet_id = v.sheet_id AND x.reason = 'note_deleted')
            ORDER BY sheet_id
            """,
            (nid,),
        ).fetchall()
    out = dict(main)
    try:
        out["extra"] = json.loads(out.get("extra_json") or "{}") or {}
    except ValueError:
        out["extra"] = {}
    out["sheets"] = [dict(r) for r in sheets]
    return out


def mark_note_restored(note_id: Any) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE note_versions SET reason = 'note_restored' WHERE note_id = ? AND reason = 'note_deleted'",
            (int(note_id),),
        )
        conn.commit()
