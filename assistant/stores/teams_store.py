"""Персональные команды владельца и шаринг документов базы знаний."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_teams_owner ON teams(owner_user_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_members (
            team_id INTEGER NOT NULL,
            contact_email TEXT NOT NULL,
            member_user_id TEXT,
            added_at TEXT NOT NULL,
            PRIMARY KEY (team_id, contact_email)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_members_user "
        "ON team_members(member_user_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS team_note_shares (
            team_id INTEGER NOT NULL,
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            shared_at TEXT NOT NULL,
            PRIMARY KEY (team_id, note_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_note_shares_note "
        "ON team_note_shares(owner_user_id, note_id)"
    )
    conn.commit()
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def _norm_email(email: str | None) -> str:
    return str(email or "").strip().lower()


def _team_row(row: sqlite3.Row, *, member_count: int = 0) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "owner_user_id": str(row["owner_user_id"]),
        "name": str(row["name"] or ""),
        "created_at": row["created_at"],
        "member_count": int(member_count),
    }


def _get_team_row(owner_user_id: int | str, team_id: int) -> Optional[sqlite3.Row]:
    owner = _uid(owner_user_id)
    cur = _conn().execute(
        "SELECT * FROM teams WHERE id = ? AND owner_user_id = ?",
        (int(team_id), owner),
    )
    return cur.fetchone()


def get_team(owner_user_id: int | str, team_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _get_team_row(owner_user_id, team_id)
        if not row:
            return None
        count = _member_count(int(row["id"]))
    return _team_row(row, member_count=count)


def list_teams(owner_user_id: int | str) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT t.*,
                   (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id)
                     AS member_count
            FROM teams t
            WHERE t.owner_user_id = ?
            ORDER BY t.created_at ASC, t.id ASC
            """,
            (owner,),
        )
        rows = cur.fetchall()
    return [_team_row(r, member_count=int(r["member_count"] or 0)) for r in rows]


def create_team(owner_user_id: int | str, name: str) -> dict[str, Any]:
    owner = _uid(owner_user_id)
    title = (name or "").strip()
    if not title:
        raise ValueError("Укажите название команды")
    if len(title) > 80:
        title = title[:80].strip()
    ts = _now_iso()
    with _LOCK:
        cur = _conn().execute(
            "INSERT INTO teams (owner_user_id, name, created_at) VALUES (?, ?, ?)",
            (owner, title, ts),
        )
        _conn().commit()
        tid = int(cur.lastrowid)
        row = _conn().execute("SELECT * FROM teams WHERE id = ?", (tid,)).fetchone()
    assert row is not None
    return _team_row(row, member_count=0)


def rename_team(owner_user_id: int | str, team_id: int, name: str) -> Optional[dict[str, Any]]:
    title = (name or "").strip()
    if not title:
        raise ValueError("Укажите название команды")
    if len(title) > 80:
        title = title[:80].strip()
    with _LOCK:
        row = _get_team_row(owner_user_id, team_id)
        if not row:
            return None
        _conn().execute("UPDATE teams SET name = ? WHERE id = ?", (title, int(team_id)))
        _conn().commit()
        fresh = _get_team_row(owner_user_id, team_id)
        count = _member_count(int(team_id))
    assert fresh is not None
    return _team_row(fresh, member_count=count)


def delete_team(owner_user_id: int | str, team_id: int) -> bool:
    owner = _uid(owner_user_id)
    with _LOCK:
        row = _get_team_row(owner, team_id)
        if not row:
            return False
        note_ids = _note_ids_for_team_locked(int(team_id))
        _conn().execute("DELETE FROM team_members WHERE team_id = ?", (int(team_id),))
        _conn().execute("DELETE FROM team_note_shares WHERE team_id = ?", (int(team_id),))
        _conn().execute(
            "DELETE FROM teams WHERE id = ? AND owner_user_id = ?",
            (int(team_id), owner),
        )
        _conn().commit()
    for nid in note_ids:
        sync_note_team_members(owner, nid)
    return True


