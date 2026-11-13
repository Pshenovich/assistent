"""Старый интерфейс «проектов» записей «Сохранённого» (/tags, проект документа) поверх новых проектов.

С 2026-11 проекты — это именованные связки с участниками (stores/projects.py). Старые личные
ярлыки (таблицы tags / tag_links) один раз переезжают в личные проекты: владелец — хозяин ярлыка,
элементы — документы (note) и записи журнала (journal). Таблицы tags / tag_links больше не пишутся
и остаются только как источник переезда. Соответствие старых id новым — tag_project_map
(нужно, например, чтобы восстановить из корзины документ, удалённый до переезда).

Функции сохраняют прежние имена и форму ответов ({id, name, …}); id — теперь id проекта.
Человек видит здесь проекты, где он участник (свои и общие).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from assistant.lib import sqlite_conn
from assistant.stores import bundles as bundles_store
from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_MIN_NAME_LEN = 1
_MAX_NAME_LEN = 40
_VALID_KINDS = frozenset({"local", "journal"})
# вид записи «Сохранённого» → вид элемента проекта
_ITEM_KIND = {"local": "note", "journal": "journal"}


@sqlite_conn.schema_once(lambda: notes_store._conn())  # type: ignore[attr-defined]
def _conn() -> sqlite3.Connection:
    conn = bundles_store._conn()  # type: ignore[attr-defined]  # та же база, схема связок готова
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
        "CREATE TABLE IF NOT EXISTS tag_project_map (tag_id INTEGER PRIMARY KEY, bundle_id INTEGER NOT NULL)"
    )
    conn.commit()
    migrate_legacy(conn)
    return conn


def migrate_legacy(conn: sqlite3.Connection) -> int:
    """Переезд ярлыков в личные проекты; повторный запуск ничего не дублирует. Возвращает число проектов."""
    if conn.in_transaction:
        conn.commit()
    if not conn.execute(
        "SELECT 1 FROM tags WHERE id NOT IN (SELECT tag_id FROM tag_project_map) LIMIT 1"
    ).fetchone():
        return 0
    moved = 0
    conn.execute("BEGIN IMMEDIATE")  # второй процесс (бот/веб) подождёт и увидит готовое
    try:
        rows = conn.execute(
            "SELECT id, user_id, name, created_at, updated_at FROM tags "
            "WHERE id NOT IN (SELECT tag_id FROM tag_project_map) ORDER BY id"
        ).fetchall()
        for r in rows:
            tid, uid, name = int(r[0]), str(r[1]), " ".join(str(r[2] or "").split())[: bundles_store.MAX_TITLE]
            created, updated = str(r[3]), str(r[4])
            cur = conn.execute(
                "INSERT INTO bundles(owner_user_id, title, kind, created_at, updated_at) VALUES (?, ?, 'project', ?, ?)",
                (uid, name or "Проект", created, updated),
            )
            bid = int(cur.lastrowid)
            conn.execute(
                "INSERT OR IGNORE INTO bundle_members(bundle_id, principal, role, added_by, added_at) "
                "VALUES (?, ?, 'owner', ?, ?)",
                (bid, f"u:{uid}", uid, created),
            )
            for kind, iid, ts in conn.execute(
                "SELECT item_kind, item_id, created_at FROM tag_links WHERE tag_id = ? AND user_id = ?", (tid, uid)
            ).fetchall():
                bk = _ITEM_KIND.get(str(kind))
                if not bk:
                    continue
                try:
                    k, rid = bundles_store.norm_item(bk, iid)
                except ValueError:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO bundle_items(bundle_id, item_kind, item_id, label, added_by, added_at) "
                    "VALUES (?, ?, ?, '', ?, ?)",
                    (bid, k, rid, uid, str(ts)),
                )
            conn.execute("INSERT INTO tag_project_map(tag_id, bundle_id) VALUES (?, ?)", (tid, bid))
            moved += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if moved:
        print(f"[tags] переезд ярлыков в проекты: {moved}")
    return moved


def _ensure() -> None:
    with _LOCK:
        _conn()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_name(name: str) -> str:
    return (name or "").strip()


def _validate_name(name: str) -> str:
    n = _normalize_name(name)
    if len(n) < _MIN_NAME_LEN or len(n) > _MAX_NAME_LEN:
        raise ValueError(
            f"Имя проекта должно быть от {_MIN_NAME_LEN} до {_MAX_NAME_LEN} символов"
        )
    return n


def _name_key(name: str) -> str:
    return _normalize_name(name).casefold()


def _validate_kind(item_kind: str) -> str:
    kind = (item_kind or "").strip().lower()
    if kind not in _VALID_KINDS:
        raise ValueError("Недопустимый тип записи")
    return kind


def _rows(ids: Iterable[int]) -> list[dict[str, Any]]:
    ids = sorted({int(i) for i in ids})
    if not ids:
        return []
    with _LOCK:
        conn = _conn()
        q = ",".join("?" * len(ids))
        return [
            dict(r)
            for r in conn.execute(
                f"SELECT id, title, owner_user_id, org_id, status, color, archived_at, created_at, updated_at "
                f"FROM bundles WHERE id IN ({q}) AND kind = 'project'",
                ids,
            )
        ]


def _as_tag(row: dict[str, Any], uid: str) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["title"] or ""),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "color": str(row.get("color") or ""),
        "status": str(row.get("status") or "active"),
        "shared": str(row.get("owner_user_id") or "") != uid,
    }


def _my_projects(uid: str) -> dict[int, dict[str, Any]]:
    from assistant.stores import projects as projects_store

    _ensure()
    return {int(r["id"]): r for r in _rows(projects_store.ids_for_user(uid))}


def _sorted(tags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(tags, key=lambda t: (str(t["name"]).casefold(), int(t["id"])))


def list_tags(user_id: int | str) -> list[dict[str, Any]]:
    uid = str(int(user_id))
    return _sorted([_as_tag(r, uid) for r in _my_projects(uid).values()])


def _name_taken(uid: str, name: str, *, exclude: Optional[int] = None) -> bool:
    key = _name_key(name)
    return any(
        _name_key(str(r["title"] or "")) == key and int(r["id"]) != (exclude or 0)
        for r in _my_projects(uid).values()
    )


def create_tag(user_id: int | str, name: str) -> dict[str, Any]:
    from assistant.stores import projects as projects_store

    uid = str(int(user_id))
    n = _validate_name(name)
    if _name_taken(uid, n):
        raise ValueError("Проект с таким именем уже существует")
    p = projects_store.create(uid, n)
    return _as_tag(p, uid)


def update_tag(user_id: int | str, tag_id: int, *, name: str) -> Optional[dict[str, Any]]:
    from assistant.stores import projects as projects_store

    uid = str(int(user_id))
    n = _validate_name(name)
    p = projects_store.get(tag_id)
    if not p or not projects_store.role_of(uid, p):
        return None
    if _name_taken(uid, n, exclude=int(tag_id)):
        raise ValueError("Проект с таким именем уже существует")
    p = projects_store.update(uid, tag_id, {"title": n})
    return _as_tag(p, uid)


def delete_tag(user_id: int | str, tag_id: int) -> bool:
    from assistant.stores import projects as projects_store

    uid = str(int(user_id))
    p = projects_store.get(tag_id)
    if not p or projects_store.role_of(uid, p) != "owner":
        return False
    return projects_store.delete(uid, tag_id)


def set_item_tags(
    user_id: int | str,
    item_kind: str,
    item_id: int | str,
    tag_ids: list[int],
) -> list[dict[str, Any]]:
    """Проект записи для простого выбора: один (первый из tag_ids) или никакого ([])."""
    from assistant.stores import projects as projects_store

    uid = str(int(user_id))
    kind = _validate_kind(item_kind)
    iid = str(item_id).strip()
    if not iid:
        raise ValueError("Некорректный идентификатор записи")
    target: Optional[int] = None
    for raw in tag_ids:
        target = int(raw)
        break
    if target is not None:
        p = projects_store.get(target)
        if not p or not projects_store.role_of(uid, p):
            raise ValueError("Проект не найден")
    projects_store.set_item_project(uid, _ITEM_KIND[kind], iid, target)
    return get_item_tags(uid, kind, iid)


def get_item_tags(
    user_id: int | str, item_kind: str, item_id: int | str
) -> list[dict[str, Any]]:
    kind = _validate_kind(item_kind)
    return tags_by_items(user_id, [(kind, str(item_id).strip())]).get((kind, str(item_id).strip()), [])


def tags_by_items(
    user_id: int | str,
    items: list[tuple[str, str]],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Проекты записей (только те, где человек участник)."""
    from assistant.stores import projects as projects_store

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
    keys = {}
    for kind, iid in normalized:
        try:
            keys[(kind, iid)] = bundles_store.norm_item(_ITEM_KIND[kind], iid)
        except ValueError:
            continue
    if not keys:
        return out
    _ensure()
    found = projects_store.ids_with_items(keys.values())
    wanted = {pid for pids in found.values() for pid in pids}
    if not wanted:
        return out
    mine = _my_projects(uid)
    for key, bkey in keys.items():
        out[key] = _sorted([_as_tag(mine[pid], uid) for pid in found.get(bkey, []) if pid in mine])
    return out


