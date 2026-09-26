"""Дополнительные полотна локальной заметки (не Основная)."""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

PRIMARY_SHEET_ID = "main"
PRIMARY_SHEET_TITLE = "Основная"
MAX_EXTRA_SHEETS = 10


class SheetConflictError(Exception):
    def __init__(self, sheet: dict[str, Any]) -> None:
        super().__init__("Лист изменён другим участником")
        self.sheet = sheet


class SheetError(ValueError):
    pass


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_sheets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_sheets_note ON note_sheets(note_id, position)"
    )
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _plain_text(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html or "").replace("\xa0", " ").strip()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    body = str(row["body"] or "")
    return {
        "id": int(row["id"]),
        "note_id": int(row["note_id"]),
        "title": str(row["title"] or "").strip() or PRIMARY_SHEET_TITLE,
        "body": body,
        "description": body,
        "position": int(row["position"] or 0),
        "revision": max(1, int(row["revision"] or 1)),
        "is_primary": False,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def primary_sheet(note: dict[str, Any]) -> dict[str, Any]:
    body = str(note.get("body") or note.get("description") or "")
    return {
        "id": PRIMARY_SHEET_ID,
        "note_id": int(note["id"]),
        "title": PRIMARY_SHEET_TITLE,
        "body": body,
        "description": body,
        "position": 0,
        "revision": max(1, int(note.get("revision") or 1)),
        "is_primary": True,
        "created_at": str(note.get("created_at") or ""),
        "updated_at": str(note.get("updated_at") or ""),
    }


def note_allows_sheets(note: dict[str, Any] | None) -> bool:
    if not note:
        return False
    if note.get("is_knowledge") or str(note.get("role") or "") == notes_store.KNOWLEDGE_ROLE:
        return False
    return True


def is_primary_id(sheet_id: int | str) -> bool:
    raw = str(sheet_id or "").strip().lower()
    return raw in {PRIMARY_SHEET_ID, "0"}


def list_extra_sheets(note_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM note_sheets
            WHERE note_id = ?
            ORDER BY position ASC, id ASC
            """,
            (int(note_id),),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def list_sheets(note: dict[str, Any]) -> list[dict[str, Any]]:
    extras = list_extra_sheets(int(note["id"])) if note_allows_sheets(note) else []
    return [primary_sheet(note), *extras]


def compose_share_body(
    note: dict[str, Any], sheet_ids: list[str] | None
) -> tuple[str, list[dict[str, Any]]]:
    rows = list_sheets(note)
    wanted = [str(x).strip() for x in (sheet_ids or []) if str(x).strip()]
    if wanted:
        selected = [row for row in rows if str(row.get("id")) in set(wanted)]
    else:
        selected = [primary_sheet(note)]
    if not selected:
        selected = [primary_sheet(note)]
    parts: list[str] = []
    extras_only = len(selected) > 1 or not selected[0].get("is_primary")
    for row in selected:
        body = str(row.get("body") or "").strip()
        title = str(row.get("title") or "").strip() or PRIMARY_SHEET_TITLE
        if extras_only and not row.get("is_primary"):
            heading = f"<h2>{html.escape(title)}</h2>"
            parts.append(f"{heading}\n{body}" if body else heading)
        elif body:
            parts.append(body)
    return "\n".join(parts).strip(), selected


def get_extra_sheet(note_id: int, sheet_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM note_sheets WHERE note_id = ? AND id = ?",
            (int(note_id), int(sheet_id)),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def extra_count(note_id: int) -> int:
    with _LOCK:
        row = _conn().execute(
            "SELECT COUNT(*) AS n FROM note_sheets WHERE note_id = ?",
            (int(note_id),),
        ).fetchone()
    return int(row["n"] or 0) if row else 0


def _next_position(note_id: int) -> int:
    with _LOCK:
        row = _conn().execute(
            "SELECT COALESCE(MAX(position), 0) AS mx FROM note_sheets WHERE note_id = ?",
            (int(note_id),),
        ).fetchone()
    return int(row["mx"] or 0) + 1 if row else 1


def create_sheet(note: dict[str, Any], title: str = "") -> dict[str, Any]:
    if not note_allows_sheets(note):
        raise SheetError("Листы доступны только у обычных заметок")
    note_id = int(note["id"])
    if extra_count(note_id) >= MAX_EXTRA_SHEETS:
        raise SheetError(f"Не больше {MAX_EXTRA_SHEETS} дополнительных листов")
    name = (title or "").strip() or f"Лист {extra_count(note_id) + 2}"
    ts = _now_iso()
    pos = _next_position(note_id)
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO note_sheets (note_id, title, body, position, revision, created_at, updated_at)
            VALUES (?, ?, '', ?, 1, ?, ?)
            """,
            (note_id, name, pos, ts, ts),
        )
        _conn().commit()
        sid = int(cur.lastrowid)
    out = get_extra_sheet(note_id, sid)
    assert out is not None
    return out


def update_sheet(
    note_id: int,
    sheet_id: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> dict[str, Any]:
    existing = get_extra_sheet(note_id, sheet_id)
    if not existing:
        raise SheetError("Лист не найден")
    if expected_revision is not None:
        try:
            if int(existing.get("revision") or 1) != int(expected_revision):
                raise SheetConflictError(existing)
        except (TypeError, ValueError) as e:
            raise SheetConflictError(existing) from e
    new_title = existing["title"] if title is None else ((title or "").strip() or existing["title"])
    new_body = existing["body"] if body is None else (body or "").strip()
    if body is not None and not _plain_text(new_body) and _plain_text(existing["body"] or ""):
        new_body = existing["body"]
    ts = _now_iso()
    new_rev = int(existing.get("revision") or 1) + 1
    with _LOCK:
        _conn().execute(
            """
            UPDATE note_sheets
            SET title = ?, body = ?, updated_at = ?, revision = ?
            WHERE note_id = ? AND id = ?
            """,
            (new_title, new_body, ts, new_rev, int(note_id), int(sheet_id)),
        )
        _conn().commit()
    out = get_extra_sheet(note_id, sheet_id)
    assert out is not None
    return out


def delete_sheet(note_id: int, sheet_id: int) -> bool:
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM note_sheets WHERE note_id = ? AND id = ?",
            (int(note_id), int(sheet_id)),
        )
        _conn().commit()
        return cur.rowcount > 0


def delete_all_for_note(note_id: int) -> None:
    with _LOCK:
        _conn().execute("DELETE FROM note_sheets WHERE note_id = ?", (int(note_id),))
        _conn().commit()


def copy_sheets(src_note_id: int, dest_note_id: int) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    ts = _now_iso()
    for src in list_extra_sheets(src_note_id):
        with _LOCK:
            cur = _conn().execute(
                """
                INSERT INTO note_sheets
                    (note_id, title, body, position, revision, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    int(dest_note_id),
                    src["title"],
                    src["body"],
                    int(src.get("position") or 0),
                    ts,
                    ts,
                ),
            )
            _conn().commit()
            sid = int(cur.lastrowid)
        row = get_extra_sheet(dest_note_id, sid)
        if row:
            copied.append(row)
    return copied