def _member_count(team_id: int) -> int:
    cur = _conn().execute(
        "SELECT COUNT(*) AS n FROM team_members WHERE team_id = ?",
        (int(team_id),),
    )
    row = cur.fetchone()
    return int(row["n"] or 0) if row else 0


def _note_ids_for_team_locked(team_id: int) -> list[int]:
    cur = _conn().execute(
        "SELECT note_id FROM team_note_shares WHERE team_id = ?",
        (int(team_id),),
    )
    return [int(r["note_id"]) for r in cur.fetchall()]


def list_team_members(owner_user_id: int | str, team_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        if not _get_team_row(owner_user_id, team_id):
            return []
        cur = _conn().execute(
            """
            SELECT contact_email, member_user_id, added_at
            FROM team_members
            WHERE team_id = ?
            ORDER BY added_at ASC
            """,
            (int(team_id),),
        )
        rows = cur.fetchall()
    return [
        {
            "email": str(r["contact_email"]),
            "member_user_id": str(r["member_user_id"]) if r["member_user_id"] else None,
            "added_at": r["added_at"],
        }
        for r in rows
    ]


def _contact_team_ids_locked(owner: str, email: str) -> list[int]:
    cur = _conn().execute(
        """
        SELECT m.team_id
        FROM team_members m
        JOIN teams t ON t.id = m.team_id
        WHERE t.owner_user_id = ? AND m.contact_email = ?
        ORDER BY m.team_id ASC
        """,
        (owner, email),
    )
    return [int(r["team_id"]) for r in cur.fetchall()]


def list_contact_team_ids(owner_user_id: int | str, email: str) -> list[int]:
    owner = _uid(owner_user_id)
    key = _norm_email(email)
    if not key:
        return []
    with _LOCK:
        return _contact_team_ids_locked(owner, key)


def _try_resolve_member_id(owner_user_id: int | str, email: str) -> Optional[str]:
    from assistant.stores import note_members

    try:
        uid, _ = note_members.resolve_contact_telegram_id(
            owner_user_id=owner_user_id, email=email
        )
        return _uid(uid)
    except ValueError:
        return None


def _upsert_team_member_locked(
    team_id: int, email: str, member_user_id: str | None
) -> None:
    ts = _now_iso()
    _conn().execute(
        """
        INSERT INTO team_members (team_id, contact_email, member_user_id, added_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(team_id, contact_email) DO UPDATE SET
            member_user_id = COALESCE(excluded.member_user_id, team_members.member_user_id)
        """,
        (int(team_id), email, member_user_id, ts),
    )


def set_contact_teams(
    owner_user_id: int | str, email: str, team_ids: list[int]
) -> dict[str, Any]:
    owner = _uid(owner_user_id)
    key = _norm_email(email)
    if not key:
        raise ValueError("Некорректный email")
    wanted = []
    seen: set[int] = set()
    for raw in team_ids or []:
        try:
            tid = int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError("Некорректная команда") from e
        if tid in seen:
            continue
        seen.add(tid)
        wanted.append(tid)

    member_uid = _try_resolve_member_id(owner, key)
    with _LOCK:
        current = set(_contact_team_ids_locked(owner, key))
        owned = {
            int(r["id"])
            for r in _conn()
            .execute("SELECT id FROM teams WHERE owner_user_id = ?", (owner,))
            .fetchall()
        }
        for tid in wanted:
            if tid not in owned:
                raise ValueError("Команда не найдена")
        to_add = [tid for tid in wanted if tid not in current]
        to_remove = [tid for tid in current if tid not in seen]
        for tid in to_add:
            _upsert_team_member_locked(tid, key, member_uid)
        for tid in to_remove:
            _conn().execute(
                "DELETE FROM team_members WHERE team_id = ? AND contact_email = ?",
                (int(tid), key),
            )
        if member_uid and wanted:
            _conn().execute(
                """
                UPDATE team_members SET member_user_id = ?
                WHERE contact_email = ? AND team_id IN ({})
                """.format(",".join("?" * len(wanted))),
                (member_uid, key, *wanted),
            )
        _conn().commit()
        affected = list(dict.fromkeys(to_add + to_remove + wanted))
        note_ids: list[int] = []
        if affected:
            placeholders = ",".join("?" * len(affected))
            cur = _conn().execute(
                f"SELECT DISTINCT note_id FROM team_note_shares WHERE team_id IN ({placeholders})",
                tuple(affected),
            )
            note_ids = [int(r["note_id"]) for r in cur.fetchall()]

    added: list[dict[str, Any]] = []
    for nid in note_ids:
        added.extend(sync_note_team_members(owner, nid))
    return {
        "team_ids": list_contact_team_ids(owner, key),
        "added_members": added,
    }


def remove_contact_from_all_teams(owner_user_id: int | str, email: str) -> None:
    set_contact_teams(owner_user_id, email, [])


def rename_contact_email(
    owner_user_id: int | str, old_email: str, new_email: str
) -> None:
    owner = _uid(owner_user_id)
    old_key = _norm_email(old_email)
    new_key = _norm_email(new_email)
    if not old_key or not new_key or old_key == new_key:
        return
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT m.team_id
            FROM team_members m
            JOIN teams t ON t.id = m.team_id
            WHERE t.owner_user_id = ? AND m.contact_email = ?
            """,
            (owner, old_key),
        )
        team_ids = [int(r["team_id"]) for r in cur.fetchall()]
        for tid in team_ids:
            _conn().execute(
                """
                UPDATE OR IGNORE team_members
                SET contact_email = ?
                WHERE team_id = ? AND contact_email = ?
                """,
                (new_key, tid, old_key),
            )
            _conn().execute(
                "DELETE FROM team_members WHERE team_id = ? AND contact_email = ?",
                (tid, old_key),
            )
        _conn().commit()


def refresh_contact_telegram(owner_user_id: int | str, email: str) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    key = _norm_email(email)
    if not key:
        return []
    member_uid = _try_resolve_member_id(owner, key)
    with _LOCK:
        team_ids = _contact_team_ids_locked(owner, key)
        if member_uid and team_ids:
            placeholders = ",".join("?" * len(team_ids))
            _conn().execute(
                f"""
                UPDATE team_members SET member_user_id = ?
                WHERE contact_email = ? AND team_id IN ({placeholders})
                """,
                (member_uid, key, *team_ids),
            )
            _conn().commit()
        note_ids: list[int] = []
        if team_ids:
            placeholders = ",".join("?" * len(team_ids))
            cur = _conn().execute(
                f"SELECT DISTINCT note_id FROM team_note_shares WHERE team_id IN ({placeholders})",
                tuple(team_ids),
            )
            note_ids = [int(r["note_id"]) for r in cur.fetchall()]
    added: list[dict[str, Any]] = []
    for nid in note_ids:
        added.extend(sync_note_team_members(owner, nid))
    return added


def list_note_teams(owner_user_id: int | str, note_id: int) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT t.*,
                   (SELECT COUNT(*) FROM team_members m WHERE m.team_id = t.id)
                     AS member_count
            FROM team_note_shares s
            JOIN teams t ON t.id = s.team_id
            WHERE s.owner_user_id = ? AND s.note_id = ?
            ORDER BY t.created_at ASC, t.id ASC
            """,
            (owner, int(note_id)),
        )
        rows = cur.fetchall()
    return [_team_row(r, member_count=int(r["member_count"] or 0)) for r in rows]


