"""Проекты — именованные связки с участниками, руководителем и статусом.

Хранятся в тех же таблицах, что и «Связанное» (bundles, bundle_items) с kind='project';
участники — bundle_members. Здесь — кто участник и с какой ролью, список проектов человека,
настройки проекта. Что из элементов человеку видно, решает services/bundle_access.py.

Участник (principal): ``u:<id>`` — человек; ``g:<id>`` — отдел или команда организации проекта;
``all`` — вся организация проекта (без гостей). Роли: viewer < editor < owner.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from assistant.stores import bundles as bundles_store

ROLES = ("viewer", "editor", "owner")
ROLE_RANK = {r: i + 1 for i, r in enumerate(ROLES)}
ROLE_LABEL = {"viewer": "Смотрит", "editor": "Редактирует", "owner": "Владелец"}
STATUSES = ("active", "paused", "done")
STATUS_LABEL = {"active": "Идёт", "paused": "На паузе", "done": "Завершён"}
COLORS = ("", "blue", "green", "orange", "red", "purple", "teal", "gray")
MAX_DESCRIPTION = 2000

_LOCK = bundles_store._LOCK  # type: ignore[attr-defined]


def _conn():
    return bundles_store._conn()  # type: ignore[attr-defined]


def _uid(user_id: Any) -> str:
    return str(int(user_id))


def at_least(role: str, need: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK[need]


def _max_role(roles: Iterable[str]) -> str:
    best = ""
    for r in roles:
        if ROLE_RANK.get(r, 0) > ROLE_RANK.get(best, 0):
            best = r
    return best


def _legacy_ready() -> None:
    """Старые ярлыки-проекты переезжают при первом открытии базы (stores/tags.py)."""
    from assistant.stores import tags as tags_store

    tags_store._ensure()  # type: ignore[attr-defined]


# ---------- через что человеку открыт проект ----------


def _org_principals(org_id: str, uid: str) -> set[str]:
    if not str(org_id or "").strip().isdigit():
        return set()
    try:
        from assistant.stores import org_groups

        return set(org_groups.principals(int(org_id), uid))
    except Exception as e:
        print(f"[projects] principals org={org_id} uid={uid} err={e!r}")
        return set()


def principals_for(user_id: Any, org_id: Any = "") -> set[str]:
    """Чем человек может совпасть с участником проекта организации org_id ('' — личный проект)."""
    uid = _uid(user_id)
    out = {f"u:{uid}"}
    out |= _org_principals(str(org_id or ""), uid)
    return out


def _members(conn, bundle_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT principal, role, added_by, added_at FROM bundle_members WHERE bundle_id = ? ORDER BY added_at, rowid",
            (int(bundle_id),),
        )
    ]


def members(bundle_id: Any) -> list[dict[str, Any]]:
    with _LOCK:
        return _members(_conn(), int(bundle_id))


def role_of(user_id: Any, project: Optional[dict[str, Any]]) -> str:
    """Роль человека в проекте ('' — не участник). Руководитель проекта — минимум редактор."""
    if not bundles_store.is_project(project):
        return ""
    assert project is not None
    uid = _uid(user_id)
    mine = principals_for(uid, project.get("org_id") or "")
    rows = project.get("members")
    if rows is None:
        rows = members(project["id"])
    roles = [str(m["role"]) for m in rows if str(m["principal"]) in mine]
    if str(project.get("lead_user_id") or "") == uid:
        roles.append("editor")
    return _max_role(roles)


def get(project_id: Any) -> Optional[dict[str, Any]]:
    """Проект с элементами и участниками (без проверки доступа); None — нет или это не проект."""
    b = bundles_store.get(project_id)
    if not bundles_store.is_project(b):
        return None
    assert b is not None
    b["members"] = members(b["id"])
    return b


def ids_for_user(user_id: Any, *, include_archived: bool = False) -> list[int]:
    """Проекты, где человек участник (лично, через отдел/команду или всю организацию)."""
    uid = _uid(user_id)
    _legacy_ready()
    org_principals: dict[str, set[str]] = {}
    try:
        from assistant.stores import orgs_store

        for org in orgs_store.orgs_for_user(uid):
            oid = str(org.get("id") or "")
            if oid:
                org_principals[oid] = _org_principals(oid, uid) - {f"u:{uid}"}
    except Exception as e:
        print(f"[projects] orgs uid={uid} err={e!r}")
    arch = "" if include_archived else " AND b.archived_at = ''"
    with _LOCK:
        conn = _conn()
        ids = {
            int(r[0])
            for r in conn.execute(
                "SELECT b.id FROM bundles b JOIN bundle_members m ON m.bundle_id = b.id "
                f"WHERE b.kind = 'project' AND m.principal = ?{arch} "
                f"UNION SELECT b.id FROM bundles b WHERE b.kind = 'project' AND b.lead_user_id = ?{arch}",
                (f"u:{uid}", uid),
            )
        }
        for oid, prs in org_principals.items():
            if not prs:
                continue
            q = ",".join("?" * len(prs))
            ids.update(
                int(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT b.id FROM bundles b JOIN bundle_members m ON m.bundle_id = b.id "
                    f"WHERE b.kind = 'project' AND b.org_id = ? AND m.principal IN ({q}){arch}",
                    (oid, *sorted(prs)),
                )
            )
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        return [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM bundles WHERE id IN ({q}) ORDER BY LOWER(title), id", sorted(ids)
            )
        ]


def ids_with_item(kind: Any, item_id: Any) -> list[int]:
    k, rid = bundles_store.norm_item(kind, item_id)
    _legacy_ready()
    with _LOCK:
        return [
            int(r[0])
            for r in _conn().execute(
                "SELECT b.id FROM bundle_items i JOIN bundles b ON b.id = i.bundle_id "
                "WHERE b.kind = 'project' AND i.item_kind = ? AND i.item_id = ?",
                (k, rid),
            )
        ]


def ids_with_items(keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], list[int]]:
    """{(вид, id): [id проектов]} одним проходом (для списков документов)."""
    norm: list[tuple[str, str]] = []
    for k, rid in keys:
        try:
            norm.append(bundles_store.norm_item(k, rid))
        except ValueError:
            continue
    out: dict[tuple[str, str], list[int]] = {key: [] for key in norm}
    if not norm:
        return out
    by_kind: dict[str, list[str]] = {}
    for k, rid in norm:
        by_kind.setdefault(k, []).append(rid)
    with _LOCK:
        conn = _conn()
        for k, rids in by_kind.items():
            for i in range(0, len(rids), 500):
                chunk = rids[i : i + 500]
                q = ",".join("?" * len(chunk))
                for r in conn.execute(
                    "SELECT i.item_id, b.id FROM bundle_items i JOIN bundles b ON b.id = i.bundle_id "
                    f"WHERE b.kind = 'project' AND i.item_kind = ? AND i.item_id IN ({q}) ORDER BY LOWER(b.title), b.id",
                    (k, *chunk),
                ):
                    out.setdefault((k, str(r[0])), []).append(int(r[1]))
    return out


# ---------- изменения ----------


def _require(user_id: Any, project: Optional[dict[str, Any]], need: str) -> dict[str, Any]:
    if not project:
        raise ValueError("Проект не найден")
    role = role_of(user_id, project)
    if not role:
        raise ValueError("Проект не найден")
    if not at_least(role, need):
        raise ValueError("Только владелец проекта может это сделать" if need == "owner" else "Нет прав менять проект")
    return project


def _clean_principal(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    if p == "all":
        return p
    if p.startswith(("u:", "g:")) and p[2:].isdigit() and int(p[2:]) > 0:
        return f"{p[:2]}{int(p[2:])}"
    raise ValueError("Не понял, кого добавить")


def create(
    user_id: Any,
    title: Any,
    *,
    org_id: Any = "",
    lead_user_id: Any = "",
    description: Any = "",
    color: Any = "",
    items: Optional[list[tuple[str, str, str]]] = None,
) -> dict[str, Any]:
    clean = bundles_store._clean_title(title)  # type: ignore[attr-defined]
    if not clean:
        raise ValueError("Введите название проекта")
    b = bundles_store.create(user_id, clean, list(items or []))
    patch: dict[str, Any] = {}
    if str(org_id or "").strip():
        patch["org_id"] = str(org_id).strip()
    if str(lead_user_id or "").strip():
        patch["lead_user_id"] = str(int(lead_user_id))
    if description:
        patch["description"] = str(description)[:MAX_DESCRIPTION]
    if color:
        patch["color"] = str(color)
    if patch:
        _write(int(b["id"]), patch)
    if items:
        _inherit(int(b["id"]), [(k, rid) for k, rid, _l in items])
    return get(b["id"]) or {}


def _inherit(project_id: int, keys: list[tuple[str, str]]) -> None:
    try:
        from assistant.services import project_inherit

        project_inherit.after_add(project_id, keys)
    except Exception as e:
        print(f"[projects] inherit pid={project_id} err={e!r}")


def _write(bundle_id: int, patch: dict[str, Any]) -> None:
    cols = ", ".join(f"{k} = ?" for k in patch)
    with _LOCK:
        conn = _conn()
        conn.execute(
            f"UPDATE bundles SET {cols}, updated_at = ? WHERE id = ?",
            (*patch.values(), bundles_store._now(), int(bundle_id)),  # type: ignore[attr-defined]
        )
        conn.commit()


def update(user_id: Any, project_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    """Поменять название, описание, цвет, статус, руководителя (редактор) или архив (владелец)."""
    p = _require(user_id, get(project_id), "editor")
    out: dict[str, Any] = {}
    if "title" in patch and patch["title"] is not None:
        clean = bundles_store._clean_title(patch["title"])  # type: ignore[attr-defined]
        if not clean:
            raise ValueError("У проекта должно быть название")
        out["title"] = clean
    if "description" in patch and patch["description"] is not None:
        out["description"] = str(patch["description"]).strip()[:MAX_DESCRIPTION]
    if "color" in patch and patch["color"] is not None:
        if str(patch["color"]) not in COLORS:
            raise ValueError("Нет такого цвета")
        out["color"] = str(patch["color"])
    if "status" in patch and patch["status"] is not None:
        if str(patch["status"]) not in STATUSES:
            raise ValueError("Нет такого статуса")
        out["status"] = str(patch["status"])
    if "lead_user_id" in patch and patch["lead_user_id"] is not None:
        raw = str(patch["lead_user_id"]).strip()
        out["lead_user_id"] = str(int(raw)) if raw else ""
    if "archived" in patch and patch["archived"] is not None:
        _require(user_id, p, "owner")
        out["archived_at"] = bundles_store._now() if patch["archived"] else ""  # type: ignore[attr-defined]
    if out:
        _write(int(p["id"]), out)
    return get(p["id"]) or {}


def delete(user_id: Any, project_id: Any) -> bool:
    p = _require(user_id, get(project_id), "owner")
    return bundles_store.delete(p["id"])


def set_member(user_id: Any, project_id: Any, principal: Any, role: str) -> dict[str, Any]:
    """Добавить участника или поменять роль. Редактор добавляет смотрящих и редакторов,
    владельцев назначает только владелец. Группы и «вся организация» — только у проекта организации."""
    p = _require(user_id, get(project_id), "editor")
    pr = _clean_principal(principal)
    if role not in ROLES:
        raise ValueError("Нет такой роли")
    mine = role_of(user_id, p)
    if role == "owner" and mine != "owner":
        raise ValueError("Владельцев назначает только владелец проекта")
    current = {str(m["principal"]): str(m["role"]) for m in p["members"]}
    if current.get(pr) == "owner" and mine != "owner":
        raise ValueError("Менять владельца может только владелец проекта")
    if pr == "all" or pr.startswith("g:"):
        if not str(p.get("org_id") or "").strip():
            raise ValueError("Отдел, команду или всю организацию можно добавить только в проект организации")
    if current.get(pr) == "owner" and role != "owner":
        owners = [k for k, v in current.items() if v == "owner"]
        if len(owners) <= 1:
            raise ValueError("У проекта должен остаться владелец")
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO bundle_members(bundle_id, principal, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(bundle_id, principal) DO UPDATE SET role = excluded.role",
            (int(p["id"]), pr, role, uid, bundles_store._now()),  # type: ignore[attr-defined]
        )
        conn.commit()
    return get(p["id"]) or {}


def remove_member(user_id: Any, project_id: Any, principal: Any) -> dict[str, Any]:
    """Убрать участника: редактор — не-владельцев, владелец — любого; себя убрать может каждый
    (кроме последнего владельца)."""
    p = get(project_id)
    pr = _clean_principal(principal)
    uid = _uid(user_id)
    if pr != f"u:{uid}":
        _require(user_id, p, "editor")
    elif not p or not role_of(user_id, p):
        raise ValueError("Проект не найден")
    assert p is not None
    current = {str(m["principal"]): str(m["role"]) for m in p["members"]}
    if pr not in current:
        return p
    if current[pr] == "owner":
        if pr != f"u:{uid}" and role_of(user_id, p) != "owner":
            raise ValueError("Убрать владельца может только владелец проекта")
        if sum(1 for v in current.values() if v == "owner") <= 1:
            raise ValueError("У проекта должен остаться владелец")
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM bundle_members WHERE bundle_id = ? AND principal = ?", (int(p["id"]), pr))
        conn.commit()
    return get(p["id"]) or {}


def add_items(user_id: Any, project_id: Any, items: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Добавить элементы (уже проверенные на доступ вызывающим) — нужна роль редактора."""
    p = _require(user_id, get(project_id), "editor")
    if items:
        bundles_store.add_items(user_id, p["id"], items)
        _inherit(int(p["id"]), [bundles_store.norm_item(k, rid) for k, rid, _l in items])
    return get(p["id"]) or {}


