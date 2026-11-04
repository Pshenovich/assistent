"""Ссылка разработчика на макет: токен только для чтения одного листа (MCP для агентов и HTTP API).

Токен выдаёт человек с доступом к заметке; доступ по токену действует, пока у выдавшего
есть доступ к заметке и токен не отозван. Тут же — PNG-превью фреймов, которые присылает
клиент (сервер сам макет в картинку не растрирует).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_TOKEN_BYTES = 24
MAX_PREVIEW_BYTES = 4 * 1024 * 1024


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS design_links (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            sheet_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT,
            last_used_at TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_design_links_sheet ON design_links(note_id, sheet_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS design_previews (
            note_id INTEGER NOT NULL,
            sheet_id TEXT NOT NULL,
            frame_id TEXT NOT NULL,
            sig TEXT NOT NULL DEFAULT '',
            png BLOB NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (note_id, sheet_id, frame_id)
        )
        """
    )
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "token": str(row["token"]),
        "user_id": str(row["user_id"]),
        "note_id": int(row["note_id"]),
        "sheet_id": str(row["sheet_id"]),
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
    }


def get_link(user_id: int | str, note_id: int, sheet_id: str | int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM design_links WHERE user_id = ? AND note_id = ? AND sheet_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (str(int(user_id)), int(note_id), str(sheet_id)),
        ).fetchone()
    return _row(row) if row else None


def create_or_get(user_id: int | str, note_id: int, sheet_id: str | int) -> dict[str, Any]:
    found = get_link(user_id, note_id, sheet_id)
    if found:
        return found
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO design_links(token, user_id, note_id, sheet_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, str(int(user_id)), int(note_id), str(sheet_id), _now()),
        )
        conn.commit()
    return get_link(user_id, note_id, sheet_id) or {}


def revoke(user_id: int | str, note_id: int, sheet_id: str | int) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "UPDATE design_links SET revoked_at = ? WHERE user_id = ? AND note_id = ? AND sheet_id = ? AND revoked_at IS NULL",
            (_now(), str(int(user_id)), int(note_id), str(sheet_id)),
        )
        conn.commit()
    return cur.rowcount > 0


def resolve(token: str) -> Optional[dict[str, Any]]:
    """Активный токен → {user_id, note_id, sheet_id}; отмечает последнее использование (не чаще раза в минуту)."""
    token = str(token or "").strip()
    if not token or len(token) > 80:
        return None
    with _LOCK:
        conn = _conn()
        row = conn.execute("SELECT * FROM design_links WHERE token = ? AND revoked_at IS NULL", (token,)).fetchone()
        if not row:
            return None
        now = _now()
        if str(row["last_used_at"] or "")[:16] != now[:16]:
            conn.execute("UPDATE design_links SET last_used_at = ? WHERE token = ?", (now, token))
            conn.commit()
    return _row(row)


def has_link(note_id: int, sheet_id: str | int) -> bool:
    """Есть ли у листа живая ссылка разработчика (от кого угодно) — тогда клиент присылает превью."""
    with _LOCK:
        row = _conn().execute(
            "SELECT 1 FROM design_links WHERE note_id = ? AND sheet_id = ? AND revoked_at IS NULL LIMIT 1",
            (int(note_id), str(sheet_id)),
        ).fetchone()
    return row is not None


def save_preview(note_id: int, sheet_id: str | int, frame_id: str, png: bytes, sig: str = "") -> None:
    if not png.startswith(b"\x89PNG") or len(png) > MAX_PREVIEW_BYTES:
        raise ValueError("Нужен PNG до 4 МБ")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO design_previews(note_id, sheet_id, frame_id, sig, png, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(note_id, sheet_id, frame_id) DO UPDATE SET sig = excluded.sig, png = excluded.png, "
            "updated_at = excluded.updated_at",
            (int(note_id), str(sheet_id), str(frame_id)[:64], str(sig)[:128], png, _now()),
        )
        conn.commit()


def preview_sigs(note_id: int, sheet_id: str | int) -> dict[str, str]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT frame_id, sig FROM design_previews WHERE note_id = ? AND sheet_id = ?", (int(note_id), str(sheet_id))
        ).fetchall()
    return {str(r["frame_id"]): str(r["sig"]) for r in rows}


def get_preview(note_id: int, sheet_id: str | int, frame_id: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute(
            "SELECT png, sig, updated_at FROM design_previews WHERE note_id = ? AND sheet_id = ? AND frame_id = ?",
            (int(note_id), str(sheet_id), str(frame_id)),
        ).fetchone()
    return {"png": bytes(row["png"]), "sig": row["sig"], "updated_at": row["updated_at"]} if row else None
