"""Публичные ссылки на одну заметку / запись журнала (саммари, транскрипция)."""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_VALID_KINDS = frozenset({"local", "journal"})
_VALID_ACCESS = frozenset({"view", "comment"})
_DEFAULT_ACCESS = "view"
_TOKEN_BYTES = 24


def normalize_access(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in ("comments", "comment"):
        return "comment"
    if value in _VALID_ACCESS:
        return value
    return _DEFAULT_ACCESS


def _ensure_access_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(share_links)")}
    if "access" not in cols:
        conn.execute(
            "ALTER TABLE share_links ADD COLUMN access TEXT NOT NULL DEFAULT 'view'"
        )
        conn.commit()


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS share_links (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            access TEXT NOT NULL DEFAULT 'view'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_share_links_item "
        "ON share_links(user_id, item_kind, item_id)"
    )
    _ensure_access_column(conn)
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    access = normalize_access(row["access"] if "access" in keys else _DEFAULT_ACCESS)
    return {
        "token": str(row["token"] or ""),
        "user_id": str(row["user_id"] or ""),
        "item_kind": str(row["item_kind"] or ""),
        "item_id": str(row["item_id"] or ""),
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
        "access": access,
    }


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def get_active_share(
    user_id: int | str, item_kind: str, item_id: str | int
) -> Optional[dict[str, Any]]:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        return None
    uid = _uid(user_id)
    iid = str(item_id).strip()
    if not iid:
        return None
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM share_links
            WHERE user_id = ? AND item_kind = ? AND item_id = ? AND revoked_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (uid, kind, iid),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def update_share_access(
    user_id: int | str, item_kind: str, item_id: str | int, access: str
) -> Optional[dict[str, Any]]:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        return None
    acc = normalize_access(access)
    if acc not in _VALID_ACCESS:
        raise ValueError("Некорректный уровень доступа")
    uid = _uid(user_id)
    iid = str(item_id).strip()
    if not iid:
        return None
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE share_links
            SET access = ?
            WHERE user_id = ? AND item_kind = ? AND item_id = ? AND revoked_at IS NULL
            """,
            (acc, uid, kind, iid),
        )
        _conn().commit()
        if cur.rowcount <= 0:
            return None
    return get_active_share(uid, kind, iid)


def create_or_get_share(
    user_id: int | str,
    item_kind: str,
    item_id: str | int,
    access: str | None = None,
) -> dict[str, Any]:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        raise ValueError("Некорректный тип записи для ссылки")
    uid = _uid(user_id)
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Не указан идентификатор записи")
    existing = get_active_share(uid, kind, iid)
    if existing:
        if access is None:
            return existing
        acc = normalize_access(access)
        if acc == existing.get("access"):
            return existing
        updated = update_share_access(uid, kind, iid, acc)
        return updated or existing
    acc = normalize_access(access)
    token = _new_token()
    ts = _now_iso()
    with _LOCK:
        _conn().execute(
            """
            INSERT INTO share_links (
                token, user_id, item_kind, item_id, created_at, revoked_at, access
            )
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (token, uid, kind, iid, ts, acc),
        )
        _conn().commit()
    return {
        "token": token,
        "user_id": uid,
        "item_kind": kind,
        "item_id": iid,
        "created_at": ts,
        "revoked_at": None,
        "access": acc,
    }


def resolve_share(token: str) -> Optional[dict[str, Any]]:
    raw = (token or "").strip()
    if not raw or len(raw) < 16 or len(raw) > 80:
        return None
    if any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-" for c in raw):
        return None
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM share_links WHERE token = ? AND revoked_at IS NULL",
            (raw,),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def revoke_share(user_id: int | str, item_kind: str, item_id: str | int) -> bool:
    kind = (item_kind or "").strip()
    if kind not in _VALID_KINDS:
        return False
    uid = _uid(user_id)
    iid = str(item_id).strip()
    if not iid:
        return False
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE share_links
            SET revoked_at = ?
            WHERE user_id = ? AND item_kind = ? AND item_id = ? AND revoked_at IS NULL
            """,
            (ts, uid, kind, iid),
        )
        _conn().commit()
        return cur.rowcount > 0


def revoke_all_for_item(user_id: int | str, item_kind: str, item_id: str | int) -> int:
    return 1 if revoke_share(user_id, item_kind, item_id) else 0
