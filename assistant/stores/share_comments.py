"""Комментарии к расшаренным заметкам / записям журнала."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store
from assistant.stores import share_links as share_links_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_VALID_KINDS = frozenset({"local", "journal"})
MAX_BODY_LEN = 4000
MAX_QUOTE_LEN = 500
MAX_CTX_LEN = 80


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            author_user_id TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_username TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            quote TEXT NOT NULL DEFAULT '',
            prefix TEXT NOT NULL DEFAULT '',
            suffix TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_comments_item "
        "ON share_comments(owner_user_id, item_kind, item_id, created_at)"
    )
    _ensure_anchor_columns(conn)
    conn.commit()
    return conn


def _ensure_anchor_columns(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(share_comments)")}
    for col in ("quote", "prefix", "suffix"):
        if col not in cols:
            conn.execute(
                f"ALTER TABLE share_comments ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
            )
    conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _kind_id(item_kind: str, item_id: str | int) -> tuple[str, str]:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        raise ValueError("Некорректный тип записи")
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Не указан идентификатор записи")
    return kind, iid


def _clip(raw: str | None, limit: int) -> str:
    return str(raw or "").replace("\u0000", "")[:limit]


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "item_kind": str(row["item_kind"] or ""),
        "item_id": str(row["item_id"] or ""),
        "author_user_id": str(row["author_user_id"] or ""),
        "author_name": str(row["author_name"] or ""),
        "author_username": str(row["author_username"] or "") or None,
        "body": str(row["body"] or ""),
        "created_at": row["created_at"],
        "quote": str(row["quote"] if "quote" in keys else "") or "",
        "prefix": str(row["prefix"] if "prefix" in keys else "") or "",
        "suffix": str(row["suffix"] if "suffix" in keys else "") or "",
    }


def list_comments(
    owner_user_id: int | str, item_kind: str, item_id: str | int
) -> list[dict[str, Any]]:
    kind, iid = _kind_id(item_kind, item_id)
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM share_comments
            WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (uid, kind, iid),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_comment(comment_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM share_comments WHERE id = ?",
            (int(comment_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def add_comment(
    owner_user_id: int | str,
    item_kind: str,
    item_id: str | int,
    *,
    author_user_id: int | str,
    author_name: str,
    author_username: str | None = None,
    body: str,
    quote: str = "",
    prefix: str = "",
    suffix: str = "",
) -> dict[str, Any]:
    kind, iid = _kind_id(item_kind, item_id)
    text = (body or "").strip()
    if not text:
        raise ValueError("Введите текст комментария")
    if len(text) > MAX_BODY_LEN:
        raise ValueError(f"Комментарий слишком длинный (максимум {MAX_BODY_LEN} символов)")
    q = _clip(quote, MAX_QUOTE_LEN).strip()
    pre = _clip(prefix, MAX_CTX_LEN)
    suf = _clip(suffix, MAX_CTX_LEN)
    owner = _uid(owner_user_id)
    author = _uid(author_user_id)
    name = (author_name or "").strip() or "Пользователь"
    uname = (author_username or "").strip().lstrip("@") or None
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO share_comments (
                owner_user_id, item_kind, item_id,
                author_user_id, author_name, author_username,
                body, created_at, quote, prefix, suffix
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (owner, kind, iid, author, name[:120], uname, text, ts, q, pre, suf),
        )
        _conn().commit()
        cid = int(cur.lastrowid)
    item = get_comment(cid)
    if not item:
        raise RuntimeError("Не удалось сохранить комментарий")
    return item


def delete_comment(
    comment_id: int, *, requester_user_id: int | str
) -> bool:
    item = get_comment(comment_id)
    if not item:
        return False
    req = _uid(requester_user_id)
    if req != item["author_user_id"] and req != item["owner_user_id"]:
        return False
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM share_comments WHERE id = ?",
            (int(comment_id),),
        )
        _conn().commit()
        return cur.rowcount > 0


def delete_all_for_item(
    owner_user_id: int | str, item_kind: str, item_id: str | int
) -> int:
    try:
        kind, iid = _kind_id(item_kind, item_id)
    except ValueError:
        return 0
    uid = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            DELETE FROM share_comments
            WHERE owner_user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (uid, kind, iid),
        )
        _conn().commit()
        return int(cur.rowcount or 0)


def comments_allowed_for_link(link: dict[str, Any] | None) -> bool:
    if not link:
        return False
    return share_links_store.normalize_access(link.get("access")) == "comment"


def comments_visible_for_link(link: dict[str, Any] | None) -> bool:
    return bool(link)
