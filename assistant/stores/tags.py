"""Пользовательские теги для записей «Сохранённого» (local notes + journal)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_MIN_NAME_LEN = 1
_MAX_NAME_LEN = 40
_VALID_KINDS = frozenset({"local", "journal"})


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tag_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            tag_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, tag_id, item_kind, item_id),
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tag_links_item ON tag_links(user_id, item_kind, item_id)"
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_name(name: str) -> str:
    return (name or "").strip()


def _validate_name(name: str) -> str:
    n = _normalize_name(name)
    if len(n) < _MIN_NAME_LEN or len(n) > _MAX_NAME_LEN:
        raise ValueError(f"Имя тега должно быть от {_MIN_NAME_LEN} до {_MAX_NAME_LEN} символов")
    return n


def _tag_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _name_key(name: str) -> str:
    return _normalize_name(name).casefold()


def _find_tag_by_name_ci(cur: sqlite3.Cursor, uid: str, name: str) -> Optional[sqlite3.Row]:
    key = _name_key(name)
    cur.execute("SELECT * FROM tags WHERE user_id = ?", (uid,))
    for row in cur.fetchall():
        if _name_key(str(row["name"] or "")) == key:
            return row
    return None


def list_tags(user_id: int | str) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM tags WHERE user_id = ? ORDER BY LOWER(name) ASC",
            (uid,),
        )
        return [_tag_row_to_dict(r) for r in cur.fetchall()]


def create_tag(user_id: int | str, name: str) -> dict[str, Any]:
    uid = str(int(user_id))
    n = _validate_name(name)
    ts = _now_iso()
    with _LOCK:
        cur = _conn().cursor()
        existing = _find_tag_by_name_ci(cur, uid, n)
        if existing:
            raise ValueError("Тег с таким именем уже существует")
        cur.execute(
            """
            INSERT INTO tags (user_id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (uid, n, ts, ts),
        )
        _conn().commit()
        tag_id = int(cur.lastrowid)
        cur.execute("SELECT * FROM tags WHERE id = ? AND user_id = ?", (tag_id, uid))
        row = cur.fetchone()
    assert row is not None
    return _tag_row_to_dict(row)


def update_tag(user_id: int | str, tag_id: int, *, name: str) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    n = _validate_name(name)
    ts = _now_iso()
    with _LOCK:
        cur = _conn().cursor()
        cur.execute("SELECT id FROM tags WHERE user_id = ? AND id = ?", (uid, int(tag_id)))
        if not cur.fetchone():
            return None
        existing = _find_tag_by_name_ci(cur, uid, n)
        if existing and int(existing["id"]) != int(tag_id):
            raise ValueError("Тег с таким именем уже существует")
        cur.execute(
            "UPDATE tags SET name = ?, updated_at = ? WHERE user_id = ? AND id = ?",
            (n, ts, uid, int(tag_id)),
        )
        _conn().commit()
        cur.execute("SELECT * FROM tags WHERE user_id = ? AND id = ?", (uid, int(tag_id)))
        row = cur.fetchone()
    return _tag_row_to_dict(row) if row else None


def delete_tag(user_id: int | str, tag_id: int) -> bool:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM tags WHERE user_id = ? AND id = ?",
            (uid, int(tag_id)),
        )
        _conn().commit()
        return cur.rowcount > 0


def _validate_kind(item_kind: str) -> str:
    kind = (item_kind or "").strip().lower()
    if kind not in _VALID_KINDS:
        raise ValueError("Недопустимый тип записи")
    return kind


def set_item_tags(
    user_id: int | str,
    item_kind: str,
    item_id: int | str,
    tag_ids: list[int],
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Некорректный идентификатор записи")
    unique_ids: list[int] = []
    seen: set[int] = set()
    for raw in tag_ids:
        tid = int(raw)
        if tid in seen:
            continue
        seen.add(tid)
        unique_ids.append(tid)
    ts = _now_iso()
    with _LOCK:
        cur = _conn().cursor()
        if unique_ids:
            placeholders = ",".join("?" * len(unique_ids))
            cur.execute(
                f"""
                SELECT id FROM tags
                WHERE user_id = ? AND id IN ({placeholders})
                """,
                (uid, *unique_ids),
            )
            found = {int(r["id"]) for r in cur.fetchall()}
            missing = [tid for tid in unique_ids if tid not in found]
            if missing:
                raise ValueError("Один или несколько тегов не найдены")
        cur.execute(
            """
            DELETE FROM tag_links
            WHERE user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (uid, kind, iid),
        )
        for tid in unique_ids:
            cur.execute(
                """
                INSERT INTO tag_links (user_id, tag_id, item_kind, item_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uid, tid, kind, iid, ts),
            )
        _conn().commit()
    return get_item_tags(uid, kind, iid)


def get_item_tags(
    user_id: int | str, item_kind: str, item_id: int | str
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    iid = str(item_id).strip()
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT t.id, t.name
            FROM tag_links l
            JOIN tags t ON t.id = l.tag_id AND t.user_id = l.user_id
            WHERE l.user_id = ? AND l.item_kind = ? AND l.item_id = ?
            ORDER BY LOWER(t.name) ASC
            """,
            (uid, kind, iid),
        )
        return [{"id": int(r["id"]), "name": str(r["name"] or "")} for r in cur.fetchall()]


def tags_by_items(
    user_id: int | str,
    items: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    uid = str(int(user_id))
    normalized: list[tuple[str, str]] = []
    for kind, iid in items:
        try:
            normalized.append((_validate_kind(kind), str(iid).strip()))
        except ValueError:
            continue
    if not normalized:
        return {}
    out: dict[tuple[str, str], list[dict[str, Any]]] = {k: [] for k in normalized}
    with _LOCK:
        cur = _conn().cursor()
        for kind, iid in normalized:
            cur.execute(
                """
                SELECT t.id, t.name
                FROM tag_links l
                JOIN tags t ON t.id = l.tag_id AND t.user_id = l.user_id
                WHERE l.user_id = ? AND l.item_kind = ? AND l.item_id = ?
                ORDER BY LOWER(t.name) ASC
                """,
                (uid, kind, iid),
            )
            out[(kind, iid)] = [
                {"id": int(r["id"]), "name": str(r["name"] or "")} for r in cur.fetchall()
            ]
    return out


def filter_items_by_tags(
    user_id: int | str,
    item_kind: str,
    item_ids: list[str],
    tag_ids: list[int],
    *,
    mode: str = "and",
) -> list[str]:
    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    ids = [str(i).strip() for i in item_ids if str(i).strip()]
    tids = [int(t) for t in tag_ids if int(t) > 0]
    if not ids or not tids:
        return ids if not tids else []
    with _LOCK:
        cur = _conn().cursor()
        placeholders_ids = ",".join("?" * len(ids))
        placeholders_tags = ",".join("?" * len(tids))
        cur.execute(
            f"""
            SELECT l.item_id, COUNT(DISTINCT l.tag_id) AS cnt
            FROM tag_links l
            WHERE l.user_id = ?
              AND l.item_kind = ?
              AND l.item_id IN ({placeholders_ids})
              AND l.tag_id IN ({placeholders_tags})
            GROUP BY l.item_id
            """,
            (uid, kind, *ids, *tids),
        )
        rows = cur.fetchall()
    required = len(tids) if mode == "and" else 1
    matched = {str(r["item_id"]) for r in rows if int(r["cnt"]) >= required}
    return [iid for iid in ids if iid in matched]