def remove_item(user_id: Any, project_id: Any, kind: Any, item_id: Any) -> dict[str, Any]:
    """Убрать элемент: редактор проекта или тот, кто элемент добавил."""
    p = get(project_id)
    if not p or not role_of(user_id, p):
        raise ValueError("Проект не найден")
    k, rid = bundles_store.norm_item(kind, item_id)
    it = next((i for i in p["items"] if (i["item_kind"], i["item_id"]) == (k, rid)), None)
    if not it:
        return p
    if str(it.get("added_by") or "") != _uid(user_id) and not at_least(role_of(user_id, p), "editor"):
        raise ValueError("Убрать может редактор проекта или тот, кто добавил")
    bundles_store.remove_item(p["id"], k, rid)
    return get(p["id"]) or {}


def set_item_project(user_id: Any, kind: Any, item_id: Any, project_id: Optional[Any]) -> list[int]:
    """«Проект элемента» для простого выбора (одна галочка): элемент убирается из проектов человека,
    где он может править, и кладётся в выбранный. Проекты, где человек только смотрит, не трогаются."""
    k, rid = bundles_store.norm_item(kind, item_id)
    uid = _uid(user_id)
    target = get(project_id) if project_id not in (None, "", 0) else None
    if project_id not in (None, "", 0):
        _require(uid, target, "editor")
    for pid in ids_with_item(k, rid):
        if target and pid == int(target["id"]):
            continue
        p = get(pid)
        if p and at_least(role_of(uid, p), "editor"):
            bundles_store.remove_item(pid, k, rid)
    if target and not any((i["item_kind"], i["item_id"]) == (k, rid) for i in target["items"]):
        bundles_store.add_items(uid, target["id"], [(k, rid, "")])
    return [pid for pid in ids_with_item(k, rid) if role_of(uid, get(pid))]


