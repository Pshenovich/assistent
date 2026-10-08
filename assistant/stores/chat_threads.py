"""Самостоятельные AI-чаты в сабтабе «Чаты» (без привязки к заметке)."""

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

DEFAULT_TITLE = "Новый чат"
MAX_TITLE_LEN = 80
MAX_PREVIEW_LEN = 160


def _db_path() -> Path:
    raw = (os.getenv("CHAT_THREADS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "chat_threads.sqlite"


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
        CREATE TABLE IF NOT EXISTS chat_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT 'Новый чат',
            title_locked INTEGER NOT NULL DEFAULT 0,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_preview TEXT NOT NULL DEFAULT ''
        )
        """
    )
    cols = {str(r[1]) for r in _CONN.execute("PRAGMA table_info(chat_threads)")}
    if "pinned" not in cols:
        _CONN.execute(
            "ALTER TABLE chat_threads ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"
        )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_threads_owner_updated "
        "ON chat_threads(owner_user_id, updated_at DESC)"
    )
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_members (
            owner_user_id TEXT NOT NULL,
            thread_id INTEGER NOT NULL,
            member_user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'edit',
            added_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, thread_id, member_user_id)
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_members_member "
        "ON chat_members(member_user_id)"
    )
    _CONN.commit()
    return _CONN


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _clip(raw: str | None, limit: int) -> str:
    return str(raw or "").replace("\u0000", "").strip()[:limit]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "title": str(row["title"] or DEFAULT_TITLE),
        "title_locked": bool(int(row["title_locked"] or 0)),
        "pinned": bool(int(row["pinned"] if "pinned" in keys else 0)),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_preview": str(row["last_preview"] or ""),
    }


def _attach_message_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return rows
    try:
        from assistant.stores import share_comments

        keys = [
            (str(r.get("owner_user_id") or ""), "chat", str(r.get("id") or ""))
            for r in rows
            if r.get("id") is not None
        ]
        stats = share_comments.comment_activity(keys)
    except Exception:
        stats = {}
    for row in rows:
        owner = str(row.get("owner_user_id") or "")
        tid = str(row.get("id") or "")
        act = stats.get((owner, "chat", tid)) or {}
        count = int(act.get("count") or 0)
        row["has_messages"] = count > 0
        row["message_count"] = count
        if not str(row.get("last_preview") or "").strip():
            fallback = str(act.get("preview") or "").strip()
            if fallback:
                row["last_preview"] = fallback[:MAX_PREVIEW_LEN]
    return rows


def list_threads(owner_user_id: int | str, *, limit: int = 100) -> list[dict[str, Any]]:
    uid = _uid(owner_user_id)
    lim = max(1, min(int(limit or 100), 300))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT t.* FROM chat_threads t
            WHERE t.owner_user_id = ?
               OR EXISTS (
                    SELECT 1 FROM chat_members m
                    WHERE m.thread_id = t.id AND m.member_user_id = ?
               )
            ORDER BY t.pinned DESC, t.updated_at DESC, t.id DESC
            LIMIT ?
            """,
            (uid, uid, lim),
        )
        rows = [_row_to_dict(r) for r in cur.fetchall()]
    for row in rows:
        row["members"] = list_members_public(row["owner_user_id"], row["id"])
        row["is_owner"] = str(row.get("owner_user_id") or "") == uid
    return _attach_message_stats(rows)


def get_thread(owner_user_id: int | str, thread_id: int | str) -> Optional[dict[str, Any]]:
    """Только свои треды (владелец). Для доступа участника — get_accessible_thread."""
    uid = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM chat_threads WHERE id = ? AND owner_user_id = ?",
            (tid, uid),
        )
        row = cur.fetchone()
    if not row:
        return None
    item = _row_to_dict(row)
    item["members"] = list_members_public(uid, tid)
    item["is_owner"] = True
    _attach_message_stats([item])
    return item


