"""Локальные заметки пользователя (SQLite). Опциональная связь с Todoist task id."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    raw = (os.getenv("NOTES_DB_PATH") or "").strip()
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            from assistant.config import ROOT

            p = ROOT / p
        return p
    from assistant.config import ROOT

    return ROOT / "notes.sqlite"


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is not None:
        return _CONN
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _CONN = sqlite3.connect(str(path), check_same_thread=False)
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        """
        CREATE TABLE IF NOT EXISTS local_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            todoist_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    _CONN.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_notes_user ON local_notes(user_id)"
    )
    _ensure_role_column(_CONN)
    _ensure_kb_enabled_column(_CONN)
    _ensure_revision_column(_CONN)
    _ensure_pins_table(_CONN)
    _CONN.commit()
    return _CONN


KNOWLEDGE_ROLE = "knowledge"
KNOWLEDGE_TITLE = "База знаний"


def _ensure_role_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(local_notes)")}
    if "role" not in cols:
        conn.execute(
            "ALTER TABLE local_notes ADD COLUMN role TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_local_notes_user_role "
        "ON local_notes(user_id, role)"
    )


def _ensure_kb_enabled_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(local_notes)")}
    if "kb_enabled" not in cols:
        conn.execute(
            "ALTER TABLE local_notes ADD COLUMN kb_enabled INTEGER NOT NULL DEFAULT 1"
        )


def _ensure_revision_column(conn: sqlite3.Connection) -> None:
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(local_notes)")}
    if "revision" not in cols:
        conn.execute(
            "ALTER TABLE local_notes ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
        )


def _ensure_pins_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_pins (
            user_id TEXT NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            pinned_at TEXT NOT NULL,
            PRIMARY KEY (user_id, item_kind, item_id)
        )
        """
    )


def note_kb_enabled(note: dict[str, Any] | None) -> bool:
    if not note:
        return True
    raw = note.get("kb_enabled")
    if raw in (False, 0, "0", "false", "False"):
        return False
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _plain_note_text(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").replace("\xa0", " ").strip()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    role = ""
    try:
        role = str(row["role"] or "")
    except (IndexError, KeyError):
        role = ""
    owner = ""
    try:
        owner = str(row["user_id"] or "")
    except (IndexError, KeyError):
        owner = ""
    revision = 1
    try:
        if "revision" in row.keys():
            revision = int(row["revision"] or 1)
    except (IndexError, KeyError, TypeError, ValueError):
        revision = 1
    return {
        "id": int(row["id"]),
        "title": str(row["title"] or ""),
        "body": str(row["body"] or ""),
        "description": str(row["body"] or ""),
        "content": str(row["title"] or ""),
        "todoist_id": row["todoist_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "source": "local",
        "role": role,
        "is_knowledge": role == KNOWLEDGE_ROLE,
        "owner_user_id": owner,
        "user_id": owner,
        "revision": max(1, revision),
        "kb_enabled": note_kb_enabled(
            {"kb_enabled": row["kb_enabled"] if "kb_enabled" in row.keys() else 1}
        ),
    }


def search_notes(
    user_id: int | str, query: str, *, limit: int = 5
) -> list[dict[str, Any]]:
    """Поиск по заголовку и тексту (все слова запроса должны встретиться)."""
    uid = str(int(user_id))
    q = (query or "").strip()
    if not q:
        return []
    words = [w for w in re.findall(r"\w+", q.lower(), flags=re.UNICODE) if len(w) >= 2]
    if not words:
        words = [q.lower()]
    lim = max(1, min(int(limit), 20))
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for note in list_notes(uid, limit=500):
        hay = f"{note.get('title') or ''} {note.get('body') or ''}".lower()
        score = sum(1 for w in words if w in hay)
        if score > 0:
            try:
                updated_key = int(
                    str(note.get("updated_at") or note.get("created_at") or "")
                    .replace("-", "")
                    .replace(":", "")
                    .replace("T", "")
                    .replace("+", "")[:14]
                    or "0"
                )
            except ValueError:
                updated_key = 0
            scored.append((score, updated_key, note))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [n for _, _, n in scored[:lim]]


def list_notes(
    user_id: int | str, *, limit: int = 200, include_knowledge: bool = False
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    lim = max(1, min(int(limit), 500))
    sql = """
            SELECT * FROM local_notes
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """
    params: tuple[Any, ...] = (uid, lim)
    if not include_knowledge:
        sql = """
            SELECT * FROM local_notes
            WHERE user_id = ? AND IFNULL(role, '') != ?
            ORDER BY updated_at DESC
            LIMIT ?
            """
        params = (uid, KNOWLEDGE_ROLE, lim)
    with _LOCK:
        cur = _conn().execute(sql, params)
        own = [_row_to_dict(r) for r in cur.fetchall()]
    notes = _merge_shared_notes(uid, own, include_knowledge=include_knowledge)
    notes.sort(key=lambda n: str(n.get("updated_at") or ""), reverse=True)
    notes = notes[:lim]
    out = [_with_sharing(n, uid) for n in notes]
    pins = pinned_id_set(uid, "local")
    for n in out:
        n["pinned"] = str(n.get("id") or "") in pins
    out.sort(key=lambda n: str(n.get("updated_at") or ""), reverse=True)
    out.sort(key=lambda n: 0 if n.get("pinned") else 1)
    return out


def get_note(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM local_notes WHERE user_id = ? AND id = ?",
            (uid, int(note_id)),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_note_by_id(note_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        cur = _conn().execute(
            "SELECT * FROM local_notes WHERE id = ?",
            (int(note_id),),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_accessible_note(
    user_id: int | str, note_id: int, *, with_sharing: bool = True
) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    own = get_note(uid, note_id)
    if own:
        return _with_sharing(own, uid) if with_sharing else own
    from assistant.stores import note_members

    shared = note_members.get_shared_note(uid, int(note_id))
    if not shared:
        return None
    return _with_sharing(shared, uid) if with_sharing else shared


def _merge_shared_notes(
    uid: str, own: list[dict[str, Any]], *, include_knowledge: bool
) -> list[dict[str, Any]]:
    seen = {int(n["id"]) for n in own}
    out = list(own)
    try:
        from assistant.stores import note_members
    except Exception:
        return out
    for owner, nid in note_members.list_note_ids_for_member(uid):
        if nid in seen:
            continue
        note = get_note(owner, nid)
        if not note:
            continue
        if not include_knowledge and note.get("is_knowledge"):
            continue
        seen.add(nid)
        out.append(note)
    return out


def _with_sharing(note: dict[str, Any], viewer_id: int | str) -> dict[str, Any]:
    uid = str(int(viewer_id))
    owner = str(note.get("owner_user_id") or note.get("user_id") or "")
    out = dict(note)
    out["owner_user_id"] = owner
    out["is_owner"] = owner == uid
    out["can_edit"] = True
    try:
        from assistant.stores import note_members

        out["members"] = note_members.list_members_public(owner, int(note["id"]))
    except Exception:
        out["members"] = []
    return out


def create_note(
    user_id: int | str,
    title: str,
    body: str = "",
    *,
    todoist_id: Optional[str] = None,
    role: str = "",
) -> dict[str, Any]:
    uid = str(int(user_id))
    title = (title or "").strip() or "(без названия)"
    body = (body or "").strip()
    note_role = (role or "").strip()
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            """
            INSERT INTO local_notes (user_id, title, body, todoist_id, created_at, updated_at, role)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (uid, title, body, todoist_id, ts, ts, note_role),
        )
        _conn().commit()
        nid = int(cur.lastrowid)
    out = get_note(uid, nid)
    assert out is not None
    return out


