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
SHEET_KIND_DOC = "doc"
SHEET_KIND_WEB = "web"


class SheetConflictError(Exception):
    def __init__(self, sheet: dict[str, Any]) -> None:
        super().__init__("Лист изменён другим участником")
        self.sheet = sheet


class SheetError(ValueError):
    pass


def _normalize_kind(raw: Any) -> str:
    k = str(raw or "").strip().lower()
    if k in (SHEET_KIND_WEB, "canvas", "presentation", "html"):
        return SHEET_KIND_WEB
    return SHEET_KIND_DOC


def _ensure_kind_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(note_sheets)").fetchall()}
    if "kind" not in cols:
        conn.execute(
            "ALTER TABLE note_sheets ADD COLUMN kind TEXT NOT NULL DEFAULT 'doc'"
        )


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
            updated_at TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'doc'
        )
        """
    )
    _ensure_kind_column(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_sheets_note ON note_sheets(note_id, position)"
    )
    # Место «Основной» среди вкладок (доп. листы упорядочены своим position).
    conn.execute(
        "CREATE TABLE IF NOT EXISTS note_sheet_prefs (note_id INTEGER PRIMARY KEY, primary_pos INTEGER NOT NULL DEFAULT 0)"
    )
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _plain_text(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html or "").replace("\xa0", " ").strip()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    body = str(row["body"] or "")
    keys = set(row.keys())
    kind = _normalize_kind(row["kind"] if "kind" in keys else SHEET_KIND_DOC)
    return {
        "id": int(row["id"]),
        "note_id": int(row["note_id"]),
        "title": str(row["title"] or "").strip() or PRIMARY_SHEET_TITLE,
        "body": body,
        "description": body,
        "kind": kind,
        "position": int(row["position"] or 0),
        "revision": max(1, int(row["revision"] or 1)),
        "is_primary": False,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def primary_sheet(note: dict[str, Any]) -> dict[str, Any]:
    body = str(note.get("body") or note.get("description") or "")
    custom = str(note.get("primary_sheet_title") or "").strip()
    title = custom or PRIMARY_SHEET_TITLE
    return {
        "id": PRIMARY_SHEET_ID,
        "note_id": int(note["id"]),
        "title": title,
        "body": body,
        "description": body,
        "kind": SHEET_KIND_DOC,
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


def list_sheet_versions(note_id: int) -> list[dict[str, Any]]:
    """Лёгкий список листов без содержимого — для опроса «что изменилось» (collab)."""
    with _LOCK:
        cur = _conn().execute(
            "SELECT id, title, kind, position, revision FROM note_sheets WHERE note_id = ? ORDER BY position ASC, id ASC",
            (int(note_id),),
        )
        return [
            {
                "id": int(r["id"]),
                "title": str(r["title"] or "").strip() or PRIMARY_SHEET_TITLE,
                "kind": _normalize_kind(r["kind"]),
                "revision": max(1, int(r["revision"] or 1)),
            }
            for r in cur.fetchall()
        ]


def primary_position(note_id: int) -> int:
    with _LOCK:
        row = _conn().execute("SELECT primary_pos FROM note_sheet_prefs WHERE note_id = ?", (int(note_id),)).fetchone()
    return max(0, int(row["primary_pos"])) if row else 0


def list_sheets(note: dict[str, Any]) -> list[dict[str, Any]]:
    if not note_allows_sheets(note):
        return [primary_sheet(note)]
    extras = list_extra_sheets(int(note["id"]))
    pos = min(primary_position(int(note["id"])), len(extras))
    return [*extras[:pos], primary_sheet(note), *extras[pos:]]


def sheet_order_ids(note: dict[str, Any]) -> list[str]:
    """Порядок вкладок (id листов, «Основная» = PRIMARY_SHEET_ID)."""
    return [str(s["id"]) for s in list_sheets(note)]


def set_sheet_order(note: dict[str, Any], order: list[Any]) -> list[dict[str, Any]]:
    """Новый порядок вкладок. order — все id листов заметки (включая «Основную»)."""
    note_id = int(note["id"])
    current = sheet_order_ids(note)
    want = [str(x).strip() for x in order or []]
    if sorted(want) != sorted(current) or len(set(want)) != len(want):
        raise SheetError("Порядок листов устарел — обновите заметку")
    extras = [x for x in want if x != str(PRIMARY_SHEET_ID)]
    with _LOCK:
        conn = _conn()
        for i, sid in enumerate(extras):
            conn.execute("UPDATE note_sheets SET position = ? WHERE note_id = ? AND id = ?", (i, note_id, int(sid)))
        conn.execute(
            "INSERT INTO note_sheet_prefs (note_id, primary_pos) VALUES (?, ?) "
            "ON CONFLICT(note_id) DO UPDATE SET primary_pos = excluded.primary_pos",
            (note_id, want.index(str(PRIMARY_SHEET_ID))),
        )
        conn.commit()
    return list_sheets(note)


def _selected_share_rows(
    note: dict[str, Any], sheet_ids: list[str] | None
) -> list[dict[str, Any]]:
    rows = list_sheets(note)
    wanted = [str(x).strip() for x in (sheet_ids or []) if str(x).strip()]
    if wanted:
        # Удалённые листы не подменяем основным: по такой ссылке ничего не показываем.
        return [row for row in rows if str(row.get("id")) in set(wanted)]
    return [primary_sheet(note)]


def compose_share_body(
    note: dict[str, Any], sheet_ids: list[str] | None
) -> tuple[str, list[dict[str, Any]]]:
    """Rich-text body for share page. Web-canvas sheets are omitted here —
    they are delivered separately via list_share_canvases (iframe srcdoc)."""
    selected = _selected_share_rows(note, sheet_ids)
    parts: list[str] = []
    if not selected:
        return "", []
    extras_only = len(selected) > 1 or not selected[0].get("is_primary")
    for row in selected:
        if _normalize_kind(row.get("kind")) == SHEET_KIND_WEB:
            continue
        body = str(row.get("body") or "").strip()
        title = str(row.get("title") or "").strip() or PRIMARY_SHEET_TITLE
        if extras_only and not row.get("is_primary"):
            heading = f"<h2>{html.escape(title)}</h2>"
            parts.append(f"{heading}\n{body}" if body else heading)
        elif body:
            parts.append(body)
    return "\n".join(parts).strip(), selected


def list_share_canvases(
    note: dict[str, Any], sheet_ids: list[str] | None
) -> list[dict[str, Any]]:
    """Web-canvas sheets selected for a share link (full HTML documents)."""
    out: list[dict[str, Any]] = []
    for row in _selected_share_rows(note, sheet_ids):
        if _normalize_kind(row.get("kind")) != SHEET_KIND_WEB:
            continue
        html_doc = str(row.get("body") or "").strip()
        if not html_doc:
            continue
        out.append(
            {
                "id": str(row.get("id")),
                "title": str(row.get("title") or "").strip() or "Презентация",
                "html": html_doc[:200000],
                "kind": SHEET_KIND_WEB,
            }
        )
    return out


def copy_shared_selection(
    src_note: dict[str, Any],
    sheet_ids: list[str] | None,
    dest_user_id: int | str,
) -> dict[str, Any]:
    """Копия заметки по публичной ссылке: только листы, открытые этой ссылкой.

    Набор листов совпадает с тем, что видит гость (`_selected_share_rows`):
    первый выбранный doc-лист становится «Основной» копии, остальные — доп. листами
    в исходном порядке и с исходным kind. Невыбранные листы не попадают в копию.
    """
    selected = _selected_share_rows(src_note, sheet_ids)
    primary_row = next(
        (r for r in selected if _normalize_kind(r.get("kind")) == SHEET_KIND_DOC), None
    )
    primary_title = ""
    if primary_row is not None:
        raw_title = str(primary_row.get("title") or "").strip()
        if raw_title and raw_title != PRIMARY_SHEET_TITLE:
            primary_title = raw_title
    dest = notes_store.create_note(
        dest_user_id,
        str(src_note.get("title") or "Без названия"),
        str((primary_row or {}).get("body") or ""),
        primary_sheet_title=primary_title,
    )
    if not note_allows_sheets(dest):
        return dest
    ts = _now_iso()
    position = 0
    for row in selected:
        if row is primary_row:
            continue
        position += 1
        with _LOCK:
            _conn().execute(
                """
                INSERT INTO note_sheets
                    (note_id, title, body, position, revision, created_at, updated_at, kind)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    int(dest["id"]),
                    str(row.get("title") or "").strip() or f"Лист {position + 1}",
                    str(row.get("body") or ""),
                    position,
                    ts,
                    ts,
                    _normalize_kind(row.get("kind")),
                ),
            )
            _conn().commit()
    return dest


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


