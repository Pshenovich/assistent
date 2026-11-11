"""Библиотеки компонентов организации: макет (лист «Макет» документа), опубликованный для организации.

Публикует тот, кто может править документ и состоит в организации. Участники организации видят
компоненты библиотеки (только сами компоненты, не весь документ) и вставляют их в свои макеты.
Снять с публикации может опубликовавший, тот, кто может править документ, и админ организации.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.lib import sqlite_conn
from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]


@sqlite_conn.schema_once(lambda: notes_store._conn())  # type: ignore[attr-defined]
def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS design_libraries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id INTEGER NOT NULL,
            note_id INTEGER NOT NULL,
            sheet_id TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            published_by TEXT NOT NULL,
            published_at TEXT NOT NULL,
            UNIQUE(org_id, note_id, sheet_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_design_libraries_org ON design_libraries(org_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_design_libraries_sheet ON design_libraries(note_id, sheet_id)")
    conn.commit()
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "org_id": int(row["org_id"]),
        "note_id": int(row["note_id"]),
        "sheet_id": str(row["sheet_id"]),
        "name": str(row["name"] or ""),
        "published_by": str(row["published_by"]),
        "published_at": row["published_at"],
    }


def publish(user_id: int | str, org_id: int, note_id: int, sheet_id: str | int, name: str) -> dict[str, Any]:
    """Опубликовать (или переименовать уже опубликованную) библиотеку. Права проверяет вызывающий."""
    name = " ".join(str(name or "").split())[:120]
    with _LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO design_libraries(org_id, note_id, sheet_id, name, published_by, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_id, note_id, sheet_id) DO UPDATE SET name = excluded.name
            """,
            (int(org_id), int(note_id), str(sheet_id), name, str(int(user_id)), _now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM design_libraries WHERE org_id = ? AND note_id = ? AND sheet_id = ?",
            (int(org_id), int(note_id), str(sheet_id)),
        ).fetchone()
    return _row(row)


def get(lib_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT * FROM design_libraries WHERE id = ?", (int(lib_id),)).fetchone()
    return _row(row) if row else None


def remove(lib_id: int) -> bool:
    with _LOCK:
        conn = _conn()
        cur = conn.execute("DELETE FROM design_libraries WHERE id = ?", (int(lib_id),))
        conn.commit()
    return cur.rowcount > 0


def for_sheet(note_id: int, sheet_id: str | int) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM design_libraries WHERE note_id = ? AND sheet_id = ? ORDER BY id",
            (int(note_id), str(sheet_id)),
        ).fetchall()
    return [_row(r) for r in rows]


def for_orgs(org_ids: list[int]) -> list[dict[str, Any]]:
    ids = [int(x) for x in org_ids][:50]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    with _LOCK:
        rows = _conn().execute(
            f"SELECT * FROM design_libraries WHERE org_id IN ({marks}) ORDER BY name COLLATE NOCASE, id",  # noqa: S608
            ids,
        ).fetchall()
    return [_row(r) for r in rows]