class NoteConflictError(Exception):
    def __init__(self, note: dict[str, Any]) -> None:
        super().__init__("Заметка изменена другим участником")
        self.note = note


def update_note(
    user_id: int | str,
    note_id: int,
    *,
    title: Optional[str] = None,
    body: Optional[str] = None,
    todoist_id: Optional[str] = None,
    kb_enabled: Optional[bool] = None,
    expected_updated_at: Optional[str] = None,
    expected_revision: Optional[int] = None,
    actor_user_id: Optional[int | str] = None,
) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    existing = get_note(uid, note_id)
    if not existing:
        return None
    if expected_revision is not None:
        try:
            if int(existing.get("revision") or 1) != int(expected_revision):
                raise NoteConflictError(existing)
        except (TypeError, ValueError) as e:
            raise NoteConflictError(existing) from e
    if expected_updated_at is not None:
        if str(existing.get("updated_at") or "") != str(expected_updated_at):
            raise NoteConflictError(existing)
    new_title = existing["title"] if title is None else (title or "").strip() or "(без названия)"
    new_body = existing["body"] if body is None else (body or "").strip()
    # Автосохранение пустого редактора не должно затирать текст заметки.
    if body is not None and not _plain_note_text(new_body) and _plain_note_text(existing["body"] or ""):
        new_body = existing["body"]
    tid = existing.get("todoist_id") if todoist_id is None else todoist_id
    enabled = (
        1
        if (existing.get("kb_enabled") if kb_enabled is None else bool(kb_enabled))
        else 0
    )
    ts = _now_iso()
    new_rev = int(existing.get("revision") or 1) + 1
    with _LOCK:
        _conn().execute(
            """
            UPDATE local_notes
            SET title = ?, body = ?, todoist_id = ?, kb_enabled = ?,
                updated_at = ?, revision = ?
            WHERE user_id = ? AND id = ?
            """,
            (new_title, new_body, tid, enabled, ts, new_rev, uid, int(note_id)),
        )
        _conn().commit()
    viewer = actor_user_id if actor_user_id is not None else uid
    out = get_note(uid, note_id)
    return _with_sharing(out, viewer) if out else None


def is_knowledge_note(user_id: int | str, note_id: int) -> bool:
    row = get_note(user_id, note_id)
    return bool(row and row.get("is_knowledge"))