def get_accessible_thread(
    user_id: int | str, thread_id: int | str
) -> Optional[dict[str, Any]]:
    uid = _uid(user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        cur = _conn().execute("SELECT * FROM chat_threads WHERE id = ?", (tid,))
        row = cur.fetchone()
    if not row:
        return None
    item = _row_to_dict(row)
    owner = str(item.get("owner_user_id") or "")
    if owner != uid and not is_member(owner, tid, uid):
        return None
    item["members"] = list_members_public(owner, tid)
    item["is_owner"] = owner == uid
    _attach_message_stats([item])
    return item


def create_thread(
    owner_user_id: int | str, *, title: str | None = None
) -> dict[str, Any]:
    uid = _uid(owner_user_id)
    now = _now_iso()
    title_s = _clip(title, MAX_TITLE_LEN) or DEFAULT_TITLE
    locked = 1 if title and _clip(title, MAX_TITLE_LEN) else 0
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO chat_threads (
                owner_user_id, title, title_locked, created_at, updated_at, last_preview
            ) VALUES (?, ?, ?, ?, ?, '')
            """,
            (uid, title_s, locked, now, now),
        )
        _conn().commit()
        tid = int(cur.lastrowid)
    item = get_thread(uid, tid)
    assert item is not None
    return item


def rename_thread(
    owner_user_id: int | str,
    thread_id: int | str,
    title: str,
    *,
    lock: bool = True,
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return None
    title_s = _clip(title, MAX_TITLE_LEN)
    if not title_s:
        raise ValueError("Введите название")
    now = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE chat_threads
            SET title = ?, title_locked = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (title_s, 1 if lock else 0, now, tid, uid),
        )
        _conn().commit()
        if cur.rowcount <= 0:
            return None
    return get_thread(uid, tid)


def set_pinned(
    owner_user_id: int | str,
    thread_id: int | str,
    pinned: bool,
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return None
    now = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE chat_threads
            SET pinned = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (1 if pinned else 0, now, tid, uid),
        )
        _conn().commit()
        if cur.rowcount <= 0:
            return None
    return get_thread(uid, tid)


def touch_preview(
    owner_user_id: int | str,
    thread_id: int | str,
    preview: str,
) -> Optional[dict[str, Any]]:
    uid = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return None
    prev = _clip(re.sub(r"\s+", " ", preview or ""), MAX_PREVIEW_LEN)
    if not prev:
        return get_thread(uid, tid)
    now = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE chat_threads
            SET last_preview = ?, updated_at = ?
            WHERE id = ? AND owner_user_id = ?
            """,
            (prev, now, tid, uid),
        )
        _conn().commit()
        if cur.rowcount <= 0:
            return None
    return get_thread(uid, tid)


def _fallback_title(message: str) -> str:
    raw = _clip(re.sub(r"\s+", " ", message or ""), MAX_TITLE_LEN)
    if not raw:
        return DEFAULT_TITLE
    if len(raw) > 48:
        return raw[:47].rstrip() + "…"
    return raw


def suggest_chat_title(message: str) -> str:
    """Короткий заголовок по первому сообщению; при ошибке LLM — обрезка текста."""
    text = _clip(message, 500)
    if not text:
        return DEFAULT_TITLE
    try:
        from assistant.integrations.openrouter_client import (
            is_llm_configured,
            openrouter_chat_completion,
        )

        if not is_llm_configured():
            return _fallback_title(text)
        model = (
            os.getenv("OPENROUTER_MODEL_ROUTER", "").strip()
            or "google/gemini-2.5-flash"
        )
        payload = {
            "model": model,
            "temperature": 0.2,
            "max_tokens": 40,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Придумай короткий заголовок чата на русском (3–6 слов). "
                        "Без кавычек, точек и эмодзи. Только сам заголовок."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        data = openrouter_chat_completion(
            payload, operation="chat_thread_title", timeout=30
        )
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        raw = str(msg.get("content") or "").strip()
        raw = raw.strip("«»\"'").split("\n")[0].strip()
        raw = _clip(re.sub(r"\s+", " ", raw), MAX_TITLE_LEN)
        if not raw or raw.lower() == DEFAULT_TITLE.lower():
            return _fallback_title(text)
        return raw
    except Exception:
        return _fallback_title(text)


def maybe_autotitle(
    owner_user_id: int | str,
    thread_id: int | str,
    first_message: str,
) -> Optional[dict[str, Any]]:
    thread = get_thread(owner_user_id, thread_id)
    if not thread:
        return None
    if thread.get("title_locked"):
        return thread
    if str(thread.get("title") or "").strip() != DEFAULT_TITLE:
        return thread
    title = suggest_chat_title(first_message)
    if not title or title == DEFAULT_TITLE:
        return thread
    return rename_thread(owner_user_id, thread_id, title, lock=False)


def after_comment(
    owner_user_id: int | str,
    thread_id: int | str,
    body: str,
    *,
    is_user: bool,
) -> Optional[dict[str, Any]]:
    thread = touch_preview(owner_user_id, thread_id, body)
    if not thread:
        return None
    if is_user:
        thread = maybe_autotitle(owner_user_id, thread_id, body) or thread
    return thread


def delete_thread(owner_user_id: int | str, thread_id: int | str) -> bool:
    uid = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return False
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM chat_threads WHERE id = ? AND owner_user_id = ?",
            (tid, uid),
        )
        _conn().commit()
        deleted = cur.rowcount > 0
    if deleted:
        with _LOCK:
            _conn().execute(
                "DELETE FROM chat_members WHERE owner_user_id = ? AND thread_id = ?",
                (uid, tid),
            )
            _conn().commit()
        try:
            from assistant.stores import share_comments

            share_comments.delete_all_for_item(uid, "chat", tid)
        except Exception as e:
            print(f"[chat_threads] delete comments failed thread={tid} err={e!r}")
        try:
            from assistant.stores import comment_files

            comment_files.delete_for_item(uid, "chat", tid)
        except Exception as e:
            print(f"[chat_threads] delete comment files failed thread={tid} err={e!r}")
    return deleted


