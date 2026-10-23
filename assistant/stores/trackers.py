"""Внешние трекеры: доска и стадия по умолчанию, доступы (вебхук — зашифрован), заведённые задачи.

Задачи трекеров живут только в трекере: здесь — ссылка на них (для чипа в заметке, бейджа
«уже в Битрикс24» у задачи Орбиты и будущей синхронизации). В календарь и Фокус они не попадают.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Optional

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
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.executescript(
        """
        CREATE TABLE IF NOT EXISTS tracker_settings (
            owner_user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            default_board_id TEXT NOT NULL DEFAULT '',
            default_board_name TEXT NOT NULL DEFAULT '',
            default_stage_id TEXT NOT NULL DEFAULT '',
            default_stage_name TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, provider)
        );
        CREATE TABLE IF NOT EXISTS tracker_credentials (
            owner_user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            kind TEXT NOT NULL,
            secret_enc TEXT NOT NULL DEFAULT '',
            portal TEXT NOT NULL DEFAULT '',
            account_id TEXT NOT NULL DEFAULT '',
            account_name TEXT NOT NULL DEFAULT '',
            saved_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, provider)
        );
        CREATE TABLE IF NOT EXISTS external_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            board_id TEXT NOT NULL DEFAULT '',
            board_name TEXT NOT NULL DEFAULT '',
            stage_id TEXT NOT NULL DEFAULT '',
            stage_name TEXT NOT NULL DEFAULT '',
            note_id TEXT NOT NULL DEFAULT '',
            calendar_task_id TEXT NOT NULL DEFAULT '',
            focus_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_external_tasks_owner_cal
            ON external_tasks(owner_user_id, calendar_task_id);
        """
    )
    _CONN.commit()
    return _CONN


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Настройки по умолчанию ---

def get_settings(uid: Any, provider: str) -> dict[str, str]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM tracker_settings WHERE owner_user_id=? AND provider=?", (str(uid), provider)
        ).fetchone()
    if not row:
        return {"board_id": "", "board_name": "", "stage_id": "", "stage_name": ""}
    return {
        "board_id": row["default_board_id"],
        "board_name": row["default_board_name"],
        "stage_id": row["default_stage_id"],
        "stage_name": row["default_stage_name"],
    }


def save_settings(
    uid: Any, provider: str, *, board_id: str, board_name: str = "", stage_id: str = "", stage_name: str = ""
) -> dict[str, str]:
    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO tracker_settings
                (owner_user_id, provider, default_board_id, default_board_name,
                 default_stage_id, default_stage_name, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, provider) DO UPDATE SET
                default_board_id=excluded.default_board_id,
                default_board_name=excluded.default_board_name,
                default_stage_id=excluded.default_stage_id,
                default_stage_name=excluded.default_stage_name,
                updated_at=excluded.updated_at
            """,
            (str(uid), provider, str(board_id or ""), str(board_name or "")[:200],
             str(stage_id or ""), str(stage_name or "")[:200], _now()),
        )
        c.commit()
    return get_settings(uid, provider)


# --- Доступы (секрет шифруется тем же ключом, что пароли почты) ---

def save_credentials(
    uid: Any, provider: str, *, kind: str, secret: str, portal: str = "", account_id: str = "", account_name: str = ""
) -> None:
    from assistant.stores.mail_accounts import encrypt

    with _LOCK:
        c = _conn()
        c.execute(
            """
            INSERT INTO tracker_credentials
                (owner_user_id, provider, kind, secret_enc, portal, account_id, account_name, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, provider) DO UPDATE SET
                kind=excluded.kind, secret_enc=excluded.secret_enc, portal=excluded.portal,
                account_id=excluded.account_id, account_name=excluded.account_name, saved_at=excluded.saved_at
            """,
            (str(uid), provider, kind, encrypt(secret), portal, str(account_id or ""), account_name[:200], _now()),
        )
        c.commit()


def get_credentials(uid: Any, provider: str) -> Optional[dict[str, str]]:
    from assistant.stores.mail_accounts import decrypt

    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM tracker_credentials WHERE owner_user_id=? AND provider=?", (str(uid), provider)
        ).fetchone()
    if not row:
        return None
    try:
        secret = decrypt(row["secret_enc"])
    except Exception as e:
        print(f"[trackers] decrypt failed uid={uid} provider={provider} err={e!r}")
        return None
    return {
        "kind": row["kind"],
        "secret": secret,
        "portal": row["portal"],
        "account_id": row["account_id"],
        "account_name": row["account_name"],
    }


def delete_credentials(uid: Any, provider: str) -> bool:
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "DELETE FROM tracker_credentials WHERE owner_user_id=? AND provider=?", (str(uid), provider)
        )
        c.commit()
        return cur.rowcount > 0


# --- Заведённые задачи ---

_TASK_FIELDS = (
    "id", "provider", "external_id", "url", "title", "board_id", "board_name",
    "stage_id", "stage_name", "note_id", "calendar_task_id", "focus_key", "created_at",
)


def _task_public(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in _TASK_FIELDS}


def add_task(uid: Any, provider: str, *, external_id: str, url: str, title: str, **kw: Any) -> dict[str, Any]:
    vals = {k: str(kw.get(k) or "") for k in (
        "board_id", "board_name", "stage_id", "stage_name", "note_id", "calendar_task_id", "focus_key")}
    with _LOCK:
        c = _conn()
        cur = c.execute(
            """
            INSERT INTO external_tasks
                (owner_user_id, provider, external_id, url, title, board_id, board_name,
                 stage_id, stage_name, note_id, calendar_task_id, focus_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uid), provider, str(external_id), url, title[:500], vals["board_id"], vals["board_name"][:200],
             vals["stage_id"], vals["stage_name"][:200], vals["note_id"], vals["calendar_task_id"],
             vals["focus_key"], _now()),
        )
        c.commit()
        row = c.execute("SELECT * FROM external_tasks WHERE id=?", (cur.lastrowid,)).fetchone()
    return _task_public(row)


def tasks_for_calendar_task(uid: Any, calendar_task_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM external_tasks WHERE owner_user_id=? AND calendar_task_id=? ORDER BY id",
            (str(uid), str(calendar_task_id)),
        ).fetchall()
    return [_task_public(r) for r in rows]