def knowledge_version(user_id: int | str) -> str:
    """Слепок включённой БЗ: sha1(count|max(updated_at)|sum(len)|sum(revision))."""
    import hashlib

    uid = str(int(user_id))
    with _LOCK:
        row = _conn().execute(
            """
            SELECT
                COUNT(*) AS n,
                COALESCE(MAX(updated_at), '') AS mx,
                COALESCE(SUM(LENGTH(body)), 0) AS sz,
                COALESCE(SUM(revision), 0) AS rev
            FROM local_notes
            WHERE user_id = ?
              AND role = ?
              AND COALESCE(kb_enabled, 1) != 0
            """,
            (uid, KNOWLEDGE_ROLE),
        ).fetchone()
    n = int(row["n"] or 0) if row else 0
    mx = str(row["mx"] or "") if row else ""
    sz = int(row["sz"] or 0) if row else 0
    rev = int(row["rev"] or 0) if row else 0
    raw = f"{n}|{mx}|{sz}|{rev}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def list_knowledge_notes(
    user_id: int | str, *, limit: int = 200
) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    lim = max(1, min(int(limit), 500))
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM local_notes
            WHERE user_id = ? AND role = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (uid, KNOWLEDGE_ROLE, lim),
        )
        return [_row_to_dict(r) for r in cur.fetchall()]


def get_knowledge_note(user_id: int | str) -> Optional[dict[str, Any]]:
    notes = list_knowledge_notes(user_id, limit=1)
    return notes[0] if notes else None


def ensure_knowledge_note(user_id: int | str) -> dict[str, Any]:
    existing = get_knowledge_note(user_id)
    if existing:
        return existing
    return create_note(user_id, KNOWLEDGE_TITLE, "", role=KNOWLEDGE_ROLE)


def pinned_id_set(user_id: int | str, item_kind: str = "local") -> set[str]:
    uid = str(int(user_id))
    kind = (item_kind or "local").strip() or "local"
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT item_id FROM note_pins
            WHERE user_id = ? AND item_kind = ?
            """,
            (uid, kind),
        )
        return {str(r[0]) for r in cur.fetchall()}


def set_note_pinned(
    user_id: int | str, note_id: int, pinned: bool, *, item_kind: str = "local"
) -> bool:
    uid = str(int(user_id))
    kind = (item_kind or "local").strip() or "local"
    iid = str(int(note_id))
    with _LOCK:
        if pinned:
            _conn().execute(
                """
                INSERT OR REPLACE INTO note_pins (user_id, item_kind, item_id, pinned_at)
                VALUES (?, ?, ?, ?)
                """,
                (uid, kind, iid, _now_iso()),
            )
        else:
            _conn().execute(
                """
                DELETE FROM note_pins
                WHERE user_id = ? AND item_kind = ? AND item_id = ?
                """,
                (uid, kind, iid),
            )
        _conn().commit()
    return True


def duplicate_note(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    uid = str(int(user_id))
    src = get_accessible_note(uid, int(note_id), with_sharing=True)
    if not src:
        return None
    title = str(src.get("title") or "").strip() or "(без названия)"
    if not title.endswith(" (копия)"):
        title = title + " (копия)"
    body = str(src.get("body") or src.get("description") or "")
    dup = create_note(uid, title, body)
    if src.get("is_owner"):
        try:
            from assistant.stores import tags as tags_store

            mapping = tags_store.tags_by_items(uid, [("local", str(src["id"]))])
            tag_ids = [
                int(t["id"])
                for t in mapping.get(("local", str(src["id"])), [])
                if t.get("id") is not None
            ]
            if tag_ids:
                tags_store.set_item_tags(uid, "local", dup["id"], tag_ids)
                dup["tags"] = tags_store.tags_by_items(
                    uid, [("local", str(dup["id"]))]
                ).get(("local", str(dup["id"])), [])
        except Exception:
            pass
    dup["pinned"] = False
    return _with_sharing(dup, uid)


def delete_note(user_id: int | str, note_id: int) -> bool:
    uid = str(int(user_id))
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM local_notes WHERE user_id = ? AND id = ?",
            (uid, int(note_id)),
        )
        _conn().commit()
        ok = cur.rowcount > 0
    if ok:
        try:
            with _LOCK:
                _conn().execute(
                    "DELETE FROM note_pins WHERE item_kind = 'local' AND item_id = ?",
                    (str(int(note_id)),),
                )
                _conn().commit()
        except Exception:
            pass
        try:
            from assistant.stores import note_members

            note_members.delete_all_for_note(uid, int(note_id))
        except Exception:
            pass
    return ok


def upsert_by_todoist_id(
    user_id: int | str,
    todoist_id: str,
    title: str,
    body: str = "",
) -> dict[str, Any]:
    """Связать локальную заметку с задачей Todoist (создать или обновить)."""
    uid = str(int(user_id))
    tid = (todoist_id or "").strip()
    with _LOCK:
        cur = _conn().execute(
            "SELECT id FROM local_notes WHERE user_id = ? AND todoist_id = ?",
            (uid, tid),
        )
        row = cur.fetchone()
    if row:
        return update_note(uid, int(row["id"]), title=title, body=body, todoist_id=tid) or {}
    return create_note(uid, title, body, todoist_id=tid)
