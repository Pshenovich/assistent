"""Web Push подписки пользователей миниаппа."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from assistant.lib import sqlite_conn

_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None


def _db_path() -> Path:
    raw = (os.getenv("PUSH_SUBSCRIPTIONS_DB_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "data" / "push_subscriptions.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite_conn.connect(str(path))
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            endpoint TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            updated_at REAL NOT NULL
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_push_subs_user "
        "ON push_subscriptions(user_id)"
    )
    _CONN.commit()
    return _CONN


def reset_connection() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            try:
                _CONN.close()
            except Exception:
                pass
            _CONN = None


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "endpoint": str(row["endpoint"] or ""),
        "user_id": str(row["user_id"] or ""),
        "p256dh": str(row["p256dh"] or ""),
        "auth": str(row["auth"] or ""),
        "user_agent": str(row["user_agent"] or ""),
        "updated_at": float(row["updated_at"] or 0),
    }


def upsert(
    user_id: int | str,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str = "",
) -> dict[str, Any]:
    uid = str(user_id).strip()
    ep = str(endpoint or "").strip()
    p256 = str(p256dh or "").strip()
    secret = str(auth or "").strip()
    if not uid or not ep or not p256 or not secret:
        raise ValueError("Неполная подписка Web Push")
    now = time.time()
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO push_subscriptions
                (endpoint, user_id, p256dh, auth, user_agent, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
                user_id = excluded.user_id,
                p256dh = excluded.p256dh,
                auth = excluded.auth,
                user_agent = excluded.user_agent,
                updated_at = excluded.updated_at
            """,
            (ep, uid, p256, secret, str(user_agent or "")[:400], now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM push_subscriptions WHERE endpoint = ?", (ep,)
        ).fetchone()
    assert row is not None
    return _row(row)


def delete(endpoint: str, *, user_id: int | str | None = None) -> bool:
    ep = str(endpoint or "").strip()
    if not ep:
        return False
    with _LOCK:
        conn = _conn()
        if user_id is None:
            cur = conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ?", (ep,)
            )
        else:
            cur = conn.execute(
                "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
                (ep, str(user_id).strip()),
            )
        conn.commit()
        return cur.rowcount > 0


def list_for_user(user_id: int | str) -> list[dict[str, Any]]:
    uid = str(user_id).strip()
    if not uid:
        return []
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY updated_at DESC",
            (uid,),
        ).fetchall()
    return [_row(r) for r in rows]
