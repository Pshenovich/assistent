"""Хэштеги записей «Сохранённого»: извлекаются из текста (#тег)."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_MIN_NAME_LEN = 1
_MAX_NAME_LEN = 40
_VALID_KINDS = frozenset({"local", "journal"})
_HASHTAG_RE = re.compile(
    r"(?<![\w#])#([A-Za-zА-Яа-яЁё0-9_]{1,40})",
    flags=re.UNICODE,
)


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hashtags (
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
        CREATE TABLE IF NOT EXISTS hashtag_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            hashtag_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, hashtag_id, item_kind, item_id),
            FOREIGN KEY(hashtag_id) REFERENCES hashtags(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_hashtag_links_item "
        "ON hashtag_links(user_id, item_kind, item_id)"
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_name(name: str) -> str:
    n = (name or "").strip()
    if n.startswith("#"):
        n = n[1:].strip()
    return n


def _name_key(name: str) -> str:
    return _normalize_name(name).casefold()


def _validate_name(name: str) -> str:
    n = _normalize_name(name)
    if len(n) < _MIN_NAME_LEN or len(n) > _MAX_NAME_LEN:
        raise ValueError(
            f"Имя хэштега должно быть от {_MIN_NAME_LEN} до {_MAX_NAME_LEN} символов"
        )
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё0-9_]+", n, flags=re.UNICODE):
        raise ValueError("Хэштег может содержать буквы, цифры и подчёркивание")
    return n


def _validate_kind(item_kind: str) -> str:
    kind = (item_kind or "").strip().lower()
    if kind not in _VALID_KINDS:
        raise ValueError("Недопустимый тип записи")
    return kind


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _plain_text(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "").replace("\xa0", " ")


def extract_hashtag_names(*texts: str) -> list[str]:
    """Уникальные имена хэштегов в порядке появления, без #."""
    seen: set[str] = set()
    out: list[str] = []
    blob = " ".join(_plain_text(t) for t in texts)
    for match in _HASHTAG_RE.finditer(blob):
        raw = match.group(1)
        key = raw.casefold()
        if key in seen:
            continue
        try:
            name = _validate_name(raw)
        except ValueError:
            continue
        seen.add(key)
        out.append(name)
    return out


def _find_by_name_ci(cur: sqlite3.Cursor, uid: str, name: str) -> Optional[sqlite3.Row]:
    key = _name_key(name)
    cur.execute("SELECT * FROM hashtags WHERE user_id = ?", (uid,))
    for row in cur.fetchall():
        if _name_key(str(row["name"] or "")) == key:
            return row
    return None


