"""Подключённые почтовые ящики пользователя.

Яндекс — через OAuth-токен Телемоста (auth=oauth_yandex, пароль не храним); Gmail, Mail.ru и любой IMAP —
через пароль приложения, он хранится зашифрованным (Fernet, ключ MAIL_SECRET_KEY или файл рядом с базой).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None
_FERNET: Any = None

FIELDS = (
    "account_id", "provider", "email", "auth", "imap_host", "imap_port", "smtp_host", "smtp_port",
    "send_enabled", "status", "last_error", "last_sync_at", "created_at", "stats_json",
)


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
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS mail_accounts (
            owner_user_id TEXT NOT NULL,
            account_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            email TEXT NOT NULL,
            auth TEXT NOT NULL,
            imap_host TEXT NOT NULL DEFAULT '',
            imap_port INTEGER NOT NULL DEFAULT 993,
            smtp_host TEXT NOT NULL DEFAULT '',
            smtp_port INTEGER NOT NULL DEFAULT 465,
            secret_enc TEXT NOT NULL DEFAULT '',
            send_enabled INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            last_error TEXT NOT NULL DEFAULT '',
            last_sync_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            stats_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (owner_user_id, account_id)
        )
        """
    )
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS mail_settings (owner_user_id TEXT PRIMARY KEY, signature TEXT NOT NULL DEFAULT '')"
    )
    for col, ddl in (("memory_enabled", "INTEGER NOT NULL DEFAULT 1"), ("view", "TEXT NOT NULL DEFAULT 'chat'")):
        try:
            _CONN.execute(f"ALTER TABLE mail_settings ADD COLUMN {col} {ddl}")
        except sqlite3.OperationalError:
            pass  # колонка уже есть
    _CONN.commit()
    return _CONN


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fernet() -> Any:
    global _FERNET
    if _FERNET is not None:
        return _FERNET
    from cryptography.fernet import Fernet

    key = os.getenv("MAIL_SECRET_KEY", "").strip()
    if not key:
        from assistant.stores import notes as notes_store

        path = Path(notes_store._db_path()).parent / "mail_secret.key"
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
        else:
            key = Fernet.generate_key().decode()
            path.write_text(key, encoding="utf-8")
            os.chmod(path, 0o600)
    _FERNET = Fernet(key.encode())
    return _FERNET


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode() if secret else ""


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode("utf-8") if token else ""


def account_id_for(email: str) -> str:
    return "m_" + hashlib.sha1(email.strip().lower().encode()).hexdigest()[:10]


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = {k: r[k] for k in FIELDS}
    d["send_enabled"] = bool(d["send_enabled"])
    try:
        d["stats"] = json.loads(d.pop("stats_json") or "{}")
    except (TypeError, ValueError):
        d["stats"] = {}
    return d