def principal_labels(org_id: Any, principals: Iterable[str]) -> dict[str, str]:
    """Подписи участников: имя человека, название отдела/команды, «Вся организация»."""
    from assistant.stores import note_members

    out: dict[str, str] = {}
    groups: list[int] = []
    for pr in principals:
        pr = str(pr)
        if pr == "all":
            out[pr] = "Вся организация"
        elif pr.startswith("u:"):
            try:
                out[pr] = note_members.profile_display_name(note_members.get_profile(pr[2:]))
            except Exception:
                out[pr] = "Участник"
        elif pr.startswith("g:") and pr[2:].isdigit():
            groups.append(int(pr[2:]))
    if groups and str(org_id or "").strip().isdigit():
        try:
            from assistant.stores import orgs_store

            with orgs_store._LOCK:  # type: ignore[attr-defined]
                q = ",".join("?" * len(groups))
                for r in orgs_store._conn().execute(  # type: ignore[attr-defined]
                    f"SELECT id, name FROM org_groups WHERE org_id = ? AND id IN ({q})", (int(org_id), *groups)
                ):
                    out[f"g:{int(r['id'])}"] = str(r["name"] or "Отдел")
        except Exception as e:
            print(f"[projects] group labels org={org_id} err={e!r}")
    for g in groups:
        out.setdefault(f"g:{g}", "Отдел или команда")
    return out


def find_for_user(user_id: Any, name: Any, *, need: str = "viewer") -> list[dict[str, Any]]:
    """Проекты человека по названию (для Лео): точное совпадение без учёта регистра — первым и одно;
    иначе все, где название содержит все слова. need — минимальная роль."""
    q = " ".join(str(name or "").lower().split())
    if not q:
        return []
    exact, partial = [], []
    for pid in ids_for_user(user_id):
        p = get(pid)
        if not p or not at_least(role_of(user_id, p), need):
            continue
        title = str(p.get("title") or "").lower()
        if title == q:
            exact.append(p)
        elif all(w in title for w in q.split()):
            partial.append(p)
    return exact[:1] or partial


def webapp_url(project_id: Any) -> str:
    """Ссылка на страницу проекта в приложении (…/webapp/?project=N), как у документов ?note=N."""
    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}project={int(project_id)}"
