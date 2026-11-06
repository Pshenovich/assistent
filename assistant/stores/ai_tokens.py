"""Личные токены доступа для ИИ-агентов (Claude Code и др.): MCP базы знаний, только чтение.

Токен показывается один раз при создании; в базе — только его хэш и первые символы (чтобы узнать в списке).
Токен видит ровно то, что видит человек, и только базу знаний — не документы вообще, не почту, не сейфы.
Права: kb:read — только чтение; kb:write — ещё создавать разделы и документы и править документы там, где
человек редактор (удалять и менять доступ — нет). Права токена не меняются — только выпустить новый.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

PREFIX = "orb_"
SCOPES = ("kb:read", "kb:write")
MAX_ACTIVE = 10
NAME_MAX = 60
_USED_EVERY = timedelta(minutes=5)  # last_used_at обновляем не чаще — без записи на каждый вызов

_SCHEMA_CONN: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _SCHEMA_CONN
    conn = notes_store._conn()  # type: ignore[attr-defined]
    if _SCHEMA_CONN is conn:
        return conn
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ai_tokens (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, "
        "name TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, hint TEXT NOT NULL, scope TEXT NOT NULL DEFAULT 'kb:read', "
        "created_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_tokens_user ON ai_tokens(user_id)")
    conn.commit()
    _SCHEMA_CONN = conn
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _public(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "name": str(r["name"]),
        "hint": str(r["hint"]),
        "scope": str(r["scope"]),
        "created_at": r["created_at"],
        "last_used_at": r["last_used_at"],
    }


def create(user_id: int | str, name: str = "", *, scope: str = "kb:read") -> dict[str, Any]:
    """Новый токен. ``token`` в ответе — единственный раз, когда он виден целиком."""
    uid = str(int(user_id))
    if scope not in SCOPES:
        raise ValueError("Неизвестный доступ токена")
    title = " ".join(str(name or "").split())[:NAME_MAX] or "Claude"
    raw = PREFIX + secrets.token_urlsafe(32)
    with _LOCK:
        conn = _conn()
        active = conn.execute(
            "SELECT COUNT(*) FROM ai_tokens WHERE user_id = ? AND revoked_at IS NULL", (uid,)
        ).fetchone()[0]
        if active >= MAX_ACTIVE:
            raise ValueError(f"Не больше {MAX_ACTIVE} токенов — отзовите ненужный")
        cur = conn.execute(
            "INSERT INTO ai_tokens (user_id, name, token_hash, hint, scope, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, title, _hash(raw), raw[: len(PREFIX) + 4] + "…", scope, _now().isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_tokens WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {**_public(row), "token": raw}


def list_for(user_id: int | str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM ai_tokens WHERE user_id = ? AND revoked_at IS NULL ORDER BY id DESC", (str(int(user_id)),)
        ).fetchall()
    return [_public(r) for r in rows]


def revoke(user_id: int | str, token_id: int) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "UPDATE ai_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
            (_now().isoformat(), int(token_id), str(int(user_id))),
        )
        conn.commit()
        return bool(cur.rowcount)


def resolve(raw: str) -> Optional[int]:
    """Токен → id пользователя (или None — неверный или отозван). Отмечает «использовался»."""
    hit = resolve_full(raw)
    return hit[0] if hit else None


def resolve_full(raw: str) -> Optional[tuple[int, str]]:
    """Токен → (id пользователя, доступ kb:read | kb:write) или None."""
    raw = str(raw or "").strip()
    if not raw.startswith(PREFIX) or len(raw) > 120:
        return None
    now = _now()
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT * FROM ai_tokens WHERE token_hash = ? AND revoked_at IS NULL", (_hash(raw),)
        ).fetchone()
        if not row:
            return None
        last = row["last_used_at"]
        if not last or datetime.fromisoformat(str(last)) < now - _USED_EVERY:
            conn.execute("UPDATE ai_tokens SET last_used_at = ? WHERE id = ?", (now.isoformat(), row["id"]))
            conn.commit()
    try:
        return int(row["user_id"]), str(row["scope"] or "kb:read")
    except (TypeError, ValueError):
        return None


def revoke_all(user_id: int | str) -> int:
    """При выходе из аккаунта / смене владельца — отозвать всё."""
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "UPDATE ai_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now().isoformat(), str(int(user_id))),
        )
        conn.commit()
        return int(cur.rowcount or 0)