def upsert(
    owner_user_id: Any, *, provider: str, email: str, auth: str, imap_host: str = "", imap_port: int = 993,
    smtp_host: str = "", smtp_port: int = 465, secret: str = "", send_enabled: bool = False,
) -> dict[str, Any]:
    owner, aid = str(owner_user_id), account_id_for(email)
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO mail_accounts (owner_user_id, account_id, provider, email, auth, imap_host, imap_port,
                smtp_host, smtp_port, secret_enc, send_enabled, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
            ON CONFLICT(owner_user_id, account_id) DO UPDATE SET
                provider = excluded.provider, auth = excluded.auth, imap_host = excluded.imap_host,
                imap_port = excluded.imap_port, smtp_host = excluded.smtp_host, smtp_port = excluded.smtp_port,
                secret_enc = excluded.secret_enc, send_enabled = excluded.send_enabled, status = 'ok', last_error = ''
            """,
            (owner, aid, provider, email.strip().lower(), auth, imap_host, int(imap_port), smtp_host, int(smtp_port),
             encrypt(secret), int(bool(send_enabled)), _now()),
        )
        conn.commit()
    return get(owner, aid) or {}


def get(owner_user_id: Any, account_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM mail_accounts WHERE owner_user_id = ? AND account_id = ?", (str(owner_user_id), account_id)
        ).fetchone()
    return _row(r) if r else None


def list_accounts(owner_user_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM mail_accounts WHERE owner_user_id = ? ORDER BY created_at", (str(owner_user_id),)
        ).fetchall()
    return [_row(r) for r in rows]


def owners() -> list[int]:
    with _LOCK:
        rows = _conn().execute("SELECT DISTINCT owner_user_id FROM mail_accounts").fetchall()
    out = []
    for r in rows:
        try:
            out.append(int(r["owner_user_id"]))
        except (TypeError, ValueError):
            continue
    return out


def secret(owner_user_id: Any, account_id: str) -> str:
    with _LOCK:
        r = _conn().execute(
            "SELECT secret_enc FROM mail_accounts WHERE owner_user_id = ? AND account_id = ?",
            (str(owner_user_id), account_id),
        ).fetchone()
    return decrypt(str(r["secret_enc"] or "")) if r else ""


def set_status(owner_user_id: Any, account_id: str, *, status: str, error: str = "", stats: Optional[dict] = None,
               synced: bool = False) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE mail_accounts SET status = ?, last_error = ?" + (", last_sync_at = ?" if synced else "")
            + (", stats_json = ?" if stats is not None else "") + " WHERE owner_user_id = ? AND account_id = ?",
            tuple([status, error[:300]] + ([_now()] if synced else [])
                  + ([json.dumps(stats, ensure_ascii=False)] if stats is not None else [])
                  + [str(owner_user_id), account_id]),
        )
        conn.commit()


def set_send_enabled(owner_user_id: Any, account_id: str, enabled: bool) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE mail_accounts SET send_enabled = ? WHERE owner_user_id = ? AND account_id = ?",
            (int(bool(enabled)), str(owner_user_id), account_id),
        )
        conn.commit()


def remove(owner_user_id: Any, account_id: str) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "DELETE FROM mail_accounts WHERE owner_user_id = ? AND account_id = ?", (str(owner_user_id), account_id)
        )
        conn.commit()
    return cur.rowcount > 0


def public(acc: dict[str, Any]) -> dict[str, Any]:
    """Для клиента: без секретов."""
    return {k: acc.get(k) for k in ("account_id", "provider", "email", "auth", "send_enabled", "status",
                                    "last_error", "last_sync_at", "stats")}


def signature(owner_user_id: Any) -> str:
    with _LOCK:
        r = _conn().execute("SELECT signature FROM mail_settings WHERE owner_user_id = ?", (str(owner_user_id),)).fetchone()
    return str(r["signature"] or "") if r else ""


def set_signature(owner_user_id: Any, text: str) -> str:
    text = str(text or "").strip()[:1000]
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO mail_settings (owner_user_id, signature) VALUES (?, ?)"
            " ON CONFLICT(owner_user_id) DO UPDATE SET signature = excluded.signature",
            (str(owner_user_id), text),
        )
        conn.commit()
    return text


VIEWS = ("chat", "letters")


def settings(owner_user_id: Any) -> dict[str, Any]:
    """Настройки почты пользователя: подпись, «Память переписки», вид раздела «Почта» (chat | letters)."""
    with _LOCK:
        r = _conn().execute("SELECT * FROM mail_settings WHERE owner_user_id = ?", (str(owner_user_id),)).fetchone()
    if not r:
        return {"signature": "", "memory_enabled": True, "view": "chat"}
    return {"signature": str(r["signature"] or ""), "memory_enabled": bool(r["memory_enabled"]),
            "view": r["view"] if r["view"] in VIEWS else "chat"}


def update_settings(owner_user_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    cur = settings(owner_user_id)
    if "signature" in patch:
        cur["signature"] = str(patch["signature"] or "").strip()[:1000]
    if "memory_enabled" in patch:
        cur["memory_enabled"] = bool(patch["memory_enabled"])
    if patch.get("view") in VIEWS:
        cur["view"] = patch["view"]
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO mail_settings (owner_user_id, signature, memory_enabled, view) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(owner_user_id) DO UPDATE SET signature = excluded.signature,"
            " memory_enabled = excluded.memory_enabled, view = excluded.view",
            (str(owner_user_id), cur["signature"], int(cur["memory_enabled"]), cur["view"]),
        )
        conn.commit()
    return cur