def create_sheet(
    note: dict[str, Any],
    title: str = "",
    body: str = "",
    *,
    kind: str = SHEET_KIND_DOC,
) -> dict[str, Any]:
    if not note_allows_sheets(note):
        raise SheetError("Листы доступны только у обычных заметок")
    note_id = int(note["id"])
    if extra_count(note_id) >= MAX_EXTRA_SHEETS:
        raise SheetError(f"Не больше {MAX_EXTRA_SHEETS} дополнительных листов")
    name = (title or "").strip() or f"Лист {extra_count(note_id) + 2}"
    ts = _now_iso()
    pos = _next_position(note_id)
    sheet_body = body if body is not None else ""
    sheet_kind = _normalize_kind(kind)
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO note_sheets
                (note_id, title, body, position, revision, created_at, updated_at, kind)
            VALUES (?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (note_id, name, sheet_body, pos, ts, ts, sheet_kind),
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
    kind: Optional[str] = None,
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
    # Web canvases may be CSS-heavy with little plain text — allow empty-ish overwrite.
    is_web = _normalize_kind(kind if kind is not None else existing.get("kind")) == SHEET_KIND_WEB
    if (
        body is not None
        and not is_web
        and not _plain_text(new_body)
        and _plain_text(existing["body"] or "")
    ):
        new_body = existing["body"]
    new_kind = existing.get("kind") or SHEET_KIND_DOC
    if kind is not None:
        new_kind = _normalize_kind(kind)
    ts = _now_iso()
    new_rev = int(existing.get("revision") or 1) + 1
    with _LOCK:
        _conn().execute(
            """
            UPDATE note_sheets
            SET title = ?, body = ?, kind = ?, updated_at = ?, revision = ?
            WHERE note_id = ? AND id = ?
            """,
            (new_title, new_body, new_kind, ts, new_rev, int(note_id), int(sheet_id)),
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
                    (note_id, title, body, position, revision, created_at, updated_at, kind)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    int(dest_note_id),
                    src["title"],
                    src["body"],
                    int(src.get("position") or 0),
                    ts,
                    ts,
                    _normalize_kind(src.get("kind")),
                ),
            )
            _conn().commit()
            sid = int(cur.lastrowid)
        row = get_extra_sheet(dest_note_id, sid)
        if row:
            copied.append(row)
    return copied


def extra_sheet_counts(note_ids: list[Any]) -> dict[int, int]:
    """Число доп. листов по заметкам (для бейджа на карточке списка)."""
    ids = []
    for x in note_ids or []:
        try:
            ids.append(int(x))
        except (TypeError, ValueError):
            continue
    if not ids:
        return {}
    out: dict[int, int] = {}
    with _LOCK:
        conn = _conn()
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            q = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"SELECT note_id, COUNT(*) AS c FROM note_sheets WHERE note_id IN ({q}) GROUP BY note_id",
                chunk,
            ):
                out[int(row["note_id"])] = int(row["c"])
    return out