def set_note_teams(
    owner_user_id: int | str, note_id: int, team_ids: list[int]
) -> dict[str, Any]:
    owner = _uid(owner_user_id)
    note = notes_store.get_note(owner, int(note_id))
    if not note:
        raise ValueError("Заметка не найдена")
    if not note.get("is_knowledge"):
        raise ValueError("С командой можно делиться только документами базы знаний")
    wanted: list[int] = []
    seen: set[int] = set()
    for raw in team_ids or []:
        try:
            tid = int(raw)
        except (TypeError, ValueError) as e:
            raise ValueError("Некорректная команда") from e
        if tid in seen:
            continue
        seen.add(tid)
        wanted.append(tid)
    ts = _now_iso()
    with _LOCK:
        owned = {
            int(r["id"])
            for r in _conn()
            .execute("SELECT id FROM teams WHERE owner_user_id = ?", (owner,))
            .fetchall()
        }
        for tid in wanted:
            if tid not in owned:
                raise ValueError("Команда не найдена")
        _conn().execute(
            "DELETE FROM team_note_shares WHERE owner_user_id = ? AND note_id = ?",
            (owner, int(note_id)),
        )
        for tid in wanted:
            _conn().execute(
                """
                INSERT INTO team_note_shares (team_id, owner_user_id, note_id, shared_at)
                VALUES (?, ?, ?, ?)
                """,
                (tid, owner, int(note_id), ts),
            )
        _conn().commit()
    added = sync_note_team_members(owner, int(note_id))
    return {"teams": list_note_teams(owner, int(note_id)), "added_members": added}


