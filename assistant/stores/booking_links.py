"""Публичные ссылки на свободные слоты календаря."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_TOKEN_BYTES = 24
_VALID_MODES = frozenset({"one_shot", "reusable", "expiring"})
_VALID_DURATIONS = frozenset({15, 30, 45, 60})
_VALID_EXPIRE_DAYS = frozenset({7, 14, 30})
_TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
_DEFAULT_MODE = "reusable"
_DEFAULT_EXPIRE_DAYS = 14


def normalize_link_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower().replace("-", "_")
    aliases = {
        "oneshot": "one_shot",
        "one": "one_shot",
        "single": "one_shot",
        "multi": "reusable",
        "repeat": "reusable",
        "ttl": "expiring",
        "expire": "expiring",
        "expires": "expiring",
    }
    value = aliases.get(value, value)
    if value in _VALID_MODES:
        return value
    return _DEFAULT_MODE


def normalize_duration_min(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n in _VALID_DURATIONS:
        return n
    return None


def normalize_expire_days(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_EXPIRE_DAYS
    if n in _VALID_EXPIRE_DAYS:
        return n
    return _DEFAULT_EXPIRE_DAYS


def normalize_emails(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except Exception:
            raw = [p.strip() for p in text.split(",") if p.strip()]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            em = str(item.get("email") or "").strip().lower()
        else:
            em = str(item or "").strip().lower()
        if not em or "@" not in em or em in seen:
            continue
        seen.add(em)
        out.append(em)
    return out


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS booking_links (
            token TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            title TEXT,
            attendee_emails TEXT NOT NULL DEFAULT '[]',
            duration_min INTEGER,
            link_mode TEXT NOT NULL DEFAULT 'reusable',
            expires_at TEXT,
            used_at TEXT,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            source_chat_id TEXT,
            source_message_id TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_booking_links_owner "
        "ON booking_links(owner_user_id, created_at)"
    )
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(booking_links)")}
    if "source_chat_id" not in cols:
        conn.execute("ALTER TABLE booking_links ADD COLUMN source_chat_id TEXT")
    if "source_message_id" not in cols:
        conn.execute("ALTER TABLE booking_links ADD COLUMN source_message_id TEXT")
    conn.commit()
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def _token_ok(token: str) -> bool:
    raw = (token or "").strip()
    if not raw or len(raw) < 16 or len(raw) > 80:
        return False
    return all(c in _TOKEN_CHARS for c in raw)


def _int_or_none(raw: Any) -> int | None:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n else None


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "token": str(row["token"] or ""),
        "owner_user_id": str(row["owner_user_id"] or ""),
        "title": str(row["title"] or "").strip(),
        "attendee_emails": normalize_emails(row["attendee_emails"]),
        "duration_min": normalize_duration_min(row["duration_min"]),
        "link_mode": normalize_link_mode(row["link_mode"]),
        "expires_at": row["expires_at"],
        "used_at": row["used_at"],
        "created_at": row["created_at"],
        "revoked_at": row["revoked_at"],
        "source_chat_id": _int_or_none(row["source_chat_id"]) if "source_chat_id" in keys else None,
        "source_message_id": _int_or_none(row["source_message_id"])
        if "source_message_id" in keys
        else None,
    }


def link_status(link: dict[str, Any] | None, *, now: datetime | None = None) -> str:
    if not link:
        return "not_found"
    if link.get("revoked_at"):
        return "revoked"
    if str(link.get("link_mode") or "") == "one_shot" and link.get("used_at"):
        return "used"
    if str(link.get("link_mode") or "") == "expiring":
        exp = _parse_iso(link.get("expires_at"))
        ts = now or _now()
        if exp is not None and ts >= exp:
            return "expired"
    return "open"


def is_open(link: dict[str, Any] | None, *, now: datetime | None = None) -> bool:
    return link_status(link, now=now) == "open"


def create_link(
    owner_user_id: int | str,
    *,
    title: str | None = None,
    attendees: list[Any] | None = None,
    duration_min: int | None = None,
    link_mode: str | None = None,
    expire_days: int | None = None,
) -> dict[str, Any]:
    uid = _uid(owner_user_id)
    mode = normalize_link_mode(link_mode)
    dur = normalize_duration_min(duration_min)
    emails = normalize_emails(attendees)
    heading = str(title or "").strip()
    ts = _now()
    expires_at = None
    if mode == "expiring":
        days = normalize_expire_days(expire_days)
        expires_at = (ts + timedelta(days=days)).isoformat()
    token = _new_token()
    created = ts.isoformat()
    with _LOCK:
        _conn().execute(
            """
            INSERT INTO booking_links (
                token, owner_user_id, title, attendee_emails, duration_min,
                link_mode, expires_at, used_at, created_at, revoked_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL)
            """,
            (
                token,
                uid,
                heading,
                json.dumps(emails, ensure_ascii=False),
                dur,
                mode,
                expires_at,
                created,
            ),
        )
        _conn().commit()
    return {
        "token": token,
        "owner_user_id": uid,
        "title": heading,
        "attendee_emails": emails,
        "duration_min": dur,
        "link_mode": mode,
        "expires_at": expires_at,
        "used_at": None,
        "created_at": created,
        "revoked_at": None,
        "source_chat_id": None,
        "source_message_id": None,
    }


def attach_source_message(token: str, chat_id: int, message_id: int) -> bool:
    raw = (token or "").strip()
    if not _token_ok(raw):
        return False
    try:
        cid = int(chat_id)
        mid = int(message_id)
    except (TypeError, ValueError):
        return False
    if not cid or not mid:
        return False
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE booking_links
            SET source_chat_id = ?, source_message_id = ?
            WHERE token = ? AND revoked_at IS NULL
            """,
            (str(cid), str(mid), raw),
        )
        _conn().commit()
        return cur.rowcount > 0


def resolve_link(token: str) -> Optional[dict[str, Any]]:
    raw = (token or "").strip()
    if not _token_ok(raw):
        return None
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM booking_links WHERE token = ? AND revoked_at IS NULL",
            (raw,),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def try_mark_used(token: str) -> bool:
    raw = (token or "").strip()
    if not _token_ok(raw):
        return False
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE booking_links
            SET used_at = ?
            WHERE token = ? AND revoked_at IS NULL AND used_at IS NULL
            """,
            (ts, raw),
        )
        _conn().commit()
        return cur.rowcount > 0


def clear_used(token: str) -> None:
    raw = (token or "").strip()
    if not _token_ok(raw):
        return
    with _LOCK:
        _conn().execute(
            "UPDATE booking_links SET used_at = NULL WHERE token = ?",
            (raw,),
        )
        _conn().commit()


def revoke_link(owner_user_id: int | str, token: str) -> bool:
    uid = _uid(owner_user_id)
    raw = (token or "").strip()
    if not _token_ok(raw):
        return False
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            UPDATE booking_links
            SET revoked_at = ?
            WHERE token = ? AND owner_user_id = ? AND revoked_at IS NULL
            """,
            (ts, raw, uid),
        )
        _conn().commit()
        return cur.rowcount > 0