def is_member(
    owner_user_id: int | str, thread_id: int | str, user_id: int | str
) -> bool:
    owner = _uid(owner_user_id)
    mid = _uid(user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return False
    if owner == mid:
        return True
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT 1 FROM chat_members
            WHERE owner_user_id = ? AND thread_id = ? AND member_user_id = ?
            """,
            (owner, tid, mid),
        )
        return cur.fetchone() is not None


def list_members_public(
    owner_user_id: int | str, thread_id: int | str
) -> list[dict[str, Any]]:
    from assistant.stores import note_members

    owner = _uid(owner_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return []
    owner_profile = note_members.get_profile(owner)
    out: list[dict[str, Any]] = [
        {
            "user_id": int(owner) if str(owner).isdigit() else owner,
            "is_owner": True,
            "role": "owner",
            "name": note_members.profile_display_name(owner_profile, fallback="Автор"),
            "username": str((owner_profile or {}).get("username") or ""),
            "color": note_members.member_color(owner),
        }
    ]
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT member_user_id, role FROM chat_members
            WHERE owner_user_id = ? AND thread_id = ?
            ORDER BY added_at ASC
            """,
            (owner, tid),
        )
        rows = cur.fetchall()
    for r in rows:
        mid = str(r["member_user_id"])
        role = str(r["role"] or "edit")
        prof = note_members.get_profile(mid)
        out.append(
            {
                "user_id": int(mid) if str(mid).isdigit() else mid,
                "is_owner": False,
                "role": role,
                "name": note_members.profile_display_name(prof, fallback="Участник"),
                "username": str((prof or {}).get("username") or ""),
                "color": note_members.member_color(mid),
            }
        )
    return out


def add_member(
    owner_user_id: int | str,
    thread_id: int | str,
    member_user_id: int | str,
    *,
    role: str = "edit",
) -> dict[str, Any]:
    from assistant.stores import note_members

    owner = _uid(owner_user_id)
    mid = _uid(member_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        raise ValueError("Чат не найден") from None
    if owner == mid:
        raise ValueError("Нельзя добавить владельца как участника")
    thread = get_thread(owner, tid)
    if not thread:
        raise ValueError("Чат не найден")
    member_role = (role or "edit").strip() or "edit"
    if member_role not in ("edit", "view", "comment"):
        raise ValueError("Некорректная роль")
    ts = _now_iso()
    with _LOCK:
        _conn().execute(
            """
            INSERT INTO chat_members (
                owner_user_id, thread_id, member_user_id, role, added_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, thread_id, member_user_id) DO UPDATE SET
                role = excluded.role
            """,
            (owner, tid, mid, member_role, ts),
        )
        _conn().commit()
    prof = note_members.get_profile(mid)
    return {
        "user_id": int(mid) if str(mid).isdigit() else mid,
        "is_owner": False,
        "role": member_role,
        "name": note_members.profile_display_name(prof, fallback="Участник"),
        "username": str((prof or {}).get("username") or ""),
        "color": note_members.member_color(mid),
    }


def remove_member(
    owner_user_id: int | str, thread_id: int | str, member_user_id: int | str
) -> bool:
    owner = _uid(owner_user_id)
    mid = _uid(member_user_id)
    try:
        tid = int(thread_id)
    except (TypeError, ValueError):
        return False
    if owner == mid:
        return False
    with _LOCK:
        cur = _conn().execute(
            """
            DELETE FROM chat_members
            WHERE owner_user_id = ? AND thread_id = ? AND member_user_id = ?
            """,
            (owner, tid, mid),
        )
        _conn().commit()
        return cur.rowcount > 0