def delete_all_for_note(owner_user_id: int | str, note_id: int) -> int:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM team_note_shares WHERE owner_user_id = ? AND note_id = ?",
            (owner, int(note_id)),
        )
        _conn().commit()
        return int(cur.rowcount or 0)


def _desired_member_ids(owner_user_id: int | str, note_id: int) -> set[str]:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT m.contact_email, m.member_user_id
            FROM team_note_shares s
            JOIN team_members m ON m.team_id = s.team_id
            WHERE s.owner_user_id = ? AND s.note_id = ?
            """,
            (owner, int(note_id)),
        )
        rows = cur.fetchall()
    desired: set[str] = set()
    unresolved: list[str] = []
    for row in rows:
        mid = str(row["member_user_id"] or "").strip()
        email = str(row["contact_email"] or "")
        if mid and mid != owner:
            desired.add(mid)
        elif email:
            unresolved.append(email)
    for email in unresolved:
        resolved = _try_resolve_member_id(owner, email)
        if not resolved or resolved == owner:
            continue
        desired.add(resolved)
        with _LOCK:
            _conn().execute(
                """
                UPDATE team_members SET member_user_id = ?
                WHERE contact_email = ? AND team_id IN (
                    SELECT team_id FROM team_note_shares
                    WHERE owner_user_id = ? AND note_id = ?
                )
                """,
                (resolved, email, owner, int(note_id)),
            )
            _conn().commit()
    desired.discard(owner)
    return desired


def sync_note_team_members(
    owner_user_id: int | str, note_id: int
) -> list[dict[str, Any]]:
    """Выравнивает note_members документа БЗ по составу команд. Возвращает новых участников."""
    from assistant.stores import note_members

    owner = _uid(owner_user_id)
    note = notes_store.get_note(owner, int(note_id))
    if not note or not note.get("is_knowledge"):
        return []
    desired = _desired_member_ids(owner, int(note_id))
    current = {
        str(row["member_user_id"])
        for row in note_members.list_member_rows(owner, int(note_id))
    }
    added: list[dict[str, Any]] = []
    for mid in desired - current:
        try:
            member = note_members.add_member(
                owner, int(note_id), mid, allow_knowledge=True
            )
        except ValueError:
            continue
        added.append(
            {
                "user_id": member["user_id"],
                "note_id": int(note_id),
                "title": str(note.get("title") or ""),
            }
        )
    for mid in current - desired:
        note_members.remove_member(owner, int(note_id), mid)
    return added