def filter_items_by_tags(
    user_id: int | str,
    item_kind: str,
    item_ids: list[str],
    tag_ids: list[int],
    *,
    mode: str = "and",
) -> list[str]:
    kind = _validate_kind(item_kind)
    ids = [str(i).strip() for i in item_ids if str(i).strip()]
    tids = {int(t) for t in tag_ids if int(t) > 0}
    if not ids or not tids:
        return ids if not tids else []
    mapping = tags_by_items(user_id, [(kind, i) for i in ids])
    need = len(tids) if mode == "and" else 1
    return [i for i in ids if len(tids & {int(t["id"]) for t in mapping.get((kind, i), [])}) >= need]


def project_ids_for_legacy_tags(tag_ids: Iterable[Any]) -> list[int]:
    """Старые id ярлыков → id проектов (для корзины документов, удалённых до переезда)."""
    ids = [int(t) for t in tag_ids if str(t).strip().isdigit()]
    if not ids:
        return []
    with _LOCK:
        conn = _conn()
        q = ",".join("?" * len(ids))
        m = {int(r[0]): int(r[1]) for r in conn.execute(f"SELECT tag_id, bundle_id FROM tag_project_map WHERE tag_id IN ({q})", ids)}
    return [m[t] for t in ids if t in m]


def take_extra_tag_links(user_id: int | str) -> list[dict[str, Any]]:
    """Раньше: лишние ярлыки (больше одного на запись) уходили в хэштеги. В проектах запись может
    быть в нескольких проектах — переносить нечего."""
    return []
