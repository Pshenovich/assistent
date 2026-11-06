"""Отделы и команды внутри организации — кому открыт раздел базы знаний или запись сейфа.

Два уровня: отдел (``parent_id IS NULL``, «ОКК») и команды в нём («ОКК · бэкенд»).
Человек может состоять в нескольких. Доступ, открытый отделу, получают его прямые участники
и участники всех его команд; открытый команде — только она.

Ещё есть «Все» (``ALL``) — не строка в базе, а все активные участники организации, кроме гостей.

Отделы создают владелец и админы организации. Руководитель отдела управляет составом отдела
и его командами, но не другими отделами. Неучастник организации в отдел не попадает,
а ушедший из организации выходит из всех её групп (см. ``orgs_store._drop_from_groups``).

Таблицы создаёт ``orgs_store._migrate``.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Optional

from assistant.stores import orgs_store
from assistant.stores.orgs_store import OrgError

_LOCK = orgs_store._LOCK

ALL = "all"
NAME_MAX = 60


def _conn() -> sqlite3.Connection:
    return orgs_store._conn()


def _uid(user_id: int | str) -> str:
    return orgs_store._uid(user_id)


def _clean_name(name: str) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise OrgError("Введите название", 400)
    if len(text) > NAME_MAX:
        raise OrgError(f"Название — до {NAME_MAX} символов", 400)
    return text


def _group(conn: sqlite3.Connection, org_id: int, group_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM org_groups WHERE id = ? AND org_id = ?", (int(group_id), int(org_id))
    ).fetchone()
    if not row:
        raise OrgError("Отдел или команда не найдены", 404)
    return row


def _department_of(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row:
    if row["parent_id"] is None:
        return row
    return _group(conn, int(row["org_id"]), int(row["parent_id"]))


def _is_org_admin(org_id: int, user_id: int | str) -> bool:
    return orgs_store.role_of(org_id, user_id) in ("owner", "admin")


def _require_member(org_id: int, user_id: int | str) -> None:
    if not orgs_store.get_org(org_id) or not orgs_store.role_of(org_id, user_id):
        raise OrgError("Организация не найдена", 404)


def _can_manage(conn: sqlite3.Connection, actor_id: int | str, row: sqlite3.Row, *, admin: bool) -> bool:
    if admin:
        return True
    dept = _department_of(conn, row)
    return str(dept["lead_user_id"] or "") == _uid(actor_id)


def _require_manage(conn: sqlite3.Connection, actor_id: int | str, row: sqlite3.Row, *, admin: bool) -> None:
    # роль в организации узнаём до захвата замка: он общий с orgs_store и не реентерабельный
    if not _can_manage(conn, actor_id, row, admin=admin):
        raise OrgError("Управлять отделом могут его руководитель, владелец и админы организации", 400)


def _name_taken(conn: sqlite3.Connection, org_id: int, parent_id: Optional[int], title: str, *,
                exclude_id: int = 0) -> bool:
    # COLLATE NOCASE в SQLite не понимает кириллицу — сравниваем в Python
    rows = conn.execute(
        "SELECT name FROM org_groups WHERE org_id = ? AND parent_id IS ? AND id != ?",
        (int(org_id), int(parent_id) if parent_id is not None else None, int(exclude_id)),
    ).fetchall()
    return title.casefold() in {str(r["name"]).casefold() for r in rows}


def _active_member(org_id: int, user_id: int | str) -> bool:
    return bool(orgs_store.role_of(org_id, user_id))


# ---------- чтение ----------


def list_groups(org_id: int, actor_id: int | str) -> list[dict[str, Any]]:
    """Отделы с командами и составом. Видят все участники организации — состав не секрет."""
    _require_member(org_id, actor_id)
    can_admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT * FROM org_groups WHERE org_id = ? ORDER BY name, id", (int(org_id),)
        ).fetchall()
        members: dict[int, list[str]] = {}
        for r in conn.execute(
            "SELECT gm.group_id, gm.user_id FROM org_group_members gm "
            "JOIN org_groups g ON g.id = gm.group_id "
            "JOIN org_members m ON m.org_id = g.org_id AND m.user_id = gm.user_id AND m.status = 'active' "
            "WHERE g.org_id = ? ORDER BY gm.added_at",
            (int(org_id),),
        ).fetchall():
            members.setdefault(int(r["group_id"]), []).append(str(r["user_id"]))
        me = _uid(actor_id)

        def item(r: sqlite3.Row) -> dict[str, Any]:
            return {
                "id": int(r["id"]),
                "name": str(r["name"]),
                "parent_id": int(r["parent_id"]) if r["parent_id"] is not None else None,
                "lead_user_id": str(r["lead_user_id"]) if r["lead_user_id"] else None,
                "member_ids": members.get(int(r["id"]), []),
            }

        out: list[dict[str, Any]] = []
        for r in rows:
            if r["parent_id"] is not None:
                continue
            dept = item(r)
            dept["teams"] = [item(t) for t in rows if t["parent_id"] == r["id"]]
            dept["can_manage"] = can_admin or dept["lead_user_id"] == me
            out.append(dept)
    return out


def principals(org_id: int, user_id: int | str) -> set[str]:
    """Через что человеку может быть открыт доступ: ``all``, ``g:<id>`` отделов и команд, ``u:<id>``.

    Состоит в команде → ему открыто и то, что открыто её отделу.
    Состоит в отделе напрямую → ему открыто то, что открыто отделу, но не его командам.
    """
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        m = conn.execute(
            "SELECT role, status, guest FROM org_members WHERE org_id = ? AND user_id = ?", (int(org_id), uid)
        ).fetchone()
        if not m or m["status"] != "active":
            return set()
        out = {f"u:{uid}"}
        if not m["guest"]:
            out.add(ALL)
        for r in conn.execute(
            "SELECT g.id, g.parent_id FROM org_group_members gm JOIN org_groups g ON g.id = gm.group_id "
            "WHERE gm.user_id = ? AND g.org_id = ?",
            (uid, int(org_id)),
        ).fetchall():
            out.add(f"g:{int(r['id'])}")
            if r["parent_id"] is not None:
                out.add(f"g:{int(r['parent_id'])}")
    return out


def member_ids(org_id: int, group_id: int) -> list[str]:
    """Кто получает доступ, открытый отделу или команде (для отдела — вместе с командами)."""
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        ids = [int(row["id"])]
        if row["parent_id"] is None:
            ids += [int(r["id"]) for r in conn.execute(
                "SELECT id FROM org_groups WHERE parent_id = ?", (int(row["id"]),)
            ).fetchall()]
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT DISTINCT gm.user_id FROM org_group_members gm "
            f"JOIN org_members m ON m.org_id = ? AND m.user_id = gm.user_id AND m.status = 'active' "
            f"WHERE gm.group_id IN ({marks}) ORDER BY gm.user_id",
            (int(org_id), *ids),
        ).fetchall()
    return [str(r["user_id"]) for r in rows]


# ---------- изменение ----------


def create(actor_id: int | str, org_id: int, name: str, *, parent_id: Optional[int] = None) -> dict[str, Any]:
    """Отдел (без ``parent_id``) создают владелец и админы; команду в отделе — ещё и его руководитель."""
    _require_member(org_id, actor_id)
    title = _clean_name(name)
    admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        if parent_id is None:
            if not admin:
                raise OrgError("Отделы создают владелец и админы организации", 400)
        else:
            parent = _group(conn, org_id, parent_id)
            if parent["parent_id"] is not None:
                raise OrgError("Команду можно создать только внутри отдела", 400)
            _require_manage(conn, actor_id, parent, admin=admin)
        if _name_taken(conn, org_id, parent_id, title):
            raise OrgError("Такое название уже есть", 400)
        cur = conn.execute(
            "INSERT INTO org_groups (org_id, parent_id, name, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (int(org_id), int(parent_id) if parent_id is not None else None, title, _uid(actor_id),
             orgs_store._now_iso()),
        )
        conn.commit()
        new_id = int(cur.lastrowid)
    return {"id": new_id, "name": title, "parent_id": parent_id}


def rename(actor_id: int | str, org_id: int, group_id: int, name: str) -> None:
    _require_member(org_id, actor_id)
    title = _clean_name(name)
    admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        _require_manage(conn, actor_id, row, admin=admin)
        if _name_taken(conn, org_id, row["parent_id"], title, exclude_id=int(group_id)):
            raise OrgError("Такое название уже есть", 400)
        conn.execute("UPDATE org_groups SET name = ? WHERE id = ?", (title, int(group_id)))
        conn.commit()


def delete(actor_id: int | str, org_id: int, group_id: int) -> None:
    """Отдел удаляют владелец и админы (вместе с командами); команду — ещё и руководитель отдела."""
    _require_member(org_id, actor_id)
    admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        if row["parent_id"] is None and not admin:
            raise OrgError("Отдел удаляют владелец и админы организации", 400)
        _require_manage(conn, actor_id, row, admin=admin)
        ids = [int(group_id)] + [int(r["id"]) for r in conn.execute(
            "SELECT id FROM org_groups WHERE parent_id = ?", (int(group_id),)
        ).fetchall()]
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM org_group_members WHERE group_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM org_groups WHERE id IN ({marks})", ids)
        conn.commit()


def set_lead(actor_id: int | str, org_id: int, group_id: int, user_id: Optional[int | str]) -> None:
    """Руководитель отдела — участник организации; назначают владелец и админы."""
    _require_member(org_id, actor_id)
    if not _is_org_admin(org_id, actor_id):
        raise OrgError("Руководителя назначают владелец и админы организации", 400)
    lead = _uid(user_id) if user_id not in (None, "") else None
    if lead and not _active_member(org_id, lead):
        raise OrgError("Это не участник организации", 400)
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        if row["parent_id"] is not None:
            raise OrgError("Руководитель назначается отделу, а не команде", 400)
        conn.execute("UPDATE org_groups SET lead_user_id = ? WHERE id = ?", (lead, int(group_id)))
        if lead:
            conn.execute(
                "INSERT OR IGNORE INTO org_group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
                (int(group_id), lead, orgs_store._now_iso()),
            )
        conn.commit()


def add_member(actor_id: int | str, org_id: int, group_id: int, user_id: int | str) -> None:
    _require_member(org_id, actor_id)
    uid = _uid(user_id)
    if not _active_member(org_id, uid):
        raise OrgError("Это не участник организации", 400)
    admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        _require_manage(conn, actor_id, row, admin=admin)
        conn.execute(
            "INSERT OR IGNORE INTO org_group_members (group_id, user_id, added_at) VALUES (?, ?, ?)",
            (int(group_id), uid, orgs_store._now_iso()),
        )
        conn.commit()


def remove_member(actor_id: int | str, org_id: int, group_id: int, user_id: int | str) -> None:
    _require_member(org_id, actor_id)
    uid = _uid(user_id)
    admin = _is_org_admin(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        row = _group(conn, org_id, group_id)
        _require_manage(conn, actor_id, row, admin=admin)
        conn.execute("DELETE FROM org_group_members WHERE group_id = ? AND user_id = ?", (int(group_id), uid))
        if str(row["lead_user_id"] or "") == uid:
            conn.execute("UPDATE org_groups SET lead_user_id = NULL WHERE id = ?", (int(group_id),))
        conn.commit()
