"""База знаний организации: дерево разделов и документов с правами.

Узел дерева — раздел (``kind='section'``) или документ (``kind='doc'``, ссылка на ``local_notes.id``).
Права — записи «кому → роль» на узле. Кому: ``all`` (участники без гостей), ``g:<id>`` (отдел или команда,
см. ``org_groups.principals``), ``u:<id>`` (человек).

Наследование: узел с ``inherit=1`` получает права родителя и добавляет свои (так документу «добавляют человека»);
узел с ``inherit=0`` — только свои (так раздел или документ «сужают»). Разделы верхнего уровня всегда
со своими правами; новый раздел никому не открыт, кроме создателя (владелец) и руководства организации.

Роли по возрастанию: читатель < комментатор < редактор < владелец. Владелец и админы организации —
владельцы всего пространства (нужно, когда сотрудник ушёл). Автор документа — его владелец.

Человек, которому открыт документ, но не раздел, видит документ и путь к нему, но не соседей.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from assistant.stores import org_groups, orgs_store
from assistant.stores.orgs_store import OrgError

_LOCK = orgs_store._LOCK

ROLES = ("reader", "commenter", "editor", "owner")
ROLE_RANK = {r: i + 1 for i, r in enumerate(ROLES)}
ROLE_LABEL = {"reader": "Читатель", "commenter": "Комментатор", "editor": "Редактор", "owner": "Владелец"}
VERIFY_DAYS = 180
TITLE_MAX = 120

_SCHEMA_CONN: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _SCHEMA_CONN
    conn = orgs_store._conn()
    if _SCHEMA_CONN is conn:
        return conn
    conn.execute(
        "CREATE TABLE IF NOT EXISTS org_kb_nodes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER NOT NULL, parent_id INTEGER, "
        "kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', note_id INTEGER, position REAL NOT NULL DEFAULT 0, "
        "inherit INTEGER NOT NULL DEFAULT 1, verified_at TEXT, verified_by TEXT, no_expiry INTEGER NOT NULL DEFAULT 0, "
        "created_by TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_org_kb_nodes_org ON org_kb_nodes(org_id, parent_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_org_kb_nodes_note ON org_kb_nodes(note_id) WHERE note_id IS NOT NULL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS org_kb_acl (node_id INTEGER NOT NULL, principal TEXT NOT NULL, "
        "role TEXT NOT NULL, PRIMARY KEY (node_id, principal))"
    )
    conn.commit()
    _SCHEMA_CONN = conn
    return conn


def _uid(user_id: int | str) -> str:
    return orgs_store._uid(user_id)


def _now() -> str:
    return orgs_store._now_iso()


def _clean_title(title: str) -> str:
    text = " ".join(str(title or "").split())
    if not text:
        raise OrgError("Введите название раздела", 400)
    if len(text) > TITLE_MAX:
        raise OrgError(f"Название — до {TITLE_MAX} символов", 400)
    return text


def _max_role(roles: Iterable[str]) -> str:
    best = ""
    for r in roles:
        if ROLE_RANK.get(r, 0) > ROLE_RANK.get(best, 0):
            best = r
    return best


def at_least(role: str, need: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK[need]


# ---------- кто что может (без замка: вызывать вне ``with _LOCK``) ----------


class _Viewer:
    """Всё, что нужно знать о человеке, чтобы посчитать его роли в пространстве организации."""

    def __init__(self, org_id: int, user_id: int | str) -> None:
        self.org_id = int(org_id)
        self.uid = _uid(user_id)
        self.org_role = orgs_store.role_of(org_id, user_id)
        self.principals = org_groups.principals(org_id, user_id) if self.org_role else set()

    @property
    def member(self) -> bool:
        return bool(self.org_role)

    @property
    def admin(self) -> bool:
        return self.org_role in ("owner", "admin")


def _nodes(conn: sqlite3.Connection, org_id: int) -> dict[int, sqlite3.Row]:
    return {int(r["id"]): r for r in conn.execute("SELECT * FROM org_kb_nodes WHERE org_id = ?", (int(org_id),))}


def _acl(conn: sqlite3.Connection, node_ids: Iterable[int]) -> dict[int, dict[str, str]]:
    ids = list(node_ids)
    out: dict[int, dict[str, str]] = {i: {} for i in ids}
    if not ids:
        return out
    marks = ",".join("?" * len(ids))
    for r in conn.execute(f"SELECT * FROM org_kb_acl WHERE node_id IN ({marks})", ids):
        out[int(r["node_id"])][str(r["principal"])] = str(r["role"])
    return out


def _effective_acl(nodes: dict[int, sqlite3.Row], acl: dict[int, dict[str, str]], node_id: int) -> dict[str, str]:
    """Права узла с учётом наследования: свои + родителя, пока ``inherit=1``. Сильнейшая роль побеждает."""
    merged: dict[str, str] = {}
    cur: Optional[int] = node_id
    seen: set[int] = set()
    while cur is not None and cur in nodes and cur not in seen:
        seen.add(cur)
        for p, r in acl.get(cur, {}).items():
            merged[p] = _max_role([merged.get(p, ""), r])
        row = nodes[cur]
        if not row["inherit"] or row["parent_id"] is None:
            break
        cur = int(row["parent_id"])
    return merged


def _role_in(viewer: _Viewer, nodes: dict[int, sqlite3.Row], acl: dict[int, dict[str, str]],
             node_id: int, note_owner: str = "") -> str:
    if not viewer.member:
        return ""
    if viewer.admin:
        return "owner"
    row = nodes[node_id]
    if row["kind"] == "doc" and note_owner and note_owner == viewer.uid:
        return "owner"
    eff = _effective_acl(nodes, acl, node_id)
    return _max_role(eff[p] for p in viewer.principals if p in eff)


def _note_owner(note_id: int) -> str:
    from assistant.stores import notes as notes_store

    note = notes_store.get_note_by_id(int(note_id))
    return str((note or {}).get("owner_user_id") or "")


def node_for_note(note_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute("SELECT * FROM org_kb_nodes WHERE note_id = ?", (int(note_id),)).fetchone()
    return dict(r) if r else None


def role_for_note(user_id: int | str, note_id: int) -> str:
    """Роль человека на документе через пространство организации; '' — нет доступа (или документ не в пространстве)."""
    node = node_for_note(note_id)
    if not node:
        return ""
    viewer = _Viewer(node["org_id"], user_id)
    if not viewer.member:
        return ""
    owner = _note_owner(note_id)
    with _LOCK:
        conn = _conn()
        nodes = _nodes(conn, viewer.org_id)
        acl = _acl(conn, nodes)
    return _role_in(viewer, nodes, acl, int(node["id"]), owner)


def _require_role(viewer: _Viewer, nodes: dict[int, sqlite3.Row], acl: dict[int, dict[str, str]],
                  node_id: int, need: str, msg: str) -> None:
    owner = _note_owner(int(nodes[node_id]["note_id"])) if nodes[node_id]["kind"] == "doc" else ""
    if not at_least(_role_in(viewer, nodes, acl, node_id, owner), need):
        raise OrgError(msg, 400)


def _load(org_id: int, actor_id: int | str) -> tuple[_Viewer, dict[int, sqlite3.Row], dict[int, dict[str, str]]]:
    viewer = _Viewer(org_id, actor_id)
    if not viewer.member:
        raise OrgError("Организация не найдена", 404)
    with _LOCK:
        conn = _conn()
        nodes = _nodes(conn, org_id)
        acl = _acl(conn, nodes)
    return viewer, nodes, acl


def _node(nodes: dict[int, sqlite3.Row], node_id: int) -> sqlite3.Row:
    row = nodes.get(int(node_id))
    if row is None:
        raise OrgError("Раздел или документ не найден", 404)
    return row


# ---------- дерево ----------


def _stale(row: sqlite3.Row, now: datetime) -> bool:
    if row["kind"] != "doc" or row["no_expiry"]:
        return False
    at = row["verified_at"] or row["created_at"]
    try:
        return datetime.fromisoformat(str(at)) < now - timedelta(days=VERIFY_DAYS)
    except ValueError:
        return False


def tree(org_id: int, actor_id: int | str) -> list[dict[str, Any]]:
    """Видимые узлы плоским списком (клиент строит дерево по ``parent_id``).

    Раздел, который не открыт, но внутри есть открытый документ, приходит с ``restricted=True``:
    только название — чтобы был путь, без соседей.
    """
    from assistant.stores import notes as notes_store

    viewer, nodes, acl = _load(org_id, actor_id)
    doc_ids = [int(r["note_id"]) for r in nodes.values() if r["kind"] == "doc"]
    notes = {nid: notes_store.get_note_by_id(nid) or {} for nid in doc_ids}
    now = datetime.now(timezone.utc)
    roles: dict[int, str] = {}
    for nid, row in nodes.items():
        owner = str(notes.get(int(row["note_id"] or 0), {}).get("owner_user_id") or "") if row["kind"] == "doc" else ""
        roles[nid] = _role_in(viewer, nodes, acl, nid, owner)
    visible = {nid for nid, r in roles.items() if r}
    path_only: set[int] = set()
    for nid in visible:
        cur = nodes[nid]["parent_id"]
        while cur is not None and int(cur) in nodes and int(cur) not in visible:
            path_only.add(int(cur))
            cur = nodes[int(cur)]["parent_id"]
    out: list[dict[str, Any]] = []
    for nid in sorted(visible | path_only, key=lambda i: (nodes[i]["position"], i)):
        row = nodes[nid]
        note = notes.get(int(row["note_id"] or 0), {}) if row["kind"] == "doc" else {}
        item: dict[str, Any] = {
            "id": nid,
            "kind": row["kind"],
            "parent_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
            "title": str(note.get("title") or "Без названия") if row["kind"] == "doc" else str(row["title"]),
            "my_role": roles[nid],
            "restricted": nid in path_only,
            "inherit": bool(row["inherit"]),
        }
        if row["kind"] == "section" and nid not in path_only:
            # кому открыт — для подписи «ОКК · Чат-боты»; владельцы-люди не в счёт
            eff = _effective_acl(nodes, acl, nid)
            item["access"] = sorted(p for p, r in eff.items() if not (p.startswith("u:") and r == "owner"))
        if row["kind"] == "doc":
            item.update({
                "note_id": int(row["note_id"]),
                "owner_user_id": str(note.get("owner_user_id") or ""),
                "updated_at": note.get("updated_at"),
                "verified_at": row["verified_at"],
                "no_expiry": bool(row["no_expiry"]),
                "stale": _stale(row, now),
            })
        out.append(item)
    return out


def access(org_id: int, actor_id: int | str, node_id: int) -> dict[str, Any]:
    """Права узла для окна «Доступ»: свои записи, унаследованные и откуда они. Видит владелец узла."""
    viewer, nodes, acl = _load(org_id, actor_id)
    row = _node(nodes, node_id)
    _require_role(viewer, nodes, acl, int(node_id), "owner", "Права меняет владелец раздела или документа")
    inherited: dict[str, str] = {}
    if row["inherit"] and row["parent_id"] is not None:
        inherited = _effective_acl(nodes, acl, int(row["parent_id"]))
    return {
        "node_id": int(node_id),
        "inherit": bool(row["inherit"]),
        "can_inherit": row["parent_id"] is not None,
        "own": [{"principal": p, "role": r} for p, r in sorted(acl.get(int(node_id), {}).items())],
        "inherited": [{"principal": p, "role": r} for p, r in sorted(inherited.items())],
    }


# ---------- изменение ----------


def _next_position(conn: sqlite3.Connection, org_id: int, parent_id: Optional[int]) -> float:
    r = conn.execute(
        "SELECT MAX(position) FROM org_kb_nodes WHERE org_id = ? AND parent_id IS ?", (int(org_id), parent_id)
    ).fetchone()
    return float(r[0] or 0) + 1


def create_section(actor_id: int | str, org_id: int, title: str, *, parent_id: Optional[int] = None) -> dict[str, Any]:
    """Раздел верхнего уровня — владелец и админы; подраздел — редактор родителя.

    Верхний раздел никому не открыт, кроме создателя; подраздел наследует права родителя.
    """
    text = _clean_title(title)
    viewer, nodes, acl = _load(org_id, actor_id)
    if parent_id is None:
        if not viewer.admin:
            raise OrgError("Разделы верхнего уровня создают владелец и админы организации", 400)
    else:
        parent = _node(nodes, parent_id)
        if parent["kind"] != "section":
            raise OrgError("Раздел можно создать только внутри раздела", 400)
        _require_role(viewer, nodes, acl, int(parent_id), "editor", "Создавать подразделы может редактор раздела")
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO org_kb_nodes (org_id, parent_id, kind, title, position, inherit, created_by, created_at) "
            "VALUES (?, ?, 'section', ?, ?, ?, ?, ?)",
            (int(org_id), parent_id, text, _next_position(conn, org_id, parent_id),
             0 if parent_id is None else 1, viewer.uid, _now()),
        )
        node_id = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO org_kb_acl (node_id, principal, role) VALUES (?, ?, 'owner')", (node_id, f"u:{viewer.uid}")
        )
        conn.commit()
    return {"id": node_id, "title": text, "parent_id": parent_id}


def add_doc(actor_id: int | str, org_id: int, section_id: int, note_id: int) -> dict[str, Any]:
    """Положить свой документ в раздел (нужна роль редактора раздела). Документ наследует права раздела."""
    from assistant.stores import notes as notes_store

    viewer, nodes, acl = _load(org_id, actor_id)
    section = _node(nodes, section_id)
    if section["kind"] != "section":
        raise OrgError("Документ кладут в раздел", 400)
    _require_role(viewer, nodes, acl, int(section_id), "editor", "Добавлять документы может редактор раздела")
    note = notes_store.get_note(viewer.uid, int(note_id))
    if not note:
        raise OrgError("Можно добавить только свой документ", 400)
    if note.get("is_transcription"):
        raise OrgError("Транскрипции в базу знаний не добавляются", 400)
    with _LOCK:
        conn = _conn()
        if conn.execute("SELECT 1 FROM org_kb_nodes WHERE note_id = ?", (int(note_id),)).fetchone():
            raise OrgError("Документ уже в базе знаний", 400)
        cur = conn.execute(
            "INSERT INTO org_kb_nodes (org_id, parent_id, kind, note_id, position, inherit, verified_at, verified_by, "
            "created_by, created_at) VALUES (?, ?, 'doc', ?, ?, 1, ?, ?, ?, ?)",
            (int(org_id), int(section_id), int(note_id), _next_position(conn, org_id, int(section_id)),
             _now(), viewer.uid, viewer.uid, _now()),
        )
        conn.commit()
    _reindex(org_id)
    return {"id": int(cur.lastrowid), "note_id": int(note_id), "parent_id": int(section_id)}


def rename_section(actor_id: int | str, org_id: int, node_id: int, title: str) -> None:
    text = _clean_title(title)
    viewer, nodes, acl = _load(org_id, actor_id)
    if _node(nodes, node_id)["kind"] != "section":
        raise OrgError("Документ переименовывают в самом документе", 400)
    _require_role(viewer, nodes, acl, int(node_id), "editor", "Переименовать раздел может его редактор")
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE org_kb_nodes SET title = ? WHERE id = ?", (text, int(node_id)))
        conn.commit()


def _subtree(nodes: dict[int, sqlite3.Row], node_id: int) -> list[int]:
    out = [int(node_id)]
    i = 0
    while i < len(out):
        out += [nid for nid, r in nodes.items() if r["parent_id"] is not None and int(r["parent_id"]) == out[i]]
        i += 1
    return out


def move(actor_id: int | str, org_id: int, node_id: int, parent_id: int, *, position: Optional[float] = None) -> None:
    """Перенести в другой раздел: владелец узла и редактор нового раздела. Права пересчитаются от нового места."""
    viewer, nodes, acl = _load(org_id, actor_id)
    row = _node(nodes, node_id)
    target = _node(nodes, parent_id)
    if target["kind"] != "section":
        raise OrgError("Переносить можно только в раздел", 400)
    if int(parent_id) in _subtree(nodes, int(node_id)):
        raise OrgError("Нельзя перенести раздел внутрь самого себя", 400)
    _require_role(viewer, nodes, acl, int(node_id), "owner", "Переносит владелец раздела или документа")
    _require_role(viewer, nodes, acl, int(parent_id), "editor", "Нет прав на раздел, куда переносите")
    with _LOCK:
        conn = _conn()
        pos = float(position) if position is not None else _next_position(conn, org_id, int(parent_id))
        conn.execute(
            "UPDATE org_kb_nodes SET parent_id = ?, position = ?, inherit = CASE WHEN ? THEN inherit ELSE 1 END "
            "WHERE id = ?",
            (int(parent_id), pos, row["parent_id"] is not None, int(node_id)),
        )
        conn.commit()


def remove(actor_id: int | str, org_id: int, node_id: int) -> None:
    """Убрать из базы знаний. Документ остаётся у автора; раздел убирается только пустым."""
    viewer, nodes, acl = _load(org_id, actor_id)
    row = _node(nodes, node_id)
    _require_role(viewer, nodes, acl, int(node_id), "owner", "Убрать может владелец раздела или документа")
    if row["kind"] == "section" and len(_subtree(nodes, int(node_id))) > 1:
        raise OrgError("Сначала перенесите или уберите то, что внутри раздела", 400)
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM org_kb_acl WHERE node_id = ?", (int(node_id),))
        conn.execute("DELETE FROM org_kb_nodes WHERE id = ?", (int(node_id),))
        conn.commit()
    if row["kind"] == "doc":
        _reindex(org_id)


def _valid_principal(org_id: int, principal: str) -> str:
    p = str(principal or "").strip()
    if p == org_groups.ALL:
        return p
    if p.startswith("u:") and p[2:].isdigit():
        if not orgs_store.role_of(org_id, p[2:]):
            raise OrgError("Это не участник организации", 400)
        return p
    if p.startswith("g:") and p[2:].isdigit():
        with _LOCK:
            ok = _conn().execute(
                "SELECT 1 FROM org_groups WHERE id = ? AND org_id = ?", (int(p[2:]), int(org_id))
            ).fetchone()
        if not ok:
            raise OrgError("Отдел или команда не найдены", 400)
        return p
    raise OrgError("Кому открыть: всем, отделу, команде или человеку", 400)


def set_access(actor_id: int | str, org_id: int, node_id: int, principal: str, role: Optional[str]) -> None:
    """Дать роль (или убрать при ``role=None``). Меняет владелец узла."""
    if role is not None and role not in ROLES:
        raise OrgError("Неизвестная роль", 400)
    p = _valid_principal(org_id, principal)
    viewer, nodes, acl = _load(org_id, actor_id)
    _node(nodes, node_id)
    _require_role(viewer, nodes, acl, int(node_id), "owner", "Права меняет владелец раздела или документа")
    if role is None and p == f"u:{viewer.uid}" and not viewer.admin:
        own = acl.get(int(node_id), {})
        if own.get(p) == "owner" and not any(r == "owner" for q, r in own.items() if q != p):
            raise OrgError("Вы единственный владелец — сначала назначьте другого", 400)
    with _LOCK:
        conn = _conn()
        if role is None:
            conn.execute("DELETE FROM org_kb_acl WHERE node_id = ? AND principal = ?", (int(node_id), p))
        else:
            conn.execute(
                "INSERT INTO org_kb_acl (node_id, principal, role) VALUES (?, ?, ?) "
                "ON CONFLICT(node_id, principal) DO UPDATE SET role = excluded.role",
                (int(node_id), p, role),
            )
        conn.commit()


def set_inherit(actor_id: int | str, org_id: int, node_id: int, inherit: bool) -> None:
    """Сузить (``inherit=False``: только свои права) или вернуть права раздела.

    При сужении текущие унаследованные права копируются в свои — чтобы никто не потерял доступ
    неожиданно; дальше владелец убирает лишних.
    """
    viewer, nodes, acl = _load(org_id, actor_id)
    row = _node(nodes, node_id)
    if row["parent_id"] is None:
        raise OrgError("У раздела верхнего уровня всегда свои права", 400)
    _require_role(viewer, nodes, acl, int(node_id), "owner", "Права меняет владелец раздела или документа")
    with _LOCK:
        conn = _conn()
        if not inherit and row["inherit"]:
            for p, r in _effective_acl(nodes, acl, int(node_id)).items():
                conn.execute(
                    "INSERT INTO org_kb_acl (node_id, principal, role) VALUES (?, ?, ?) "
                    "ON CONFLICT(node_id, principal) DO UPDATE SET role = excluded.role",
                    (int(node_id), p, r),
                )
        conn.execute("UPDATE org_kb_nodes SET inherit = ? WHERE id = ?", (1 if inherit else 0, int(node_id)))
        conn.commit()


def verify(actor_id: int | str, note_id: int, *, no_expiry: Optional[bool] = None) -> None:
    """«Всё актуально» (редактор) или «не устаревает» (владелец)."""
    node = node_for_note(note_id)
    if not node:
        raise OrgError("Документ не в базе знаний", 404)
    viewer, nodes, acl = _load(node["org_id"], actor_id)
    need = "owner" if no_expiry is not None else "editor"
    _require_role(viewer, nodes, acl, int(node["id"]), need, "Нет прав на этот документ")
    with _LOCK:
        conn = _conn()
        if no_expiry is None:
            conn.execute(
                "UPDATE org_kb_nodes SET verified_at = ?, verified_by = ? WHERE id = ?", (_now(), viewer.uid, node["id"])
            )
        else:
            conn.execute("UPDATE org_kb_nodes SET no_expiry = ? WHERE id = ?", (1 if no_expiry else 0, node["id"]))
        conn.commit()


def mark_edited(note_id: int, user_id: int | str) -> None:
    """Правка текста документа = проверка актуальности + переиндексация для Лео. Не бросает."""
    try:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE org_kb_nodes SET verified_at = ?, verified_by = ? WHERE note_id = ?",
                (_now(), _uid(user_id), int(note_id)),
            )
            conn.commit()
            row = conn.execute("SELECT org_id FROM org_kb_nodes WHERE note_id = ?", (int(note_id),)).fetchone()
        if row:
            _reindex(int(row[0]))
    except Exception as e:
        print(f"[org_kb] mark_edited note={note_id} err={e!r}")


def _reindex(org_id: int) -> None:
    """Поиск Лео по базе знаний организации — пересобрать её раздел индекса (с задержкой, повторы склеиваются)."""
    try:
        from assistant.services import semantic_memory

        semantic_memory.org_changed(org_id)
    except Exception as e:
        print(f"[org_kb] reindex org={org_id} err={e!r}")


def stale_for_owner(user_id: int | str) -> list[dict[str, Any]]:
    """Просроченные документы, где человек автор, — для строки в Фокусе раз в неделю."""
    from assistant.stores import notes as notes_store

    uid = _uid(user_id)
    with _LOCK:
        rows = _conn().execute("SELECT * FROM org_kb_nodes WHERE kind = 'doc'").fetchall()
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        if not _stale(r, now):
            continue
        note = notes_store.get_note_by_id(int(r["note_id"])) or {}
        if str(note.get("owner_user_id") or "") != uid:
            continue
        out.append({"note_id": int(r["note_id"]), "org_id": int(r["org_id"]), "title": note.get("title") or "",
                    "verified_at": r["verified_at"]})
    return out


def doc_info(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    """Для открытого документа: в какой организации и разделе, роль, проверка. ``None`` — не в базе знаний."""
    node = node_for_note(note_id)
    if not node:
        return None
    role = role_for_note(user_id, note_id)
    if not role:
        return None
    with _LOCK:
        conn = _conn()
        row = conn.execute("SELECT * FROM org_kb_nodes WHERE id = ?", (node["id"],)).fetchone()
        section = conn.execute("SELECT title FROM org_kb_nodes WHERE id = ?", (node["parent_id"],)).fetchone()
    return {
        "org_id": int(row["org_id"]),
        "node_id": int(row["id"]),
        "section_id": int(row["parent_id"]) if row["parent_id"] is not None else None,
        "section_title": str(section["title"]) if section else "",
        "role": role,
        "verified_at": row["verified_at"],
        "no_expiry": bool(row["no_expiry"]),
        "stale": _stale(row, datetime.now(timezone.utc)),
    }


def my_docs(org_id: int, user_id: int | str, *, limit: int = 300) -> list[dict[str, Any]]:
    """Свои документы, которых ещё нет в базе знаний, — для «Добавить свой документ»."""
    from assistant.stores import notes as notes_store

    viewer = _Viewer(org_id, user_id)
    if not viewer.member:
        raise OrgError("Организация не найдена", 404)
    with _LOCK:
        taken = {int(r[0]) for r in _conn().execute("SELECT note_id FROM org_kb_nodes WHERE note_id IS NOT NULL")}
    out = []
    for n in notes_store.list_notes(viewer.uid, limit=limit, include_knowledge=True):
        if str(n.get("owner_user_id") or n.get("user_id") or "") != viewer.uid or int(n["id"]) in taken:
            continue
        out.append({"note_id": int(n["id"]), "title": n.get("title") or "Без названия",
                    "updated_at": n.get("updated_at"), "is_knowledge": bool(n.get("is_knowledge"))})
    return out


# ---------- уход автора ----------

# Таблицы базы заметок, где документ привязан к владельцу: (таблица, колонка владельца, как найти документ).
# «note» — колонка note_id; «item» — пара item_kind='local' + item_id (текстом).
_OWNED_BY_NOTE = (
    ("note_members", "owner_user_id", "note"),
    ("note_edit_requests", "owner_user_id", "note"),
    ("meeting_links", "owner_user_id", "note"),
    ("note_people", "owner_user_id", "note"),
    ("share_comments", "owner_user_id", "item"),
    ("share_comment_files", "owner_user_id", "item"),
    ("note_chats", "owner_user_id", "item"),
    ("note_chat_reads", "owner_user_id", "item"),
    ("discussion_unread", "owner_user_id", "item"),
    ("share_links", "user_id", "item"),
)
# Личное ушедшего на документе — удалить, а не передавать.
_PERSONAL_BY_ITEM = ("tag_links", "hashtag_links", "note_pins")
_NOOP = __import__("contextlib").nullcontext()


def _table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _new_owner(org_id: int, leaving: str) -> Optional[str]:
    for m in orgs_store.list_members(org_id):
        if m["status"] == "active" and m["role"] == "owner" and m["user_id"] and m["user_id"] != leaving:
            return str(m["user_id"])
    return None


def transfer_docs(org_id: int, from_user_id: int | str) -> list[int]:
    """Человек ушёл из организации: его документы базы знаний переходят владельцу организации.

    Вместе с документом — участники, комментарии, чаты, ссылки, встречи и история версий;
    его личные теги и закрепления на этих документах удаляются, копия в его поиске Лео — тоже.
    Его личные права в пространстве снимаются. Возвращает id переданных документов.
    """
    old = _uid(from_user_id)
    new = _new_owner(org_id, old)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM org_kb_acl WHERE principal = ? AND node_id IN (SELECT id FROM org_kb_nodes WHERE org_id = ?)",
            (f"u:{old}", int(org_id)),
        )
        ids = [int(r[0]) for r in conn.execute(
            "SELECT n.note_id FROM org_kb_nodes n JOIN local_notes l ON l.id = n.note_id "
            "WHERE n.org_id = ? AND n.kind = 'doc' AND l.user_id = ?",
            (int(org_id), old),
        ).fetchall()]
        if not ids or not new:
            conn.commit()
            if ids:
                print(f"[org_kb] transfer org={org_id} from={old}: no other owner, {len(ids)} docs stay")
            return []
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        for nid in ids:
            conn.execute("UPDATE local_notes SET user_id = ? WHERE id = ? AND user_id = ?", (new, nid, old))
            for table, col, how in _OWNED_BY_NOTE:
                if table not in tables:
                    continue
                if how == "note":
                    if table == "note_members":
                        # новый владелец был участником — теперь он владелец, строка участника лишняя
                        conn.execute("DELETE FROM note_members WHERE note_id = ? AND owner_user_id = ? AND member_user_id = ?",
                                     (nid, old, new))
                    conn.execute(f"UPDATE OR IGNORE {table} SET {col} = ? WHERE note_id = ? AND {col} = ?", (new, nid, old))
                    conn.execute(f"DELETE FROM {table} WHERE note_id = ? AND {col} = ?", (nid, old))
                else:
                    conn.execute(
                        f"UPDATE OR IGNORE {table} SET {col} = ? WHERE item_kind = 'local' AND item_id = ? AND {col} = ?",
                        (new, str(nid), old),
                    )
                    conn.execute(f"DELETE FROM {table} WHERE item_kind = 'local' AND item_id = ? AND {col} = ?",
                                 (str(nid), old))
            if "team_note_shares" in tables:
                conn.execute("DELETE FROM team_note_shares WHERE note_id = ? AND owner_user_id = ?", (nid, old))
            for table in _PERSONAL_BY_ITEM:
                if table in tables and "user_id" in _table_cols(conn, table):
                    conn.execute(f"DELETE FROM {table} WHERE user_id = ? AND item_kind = 'local' AND item_id = ?",
                                 (old, str(nid)))
        conn.commit()
    _transfer_outside(ids, old, new)
    print(f"[org_kb] transfer org={org_id} from={old} to={new} docs={ids}")
    return ids


def _transfer_outside(ids: list[int], old: str, new: str) -> None:
    """Отдельные базы: история версий — новому владельцу, поиск Лео ушедшего — без этих документов."""
    try:
        from assistant.stores import note_versions

        with getattr(note_versions, "_LOCK", _NOOP):
            vconn = note_versions._conn()  # type: ignore[attr-defined]
            for nid in ids:
                vconn.execute("UPDATE note_versions SET owner_user_id = ? WHERE note_id = ? AND owner_user_id = ?",
                              (new, nid, old))
            vconn.commit()
    except Exception as e:
        print(f"[org_kb] transfer versions err={e!r}")
    try:
        from assistant.stores import semantic_index

        for nid in ids:
            for key in (f"note:{nid}", f"kb:{nid}"):
                semantic_index.delete_source(old, key)
    except Exception as e:
        print(f"[org_kb] transfer semantic err={e!r}")


# ---------- для Лео ----------


def org_ids_of(user_id: int | str) -> list[int]:
    return [int(o["id"]) for o in orgs_store.orgs_for_user(user_id)]


def org_doc_note_ids(org_id: int) -> list[int]:
    with _LOCK:
        return [int(r[0]) for r in _conn().execute(
            "SELECT note_id FROM org_kb_nodes WHERE org_id = ? AND kind = 'doc'", (int(org_id),)
        ).fetchall()]


def readable_note_ids(user_id: int | str, org_id: int) -> set[int]:
    """Документы организации, которые человек может читать (одним проходом по дереву)."""
    try:
        return {int(n["note_id"]) for n in tree(org_id, user_id) if n["kind"] == "doc" and n.get("my_role")}
    except OrgError:
        return set()


def readable_notes(user_id: int | str) -> list[dict[str, Any]]:
    """Все документы баз знаний организаций, открытые человеку, — для поиска документа по названию."""
    from assistant.stores import notes as notes_store

    out = []
    for oid in org_ids_of(user_id):
        for nid in sorted(readable_note_ids(user_id, oid)):
            n = notes_store.get_note_by_id(nid)
            if n:
                out.append(n)
    return out


def all_doc_note_ids() -> set[int]:
    """Все документы баз знаний организаций — их индексирует раздел организации, а не личный раздел автора."""
    with _LOCK:
        return {int(r[0]) for r in _conn().execute("SELECT note_id FROM org_kb_nodes WHERE kind = 'doc'").fetchall()}