def list_hashtags(user_id: int | str) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM hashtags WHERE user_id = ? ORDER BY LOWER(name) ASC",
            (uid,),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def ensure_hashtag(user_id: int | str, name: str) -> dict[str, Any]:
    uid = str(int(user_id))
    n = _validate_name(name)
    ts = _now_iso()
    with _LOCK:
        cur = _conn().cursor()
        existing = _find_by_name_ci(cur, uid, n)
        if existing:
            return _row_to_dict(existing)
        cur.execute(
            """
            INSERT INTO hashtags (user_id, name, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (uid, n, ts, ts),
        )
        _conn().commit()
        hid = int(cur.lastrowid)
        cur.execute("SELECT * FROM hashtags WHERE id = ? AND user_id = ?", (hid, uid))
        row = cur.fetchone()
    assert row is not None
    return _row_to_dict(row)


def get_item_hashtags(
    user_id: int | str, item_kind: str, item_id: int | str
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    iid = str(item_id).strip()
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT h.id, h.name, h.created_at, h.updated_at
            FROM hashtag_links l
            JOIN hashtags h ON h.id = l.hashtag_id AND h.user_id = l.user_id
            WHERE l.user_id = ? AND l.item_kind = ? AND l.item_id = ?
            ORDER BY LOWER(h.name) ASC
            """,
            (uid, kind, iid),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def hashtags_by_items(
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
                SELECT h.id, h.name, h.created_at, h.updated_at
                FROM hashtag_links l
                JOIN hashtags h ON h.id = l.hashtag_id AND h.user_id = l.user_id
                WHERE l.user_id = ? AND l.item_kind = ? AND l.item_id = ?
                ORDER BY LOWER(h.name) ASC
                """,
                (uid, kind, iid),
            )
            out[(kind, iid)] = [_row_to_dict(r) for r in cur.fetchall()]
    return out


def set_item_hashtags(
    user_id: int | str,
    item_kind: str,
    item_id: int | str,
    names: list[str],
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Некорректный идентификатор записи")
    unique: list[str] = []
    seen: set[str] = set()
    for raw in names:
        try:
            n = _validate_name(raw)
        except ValueError:
            continue
        key = n.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(n)
    ts = _now_iso()
    with _LOCK:
        cur = _conn().cursor()
        ids: list[int] = []
        for n in unique:
            existing = _find_by_name_ci(cur, uid, n)
            if existing:
                ids.append(int(existing["id"]))
                continue
            cur.execute(
                """
                INSERT INTO hashtags (user_id, name, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (uid, n, ts, ts),
            )
            ids.append(int(cur.lastrowid))
        cur.execute(
            """
            DELETE FROM hashtag_links
            WHERE user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (uid, kind, iid),
        )
        for hid in ids:
            cur.execute(
                """
                INSERT INTO hashtag_links
                    (user_id, hashtag_id, item_kind, item_id, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (uid, hid, kind, iid, ts),
            )
        _conn().commit()
    return get_item_hashtags(uid, kind, iid)


def sync_item_hashtags(
    user_id: int | str,
    item_kind: str,
    item_id: int | str,
    *texts: str,
) -> list[dict[str, Any]]:
    names = extract_hashtag_names(*texts)
    return set_item_hashtags(user_id, item_kind, item_id, names)


def delete_item_links(user_id: int | str, item_kind: str, item_id: int | str) -> None:
    uid = str(int(user_id))
    try:
        kind = _validate_kind(item_kind)
    except ValueError:
        return
    iid = str(item_id).strip()
    if not iid:
        return
    with _LOCK:
        _conn().execute(
            """
            DELETE FROM hashtag_links
            WHERE user_id = ? AND item_kind = ? AND item_id = ?
            """,
            (uid, kind, iid),
        )
        _conn().commit()


def _append_token_to_body(body: str, name: str) -> str:
    token = "#" + name
    plain = _plain_text(body)
    if re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", plain, flags=re.IGNORECASE):
        return body
    if re.search(r"<[a-z][\s\S]*>", body or "", flags=re.IGNORECASE):
        return (body or "").rstrip() + f"<p>{token}</p>"
    return ((body or "").rstrip() + " " + token).strip()


def migrate_extra_tags_to_hashtags(user_id: int | str) -> None:
    """Лишние теги (после перехода на 1 проект) становятся хэштегами."""
    from assistant.stores import tags as tags_store

    extras = tags_store.take_extra_tag_links(user_id)
    if not extras:
        return
    uid = str(int(user_id))
    for extra in extras:
        kind = extra["item_kind"]
        iid = extra["item_id"]
        name = extra["name"]
        current = [h["name"] for h in get_item_hashtags(uid, kind, iid)]
        if not any(_name_key(x) == _name_key(name) for x in current):
            current.append(name)
        try:
            set_item_hashtags(uid, kind, iid, current)
        except ValueError:
            continue
        if kind != "local":
            continue
        try:
            note = notes_store.get_note(uid, int(iid))
        except (TypeError, ValueError):
            note = None
        if not note:
            continue
        old_body = str(note.get("body") or "")
        new_body = _append_token_to_body(old_body, name)
        if new_body == old_body:
            continue
        with _LOCK:
            _conn().execute(
                "UPDATE local_notes SET body = ? WHERE user_id = ? AND id = ?",
                (new_body, uid, int(iid)),
            )
            _conn().commit()
