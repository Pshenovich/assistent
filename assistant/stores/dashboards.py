"""Дашборды: страницы с цифрами и графиками, которые наполняются автоматически.

Дашборд принадлежит организации (org_id) или человеку (org_id=''). Доступ — участники:
``u:<id>`` человек, ``g:<id>`` отдел/команда организации, ``all`` — вся организация (без гостей).
Роли viewer < editor < owner. Создатель — owner; владелец и админ организации — owner всех её дашбордов.

Данные приходят в источники (datasets) с серверов компании по ключу источника ``dsk_…``
(в базе только sha256). Виды источников: metric (числа во времени), table (строки), events (лента).
Виджеты — только из каталога assistant/lib/dashboard_widgets.py.

Ошибки прав — ValueError (на API → 400, не 403).
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from assistant.lib import dashboard_widgets as W
from assistant.lib import sqlite_conn
from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

ROLES = ("viewer", "editor", "owner")
ROLE_RANK = {r: i + 1 for i, r in enumerate(ROLES)}
ROLE_LABEL = {"viewer": "Смотрит", "editor": "Редактирует", "owner": "Владелец"}

KEY_PREFIX = "dsk_"
TITLE_MAX = 80
DESCRIPTION_MAX = 1000
MAX_DATASETS = 50
MAX_WIDGETS = 60
MAX_POINTS_PER_PUSH = 5000
MAX_ROWS = 5000
MAX_EVENTS_KEEP = 1000
MAX_BODY_BYTES = 1_000_000
RATE_PER_MIN = 60
KEEP_DAYS = 731
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class DashboardError(ValueError):
    def __init__(self, msg: str, code: int = 400):
        super().__init__(msg)
        self.code = code


@sqlite_conn.schema_once(lambda: notes_store._conn())  # type: ignore[attr-defined]
def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dashboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id TEXT NOT NULL DEFAULT '',
            owner_uid TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_dashboards_org ON dashboards(org_id);
        CREATE TABLE IF NOT EXISTS dashboard_members (
            dashboard_id INTEGER NOT NULL,
            principal TEXT NOT NULL,
            role TEXT NOT NULL,
            added_by TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            PRIMARY KEY (dashboard_id, principal)
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_members_pr ON dashboard_members(principal);
        CREATE TABLE IF NOT EXISTS dashboard_datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dashboard_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            unit TEXT NOT NULL DEFAULT '',
            schema_json TEXT NOT NULL DEFAULT '{}',
            expected_every_min INTEGER NOT NULL DEFAULT 0,
            ingest_hash TEXT NOT NULL,
            ingest_hint TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_push_at TEXT NOT NULL DEFAULT '',
            last_push_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            last_error_at TEXT NOT NULL DEFAULT '',
            UNIQUE(dashboard_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_datasets_hash ON dashboard_datasets(ingest_hash);
        CREATE TABLE IF NOT EXISTS dashboard_points (
            dataset_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            series TEXT NOT NULL DEFAULT '',
            value REAL NOT NULL,
            all_day INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (dataset_id, ts, series)
        );
        CREATE TABLE IF NOT EXISTS dashboard_rows (
            dataset_id INTEGER NOT NULL,
            row_key TEXT NOT NULL,
            pos INTEGER NOT NULL DEFAULT 0,
            data_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (dataset_id, row_key)
        );
        CREATE TABLE IF NOT EXISTS dashboard_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            text TEXT NOT NULL,
            url TEXT NOT NULL DEFAULT '',
            ext_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_events_ds ON dashboard_events(dataset_id, ts);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_events_ext ON dashboard_events(dataset_id, ext_id)
            WHERE ext_id != '';
        CREATE TABLE IF NOT EXISTS dashboard_widgets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dashboard_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            dataset_id INTEGER,
            options_json TEXT NOT NULL DEFAULT '{}',
            position INTEGER NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_dashboard_widgets_d ON dashboard_widgets(dashboard_id, position);
        """
    )
    conn.commit()
    return conn


# ---------- общее ----------


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: Any) -> str:
    try:
        return str(int(user_id))
    except (TypeError, ValueError):
        raise DashboardError("Нужен пользователь")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _short(v: Any, n: int) -> str:
    return " ".join(str(v if v is not None else "").split())[:n]


def at_least(role: str, need: str) -> bool:
    return ROLE_RANK.get(role or "", 0) >= ROLE_RANK[need]


def _jload(s: Any, default: Any) -> Any:
    try:
        v = json.loads(s or "")
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _dash_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "org_id": str(r["org_id"] or ""),
        "owner_uid": str(r["owner_uid"]),
        "title": str(r["title"]),
        "description": str(r["description"] or ""),
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
        "archived": bool(r["archived_at"]),
    }


def _ds_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "dashboard_id": int(r["dashboard_id"]),
        "key": str(r["key"]),
        "kind": str(r["kind"]),
        "title": str(r["title"] or ""),
        "unit": str(r["unit"] or ""),
        "schema": _jload(r["schema_json"], {}),
        "expected_every_min": int(r["expected_every_min"] or 0),
        "key_hint": str(r["ingest_hint"] or ""),
        "created_at": r["created_at"],
        "last_push_at": r["last_push_at"] or "",
        "last_push_count": int(r["last_push_count"] or 0),
        "last_error": str(r["last_error"] or ""),
        "last_error_at": r["last_error_at"] or "",
    }


def _w_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]),
        "dashboard_id": int(r["dashboard_id"]),
        "kind": str(r["kind"]),
        "title": str(r["title"] or ""),
        "dataset_id": int(r["dataset_id"]) if r["dataset_id"] is not None else None,
        "options": _jload(r["options_json"], {}),
        "position": int(r["position"]),
        "width": int(r["width"]),
    }


def stale(ds: dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Источник молчит дольше двух ожидаемых интервалов (если интервал задан)."""
    every = int(ds.get("expected_every_min") or 0)
    if every <= 0:
        return False
    last = ds.get("last_push_at") or ds.get("created_at")
    try:
        t = datetime.fromisoformat(str(last))
    except (TypeError, ValueError):
        return False
    return (now or datetime.now(timezone.utc)) - t > timedelta(minutes=2 * every)


# ---------- доступ ----------


def _org_principals(org_id: str, uid: str) -> set[str]:
    if not str(org_id or "").isdigit():
        return set()
    try:
        from assistant.stores import org_groups

        return set(org_groups.principals(int(org_id), uid))
    except Exception as e:
        print(f"[dashboards] principals org={org_id} uid={uid} err={e!r}")
        return set()


def _org_role(org_id: str, uid: str) -> str:
    if not str(org_id or "").isdigit():
        return ""
    try:
        from assistant.stores import orgs_store

        return orgs_store.role_of(int(org_id), uid)
    except Exception:
        return ""


def _members(conn: sqlite3.Connection, dash_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT principal, role, added_by, added_at FROM dashboard_members WHERE dashboard_id = ? "
            "ORDER BY added_at, rowid",
            (int(dash_id),),
        )
    ]


def role_of(user_id: Any, dash: Optional[dict[str, Any]]) -> str:
    """Роль человека в дашборде ('' — нет доступа)."""
    if not dash:
        return ""
    uid = _uid(user_id)
    org = str(dash.get("org_id") or "")
    if org:
        orole = _org_role(org, uid)
        if not orole:
            return ""  # ушёл из организации — доступа нет, даже если был участником
        if orole in ("owner", "admin"):
            return "owner"
    if str(dash.get("owner_uid") or "") == uid:
        return "owner"
    mine = {f"u:{uid}"} | _org_principals(org, uid)
    rows = dash.get("members")
    if rows is None:
        with _LOCK:
            rows = _members(_conn(), int(dash["id"]))
    best = ""
    for m in rows:
        if str(m["principal"]) in mine and ROLE_RANK.get(str(m["role"]), 0) > ROLE_RANK.get(best, 0):
            best = str(m["role"])
    return best


def get(dash_id: Any) -> Optional[dict[str, Any]]:
    try:
        did = int(dash_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        conn = _conn()
        r = conn.execute("SELECT * FROM dashboards WHERE id = ?", (did,)).fetchone()
        if not r:
            return None
        d = _dash_row(r)
        d["members"] = _members(conn, did)
    return d


def require(user_id: Any, dash_id: Any, need: str = "viewer") -> dict[str, Any]:
    """Дашборд с ролью человека; иначе DashboardError «не найден» / «нет прав»."""
    d = get(dash_id)
    role = role_of(user_id, d) if d else ""
    if not d or not role:
        raise DashboardError("Дашборд не найден")
    if not at_least(role, need):
        raise DashboardError(
            "Это может только владелец дашборда" if need == "owner" else "Нет прав менять этот дашборд"
        )
    d["my_role"] = role
    return d


def list_for(user_id: Any, *, include_archived: bool = False) -> list[dict[str, Any]]:
    """Дашборды, которые человек видит: личные, где участник, и организаций (по участию или как админ)."""
    uid = _uid(user_id)
    org_roles: dict[str, str] = {}
    try:
        from assistant.stores import orgs_store

        for o in orgs_store.orgs_for_user(uid):
            org_roles[str(o["id"])] = str(o.get("my_role") or "")
    except Exception as e:
        print(f"[dashboards] orgs uid={uid} err={e!r}")
    principals = {f"u:{uid}"}
    for oid in org_roles:
        principals |= _org_principals(oid, uid)
    arch = "" if include_archived else " AND d.archived_at = ''"
    with _LOCK:
        conn = _conn()
        ids: set[int] = set()
        q = ",".join("?" * len(principals))
        for r in conn.execute(
            f"SELECT d.id FROM dashboards d JOIN dashboard_members m ON m.dashboard_id = d.id "
            f"WHERE m.principal IN ({q}){arch}",
            tuple(principals),
        ):
            ids.add(int(r[0]))
        for r in conn.execute(f"SELECT id FROM dashboards d WHERE owner_uid = ?{arch}", (uid,)):
            ids.add(int(r[0]))
        admin_orgs = [o for o, role in org_roles.items() if role in ("owner", "admin")]
        if admin_orgs:
            q2 = ",".join("?" * len(admin_orgs))
            for r in conn.execute(f"SELECT id FROM dashboards d WHERE org_id IN ({q2}){arch}", tuple(admin_orgs)):
                ids.add(int(r[0]))
    out = []
    for did in sorted(ids):
        d = get(did)
        if not d:
            continue
        role = role_of(uid, d)
        if role:
            d["my_role"] = role
            out.append(d)
    out.sort(key=lambda d: str(d.get("updated_at") or ""), reverse=True)
    return out


def _clean_principal(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    if p == "all":
        return p
    if p.startswith(("u:", "g:")) and p[2:].isdigit() and int(p[2:]) > 0:
        return f"{p[:2]}{int(p[2:])}"
    raise DashboardError("Не понял, кому дать доступ")


def principal_labels(org_id: Any, principals: Iterable[str]) -> dict[str, str]:
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
    if groups and str(org_id or "").isdigit():
        try:
            from assistant.stores import orgs_store

            with orgs_store._LOCK:  # type: ignore[attr-defined]
                q = ",".join("?" * len(groups))
                for r in orgs_store._conn().execute(  # type: ignore[attr-defined]
                    f"SELECT id, name FROM org_groups WHERE org_id = ? AND id IN ({q})", (int(org_id), *groups)
                ):
                    out[f"g:{int(r['id'])}"] = str(r["name"] or "Отдел")
        except Exception as e:
            print(f"[dashboards] group labels org={org_id} err={e!r}")
    for g in groups:
        out.setdefault(f"g:{g}", "Отдел или команда")
    return out


# ---------- дашборды ----------


def create(user_id: Any, title: Any, *, org_id: Any = "", description: Any = "") -> dict[str, Any]:
    uid = _uid(user_id)
    clean = _short(title, TITLE_MAX)
    if not clean:
        raise DashboardError("Введите название дашборда")
    org = str(org_id or "").strip()
    if org:
        if not org.isdigit() or not _org_role(org, uid):
            raise DashboardError("Вы не состоите в этой организации")
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO dashboards (org_id, owner_uid, title, description, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (org, uid, clean, str(description or "").strip()[:DESCRIPTION_MAX], now, now),
        )
        did = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO dashboard_members (dashboard_id, principal, role, added_by, added_at) VALUES (?, ?, 'owner', ?, ?)",
            (did, f"u:{uid}", uid, now),
        )
        conn.commit()
    return require(uid, did)


def _touch(conn: sqlite3.Connection, dash_id: int) -> None:
    conn.execute("UPDATE dashboards SET updated_at = ? WHERE id = ?", (_now(), int(dash_id)))


def update(user_id: Any, dash_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    d = require(user_id, dash_id, "editor")
    sets: dict[str, Any] = {}
    if "title" in patch:
        t = _short(patch.get("title"), TITLE_MAX)
        if not t:
            raise DashboardError("Введите название дашборда")
        sets["title"] = t
    if "description" in patch:
        sets["description"] = str(patch.get("description") or "").strip()[:DESCRIPTION_MAX]
    if "archived" in patch:
        if not at_least(d["my_role"], "owner"):
            raise DashboardError("Убрать в архив может только владелец дашборда")
        sets["archived_at"] = _now() if patch.get("archived") else ""
    if sets:
        with _LOCK:
            conn = _conn()
            cols = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE dashboards SET {cols}, updated_at = ? WHERE id = ?", (*sets.values(), _now(), d["id"]))
            conn.commit()
        changed(d["id"])
    return require(user_id, d["id"])


def delete(user_id: Any, dash_id: Any) -> bool:
    """Удалить дашборд со всеми данными — только человек-владелец (не MCP)."""
    d = require(user_id, dash_id, "owner")
    did = d["id"]
    with _LOCK:
        conn = _conn()
        ds_ids = [int(r[0]) for r in conn.execute("SELECT id FROM dashboard_datasets WHERE dashboard_id = ?", (did,))]
        for t in ("dashboard_points", "dashboard_rows", "dashboard_events"):
            conn.executemany(f"DELETE FROM {t} WHERE dataset_id = ?", [(i,) for i in ds_ids])
        for t in ("dashboard_datasets", "dashboard_widgets", "dashboard_members"):
            conn.execute(f"DELETE FROM {t} WHERE dashboard_id = ?", (did,))
        conn.execute("DELETE FROM dashboards WHERE id = ?", (did,))
        conn.commit()
    return True


def set_member(user_id: Any, dash_id: Any, principal: Any, role: str) -> dict[str, Any]:
    d = require(user_id, dash_id, "owner")
    pr = _clean_principal(principal)
    if role not in ("viewer", "editor"):
        raise DashboardError("Роль: смотрит или редактирует")
    org = d["org_id"]
    if pr == "all" or pr.startswith("g:"):
        if not org:
            raise DashboardError("Отделы и вся организация — только у дашборда организации")
    elif org and not _org_role(org, pr[2:]):
        raise DashboardError("Этот человек не состоит в организации дашборда")
    if pr == f"u:{d['owner_uid']}":
        raise DashboardError("Создатель дашборда и так владелец")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO dashboard_members (dashboard_id, principal, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dashboard_id, principal) DO UPDATE SET role = excluded.role",
            (d["id"], pr, role, _uid(user_id), _now()),
        )
        conn.commit()
    changed(d["id"])
    return require(user_id, d["id"])


def remove_member(user_id: Any, dash_id: Any, principal: Any) -> dict[str, Any]:
    d = require(user_id, dash_id, "owner")
    pr = _clean_principal(principal)
    if pr == f"u:{d['owner_uid']}":
        raise DashboardError("Создателя дашборда убрать нельзя")
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM dashboard_members WHERE dashboard_id = ? AND principal = ?", (d["id"], pr))
        conn.commit()
    return require(user_id, d["id"])


def viewer_ids(dash: dict[str, Any]) -> list[str]:
    """Кому слать «дашборд изменился» (люди с доступом)."""
    uids: set[str] = {str(dash["owner_uid"])}
    org = str(dash.get("org_id") or "")
    if org.isdigit():
        try:
            from assistant.stores import orgs_store

            for u in orgs_store.member_ids(int(org)):
                if role_of(u, dash):
                    uids.add(str(u))
        except Exception as e:
            print(f"[dashboards] viewers org={org} err={e!r}")
    else:
        for m in dash.get("members") or []:
            if str(m["principal"]).startswith("u:"):
                uids.add(str(m["principal"])[2:])
    return sorted(uids)


_NOTIFY_LAST: dict[int, float] = {}
_NOTIFY_LOCK = threading.Lock()
NOTIFY_EVERY_SEC = 5.0


def changed(dash_id: int, *, force: bool = True) -> None:
    """Сообщить открытым вкладкам (WebSocket), что дашборд изменился. Из приёма — не чаще раза в 5 с."""
    now = time.monotonic()
    with _NOTIFY_LOCK:
        if not force and now - _NOTIFY_LAST.get(int(dash_id), 0.0) < NOTIFY_EVERY_SEC:
            return
        _NOTIFY_LAST[int(dash_id)] = now
    try:
        from assistant.realtime import notify
        from assistant.realtime.hub import user_topic

        d = get(dash_id)
        if not d:
            return
        ev = {"type": "dashboard.changed", "id": int(dash_id)}
        for u in viewer_ids(d):
            notify.publish(user_topic(u), ev)
    except Exception as e:
        print(f"[dashboards] notify dash={dash_id} err={e!r}")


# ---------- источники ----------


def _new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def datasets(dash_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM dashboard_datasets WHERE dashboard_id = ? ORDER BY id", (int(dash_id),)
        ).fetchall()
    return [_ds_row(r) for r in rows]


def dataset(dataset_id: Any) -> Optional[dict[str, Any]]:
    try:
        i = int(dataset_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        r = _conn().execute("SELECT * FROM dashboard_datasets WHERE id = ?", (i,)).fetchone()
    return _ds_row(r) if r else None


def dataset_by_key(dash_id: int, key: Any) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM dashboard_datasets WHERE dashboard_id = ? AND key = ?", (int(dash_id), str(key or ""))
        ).fetchone()
    return _ds_row(r) if r else None


def _ds_of(dash: dict[str, Any], key_or_id: Any) -> dict[str, Any]:
    ds = None
    if isinstance(key_or_id, int) or (isinstance(key_or_id, str) and key_or_id.isdigit()):
        ds = dataset(key_or_id)
        if ds and ds["dashboard_id"] != dash["id"]:
            ds = None
    if not ds:
        ds = dataset_by_key(dash["id"], key_or_id)
    if not ds:
        raise DashboardError(f"В дашборде нет источника «{key_or_id}»")
    return ds


def create_dataset(
    user_id: Any,
    dash_id: Any,
    key: Any,
    kind: Any,
    *,
    title: Any = "",
    unit: Any = "",
    columns: Any = None,
    expected_every_min: Any = 0,
) -> dict[str, Any]:
    """Новый источник. В ответе ``ingest_key`` — единственный раз, когда ключ виден целиком."""
    d = require(user_id, dash_id, "editor")
    k = str(key or "").strip().lower()
    if not _KEY_RE.match(k):
        raise DashboardError("key: латиница, цифры и _, с буквы, до 40 символов (например revenue_daily)")
    kd = str(kind or "").strip().lower()
    if kd not in W.DATASET_KINDS:
        raise DashboardError(f"kind: одно из {', '.join(W.DATASET_KINDS)}")
    schema: dict[str, Any] = {}
    if kd == "table":
        try:
            cols = W.clean_columns(columns)
        except W.WidgetError as e:
            raise DashboardError(str(e))
        if cols:
            schema["columns"] = cols
    try:
        every = max(0, min(60 * 24 * 31, int(expected_every_min or 0)))
    except (TypeError, ValueError):
        every = 0
    raw = _new_key()
    with _LOCK:
        conn = _conn()
        n = conn.execute("SELECT COUNT(*) FROM dashboard_datasets WHERE dashboard_id = ?", (d["id"],)).fetchone()[0]
        if n >= MAX_DATASETS:
            raise DashboardError(f"Не больше {MAX_DATASETS} источников в дашборде")
        if conn.execute(
            "SELECT 1 FROM dashboard_datasets WHERE dashboard_id = ? AND key = ?", (d["id"], k)
        ).fetchone():
            raise DashboardError(f"Источник «{k}» уже есть — используйте его или выберите другой key")
        cur = conn.execute(
            "INSERT INTO dashboard_datasets (dashboard_id, key, kind, title, unit, schema_json, expected_every_min, "
            "ingest_hash, ingest_hint, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d["id"], k, kd, _short(title, 60) or k, _short(unit, 12), json.dumps(schema, ensure_ascii=False),
                every, _hash(raw), raw[: len(KEY_PREFIX) + 4] + "…", _uid(user_id), _now(),
            ),
        )
        _touch(conn, d["id"])
        conn.commit()
        ds = _ds_row(conn.execute("SELECT * FROM dashboard_datasets WHERE id = ?", (cur.lastrowid,)).fetchone())
    return {**ds, "ingest_key": raw}


def update_dataset(user_id: Any, dash_id: Any, key_or_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    d = require(user_id, dash_id, "editor")
    ds = _ds_of(d, key_or_id)
    sets: dict[str, Any] = {}
    if "title" in patch:
        sets["title"] = _short(patch.get("title"), 60) or ds["key"]
    if "unit" in patch:
        sets["unit"] = _short(patch.get("unit"), 12)
    if "expected_every_min" in patch:
        try:
            sets["expected_every_min"] = max(0, min(60 * 24 * 31, int(patch.get("expected_every_min") or 0)))
        except (TypeError, ValueError):
            raise DashboardError("expected_every_min: минуты, целое число")
    if "columns" in patch and ds["kind"] == "table":
        try:
            cols = W.clean_columns(patch.get("columns"))
        except W.WidgetError as e:
            raise DashboardError(str(e))
        sets["schema_json"] = json.dumps({**ds["schema"], "columns": cols}, ensure_ascii=False)
    if sets:
        with _LOCK:
            conn = _conn()
            cols_sql = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(f"UPDATE dashboard_datasets SET {cols_sql} WHERE id = ?", (*sets.values(), ds["id"]))
            _touch(conn, d["id"])
            conn.commit()
        changed(d["id"])
    return dataset(ds["id"]) or ds


def rotate_key(user_id: Any, dash_id: Any, key_or_id: Any) -> dict[str, Any]:
    d = require(user_id, dash_id, "editor")
    ds = _ds_of(d, key_or_id)
    raw = _new_key()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE dashboard_datasets SET ingest_hash = ?, ingest_hint = ? WHERE id = ?",
            (_hash(raw), raw[: len(KEY_PREFIX) + 4] + "…", ds["id"]),
        )
        conn.commit()
    return {**(dataset(ds["id"]) or ds), "ingest_key": raw}


def delete_dataset(user_id: Any, dash_id: Any, key_or_id: Any) -> bool:
    """Удалить источник с данными и его виджетами — только человек-владелец (не MCP)."""
    d = require(user_id, dash_id, "owner")
    ds = _ds_of(d, key_or_id)
    with _LOCK:
        conn = _conn()
        for t in ("dashboard_points", "dashboard_rows", "dashboard_events"):
            conn.execute(f"DELETE FROM {t} WHERE dataset_id = ?", (ds["id"],))
        conn.execute("DELETE FROM dashboard_widgets WHERE dataset_id = ?", (ds["id"],))
        conn.execute("DELETE FROM dashboard_datasets WHERE id = ?", (ds["id"],))
        _touch(conn, d["id"])
        conn.commit()
    changed(d["id"])
    return True


# ---------- виджеты ----------


def widgets(dash_id: int) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM dashboard_widgets WHERE dashboard_id = ? ORDER BY position, id", (int(dash_id),)
        ).fetchall()
    return [_w_row(r) for r in rows]


def _validate_widget(d: dict[str, Any], kind: str, options: Any, ds_ref: Any) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    spec = W.WIDGET_TYPES.get(str(kind or "").strip().lower())
    ds = _ds_of(d, ds_ref) if (ds_ref not in (None, "") and spec and spec["sources"]) else None
    by_key = {x["key"]: x for x in datasets(d["id"])}
    try:
        opts = W.validate(kind, options, dataset=ds, datasets_by_key=by_key)
    except W.WidgetError as e:
        raise DashboardError(str(e))
    return ds, opts


def add_widget(
    user_id: Any,
    dash_id: Any,
    kind: Any,
    *,
    title: Any = "",
    dataset: Any = None,
    options: Any = None,
    width: Any = None,
    position: Any = None,
) -> dict[str, Any]:
    d = require(user_id, dash_id, "editor")
    k = str(kind or "").strip().lower()
    ds, opts = _validate_widget(d, k, options, dataset)
    w = W.clamp_width(k, width)
    now = _now()
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            "SELECT id FROM dashboard_widgets WHERE dashboard_id = ? ORDER BY position, id", (d["id"],)
        ).fetchall()
        if len(rows) >= MAX_WIDGETS:
            raise DashboardError(f"Не больше {MAX_WIDGETS} виджетов в дашборде")
        cur = conn.execute(
            "INSERT INTO dashboard_widgets (dashboard_id, kind, title, dataset_id, options_json, position, width, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (d["id"], k, _short(title, 60), ds["id"] if ds else None, json.dumps(opts, ensure_ascii=False),
             len(rows), w, now, now),
        )
        wid = int(cur.lastrowid)
        order = [int(r[0]) for r in rows]
        try:
            pos = int(position) if position not in (None, "") else len(order)
        except (TypeError, ValueError):
            pos = len(order)
        order.insert(max(0, min(len(order), pos)), wid)
        conn.executemany("UPDATE dashboard_widgets SET position = ? WHERE id = ?", [(i, x) for i, x in enumerate(order)])
        _touch(conn, d["id"])
        conn.commit()
        r = conn.execute("SELECT * FROM dashboard_widgets WHERE id = ?", (wid,)).fetchone()
    changed(d["id"])
    return _w_row(r)


def _widget_of(d: dict[str, Any], widget_id: Any) -> dict[str, Any]:
    try:
        wid = int(widget_id)
    except (TypeError, ValueError):
        raise DashboardError("Виджет не найден")
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM dashboard_widgets WHERE id = ? AND dashboard_id = ?", (wid, d["id"])
        ).fetchone()
    if not r:
        raise DashboardError("Виджет не найден")
    return _w_row(r)


def update_widget(user_id: Any, dash_id: Any, widget_id: Any, patch: dict[str, Any]) -> dict[str, Any]:
    d = require(user_id, dash_id, "editor")
    w = _widget_of(d, widget_id)
    kind = str(patch.get("kind") or w["kind"]).strip().lower()
    ds_ref: Any = patch.get("dataset") if "dataset" in patch else w["dataset_id"]
    if "options" in patch:
        options = patch.get("options")
        if isinstance(options, dict) and kind == w["kind"] and not patch.get("replace_options"):
            options = {**w["options"], **options}
    else:
        options = w["options"] if kind == w["kind"] else {}
    ds, opts = _validate_widget(d, kind, options, ds_ref)
    sets = {
        "kind": kind,
        "dataset_id": ds["id"] if ds else None,
        "options_json": json.dumps(opts, ensure_ascii=False),
        "width": W.clamp_width(kind, patch.get("width", w["width"])),
        "updated_at": _now(),
    }
    if "title" in patch:
        sets["title"] = _short(patch.get("title"), 60)
    with _LOCK:
        conn = _conn()
        cols = ", ".join(f"{k} = ?" for k in sets)
        conn.execute(f"UPDATE dashboard_widgets SET {cols} WHERE id = ?", (*sets.values(), w["id"]))
        _touch(conn, d["id"])
        conn.commit()
    changed(d["id"])
    return _widget_of(d, w["id"])


def move_widget(user_id: Any, dash_id: Any, widget_id: Any, position: Any) -> list[dict[str, Any]]:
    d = require(user_id, dash_id, "editor")
    w = _widget_of(d, widget_id)
    with _LOCK:
        conn = _conn()
        order = [int(r[0]) for r in conn.execute(
            "SELECT id FROM dashboard_widgets WHERE dashboard_id = ? ORDER BY position, id", (d["id"],)
        )]
        order.remove(w["id"])
        try:
            pos = int(position)
        except (TypeError, ValueError):
            raise DashboardError("position: номер места с 0")
        order.insert(max(0, min(len(order), pos)), w["id"])
        conn.executemany("UPDATE dashboard_widgets SET position = ? WHERE id = ?", [(i, x) for i, x in enumerate(order)])
        _touch(conn, d["id"])
        conn.commit()
    changed(d["id"])
    return widgets(d["id"])


def reorder_widgets(user_id: Any, dash_id: Any, ids: list[Any]) -> list[dict[str, Any]]:
    d = require(user_id, dash_id, "editor")
    current = [w["id"] for w in widgets(d["id"])]
    want = []
    for x in ids or []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in current and i not in want:
            want.append(i)
    want += [i for i in current if i not in want]
    with _LOCK:
        conn = _conn()
        conn.executemany("UPDATE dashboard_widgets SET position = ? WHERE id = ?", [(i, x) for i, x in enumerate(want)])
        _touch(conn, d["id"])
        conn.commit()
    changed(d["id"])
    return widgets(d["id"])


def remove_widget(user_id: Any, dash_id: Any, widget_id: Any) -> bool:
    d = require(user_id, dash_id, "editor")
    w = _widget_of(d, widget_id)
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM dashboard_widgets WHERE id = ?", (w["id"],))
        _touch(conn, d["id"])
        conn.commit()
    changed(d["id"])
    return True


# ---------- приём данных ----------

_RATE: dict[int, deque] = {}
_RATE_LOCK = threading.Lock()


def resolve_ingest(dataset_id: Any, raw_key: str) -> Optional[dict[str, Any]]:
    """Ключ источника → источник (или None: неверный ключ / нет источника / дашборд в архиве)."""
    raw = str(raw_key or "").strip()
    if not raw.startswith(KEY_PREFIX) or len(raw) > 120:
        return None
    ds = dataset(dataset_id)
    if not ds:
        return None
    with _LOCK:
        r = _conn().execute(
            "SELECT s.ingest_hash, d.archived_at FROM dashboard_datasets s JOIN dashboards d ON d.id = s.dashboard_id "
            "WHERE s.id = ?",
            (ds["id"],),
        ).fetchone()
    if not r or not secrets.compare_digest(str(r["ingest_hash"]), _hash(raw)):
        return None
    if r["archived_at"]:
        return None
    return ds


def rate_ok(dataset_id: int) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        q = _RATE.setdefault(int(dataset_id), deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_PER_MIN:
            return False
        q.append(now)
        return True


def record_error(dataset_id: int, msg: str) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE dashboard_datasets SET last_error = ?, last_error_at = ? WHERE id = ?",
            (str(msg)[:300], _now(), int(dataset_id)),
        )
        conn.commit()


def parse_ts(raw: Any, *, now: Optional[datetime] = None) -> tuple[str, int]:
    """Время точки → (UTC 'YYYY-MM-DDTHH:MM:SS', all_day). Дата без времени — весь день (all_day=1)."""
    if raw in (None, ""):
        t = (now or datetime.now(timezone.utc)).replace(microsecond=0)
        return t.strftime("%Y-%m-%dT%H:%M:%S"), 0
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        v = float(raw)
        if v > 1e11:  # миллисекунды
            v /= 1000.0
        t = datetime.fromtimestamp(v, tz=timezone.utc)
        return t.strftime("%Y-%m-%dT%H:%M:%S"), 0
    s = str(raw).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            date.fromisoformat(s)
        except ValueError:
            raise ValueError(f"«{s}» — не дата")
        return s + "T00:00:00", 1
    if re.fullmatch(r"\d{4}-\d{2}", s):
        return s + "-01T00:00:00", 1
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        raise ValueError(f"«{s[:40]}» — не дата и не время (нужно 2026-11-13 или 2026-11-13T10:00:00+03:00)")
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"), 0


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _clean_cell(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, (list, dict)):
        return _short(json.dumps(v, ensure_ascii=False), 300)
    return str(v)[:500]


def ingest(ds: dict[str, Any], payload: Any) -> dict[str, Any]:
    """Записать присланное в источник. ValueError — понятная ошибка для отправителя."""
    if not isinstance(payload, dict):
        raise ValueError("Тело — JSON-объект")
    kind = ds["kind"]
    if kind == "metric":
        res = _ingest_points(ds, payload)
    elif kind == "table":
        res = _ingest_rows(ds, payload)
    else:
        res = _ingest_events(ds, payload)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE dashboard_datasets SET last_push_at = ?, last_push_count = ?, last_error = '', last_error_at = '' "
            "WHERE id = ?",
            (_now(), int(res.get("accepted") or 0), ds["id"]),
        )
        conn.commit()
    changed(ds["dashboard_id"], force=False)
    return res


def _ingest_points(ds: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    pts = payload.get("points")
    if pts is None and "value" in payload:
        pts = [{k: payload.get(k) for k in ("ts", "value", "series") if k in payload}]
    if not isinstance(pts, list) or not pts:
        raise ValueError('Для метрики пришлите {"points": [{"ts": "2026-11-13", "value": 123}]} или {"value": 123}')
    if len(pts) > MAX_POINTS_PER_PUSH:
        raise ValueError(f"Не больше {MAX_POINTS_PER_PUSH} точек за раз — разбейте на части")
    rows: dict[tuple[str, str], tuple[float, int]] = {}
    now = datetime.now(timezone.utc)
    for i, p in enumerate(pts):
        if not isinstance(p, dict):
            raise ValueError(f"points[{i}]: объект {{ts, value, series?}}")
        v = _num(p.get("value"))
        if v is None:
            raise ValueError(f"points[{i}].value: нужно число, пришло {str(p.get('value'))[:30]!r}")
        try:
            ts, all_day = parse_ts(p.get("ts"), now=now)
        except ValueError as e:
            raise ValueError(f"points[{i}].ts: {e}")
        series = _short(p.get("series"), 60)
        rows[(ts, series)] = (v, all_day)
    with _LOCK:
        conn = _conn()
        before = conn.total_changes
        conn.executemany(
            "INSERT INTO dashboard_points (dataset_id, ts, series, value, all_day) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dataset_id, ts, series) DO UPDATE SET value = excluded.value, all_day = excluded.all_day",
            [(ds["id"], ts, s, v, ad) for (ts, s), (v, ad) in rows.items()],
        )
        cutoff = (now - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
        conn.execute("DELETE FROM dashboard_points WHERE dataset_id = ? AND ts < ?", (ds["id"], cutoff))
        conn.commit()
        _ = conn.total_changes - before
    return {"ok": True, "accepted": len(rows), "kind": "metric"}


def _ingest_rows(ds: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError('Для таблицы пришлите {"rows": [{...}], "mode": "replace"}')
    mode = str(payload.get("mode") or "replace").strip().lower()
    if mode not in ("replace", "upsert"):
        raise ValueError("mode: replace (вся таблица целиком) или upsert (обновить строки по key)")
    key = str(payload.get("key") or "").strip()
    if mode == "upsert" and not key:
        raise ValueError('Для mode=upsert укажите "key" — колонку, по которой узнаём строку')
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Не больше {MAX_ROWS} строк")
    cols = W.table_columns(ds.get("schema"))
    clean: list[tuple[str, int, str]] = []
    seen: set[str] = set()
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValueError(f"rows[{i}]: объект {{колонка: значение}}")
        data = {}
        for k, v in list(r.items())[:60]:
            if not isinstance(k, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", k):
                raise ValueError(f"rows[{i}]: имя колонки «{str(k)[:40]}» — латиница, цифры и _")
            data[k] = _clean_cell(v)
        if cols is not None:
            unknown = [k for k in data if k not in cols]
            if unknown:
                raise ValueError(f"rows[{i}]: колонок {', '.join(unknown[:5])} нет в источнике (есть: {', '.join(sorted(cols))})")
        if key:
            if data.get(key) in (None, ""):
                raise ValueError(f"rows[{i}]: нет значения в колонке key «{key}»")
            rk = str(data[key])[:200]
        else:
            rk = f"#{i}"
        if rk in seen:
            continue
        seen.add(rk)
        clean.append((rk, i, json.dumps(data, ensure_ascii=False)))
    now = _now()
    with _LOCK:
        conn = _conn()
        if mode == "replace":
            conn.execute("DELETE FROM dashboard_rows WHERE dataset_id = ?", (ds["id"],))
        else:
            have = conn.execute("SELECT COUNT(*) FROM dashboard_rows WHERE dataset_id = ?", (ds["id"],)).fetchone()[0]
            new = sum(
                1 for rk, _p, _d in clean
                if not conn.execute(
                    "SELECT 1 FROM dashboard_rows WHERE dataset_id = ? AND row_key = ?", (ds["id"], rk)
                ).fetchone()
            )
            if have + new > MAX_ROWS:
                raise ValueError(f"В таблице будет больше {MAX_ROWS} строк — пришлите mode=replace")
        base = 0
        if mode == "upsert":
            # строки, присланные раньше без key (replace), узнаём по значению колонки key
            conn.executemany(
                "DELETE FROM dashboard_rows WHERE dataset_id = ? AND row_key != ? "
                "AND CAST(json_extract(data_json, ?) AS TEXT) = ?",
                [(ds["id"], rk, f"$.{key}", rk) for rk, _p, _d in clean],
            )
            base = int(conn.execute(
                "SELECT COALESCE(MAX(pos), -1) + 1 FROM dashboard_rows WHERE dataset_id = ?", (ds["id"],)
            ).fetchone()[0])
        conn.executemany(
            "INSERT INTO dashboard_rows (dataset_id, row_key, pos, data_json, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dataset_id, row_key) DO UPDATE SET data_json = excluded.data_json, updated_at = excluded.updated_at",
            [(ds["id"], rk, base + p, dj, now) for rk, p, dj in clean],
        )
        conn.commit()
    return {"ok": True, "accepted": len(clean), "kind": "table", "mode": mode}


def _ingest_events(ds: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    evs = payload.get("events")
    if evs is None and "text" in payload:
        evs = [{k: payload.get(k) for k in ("ts", "text", "url", "id") if k in payload}]
    if not isinstance(evs, list) or not evs:
        raise ValueError('Для ленты пришлите {"events": [{"ts": "…", "text": "…", "url": "…", "id": "…"}]}')
    if len(evs) > 500:
        raise ValueError("Не больше 500 событий за раз")
    out = []
    now = datetime.now(timezone.utc)
    for i, e in enumerate(evs):
        if not isinstance(e, dict):
            raise ValueError(f"events[{i}]: объект {{ts, text, url?, id?}}")
        text = _short(e.get("text"), 300)
        if not text:
            raise ValueError(f"events[{i}].text: пустой текст")
        try:
            ts, _ad = parse_ts(e.get("ts"), now=now)
        except ValueError as err:
            raise ValueError(f"events[{i}].ts: {err}")
        url = str(e.get("url") or "").strip()[:500]
        if url and not re.match(r"^https?://", url):
            raise ValueError(f"events[{i}].url: только http(s)://")
        out.append((ds["id"], ts, text, url, _short(e.get("id"), 100)))
    with _LOCK:
        conn = _conn()
        for row in out:
            if row[4]:
                conn.execute(
                    "INSERT INTO dashboard_events (dataset_id, ts, text, url, ext_id) VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(dataset_id, ext_id) WHERE ext_id != '' DO UPDATE SET ts = excluded.ts, "
                    "text = excluded.text, url = excluded.url",
                    row,
                )
            else:
                conn.execute("INSERT INTO dashboard_events (dataset_id, ts, text, url, ext_id) VALUES (?, ?, ?, ?, ?)", row)
        conn.execute(
            "DELETE FROM dashboard_events WHERE dataset_id = ? AND id NOT IN ("
            "SELECT id FROM dashboard_events WHERE dataset_id = ? ORDER BY ts DESC, id DESC LIMIT ?)",
            (ds["id"], ds["id"], MAX_EVENTS_KEEP),
        )
        conn.commit()
    return {"ok": True, "accepted": len(out), "kind": "events"}


# ---------- чтение данных ----------


def points(dataset_id: int, since: str = "", until: str = "", series: Optional[list[str]] = None) -> list[dict[str, Any]]:
    q = "SELECT ts, series, value, all_day FROM dashboard_points WHERE dataset_id = ?"
    args: list[Any] = [int(dataset_id)]
    if since:
        q += " AND ts >= ?"
        args.append(since)
    if until:
        q += " AND ts < ?"
        args.append(until)
    if series:
        q += f" AND series IN ({','.join('?' * len(series))})"
        args += list(series)
    q += " ORDER BY ts"
    with _LOCK:
        rows = _conn().execute(q, args).fetchall()
    return [{"ts": r["ts"], "series": r["series"], "value": float(r["value"]), "all_day": bool(r["all_day"])} for r in rows]


def series_names(dataset_id: int) -> list[str]:
    with _LOCK:
        return [str(r[0]) for r in _conn().execute(
            "SELECT series, SUM(ABS(value)) s FROM dashboard_points WHERE dataset_id = ? GROUP BY series ORDER BY s DESC",
            (int(dataset_id),),
        )]


def rows(dataset_id: int, limit: int = MAX_ROWS) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT data_json FROM dashboard_rows WHERE dataset_id = ? ORDER BY pos, rowid LIMIT ?",
            (int(dataset_id), int(limit)),
        ).fetchall()
    return [_jload(r[0], {}) for r in rs]


def events(dataset_id: int, limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        rs = _conn().execute(
            "SELECT ts, text, url FROM dashboard_events WHERE dataset_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (int(dataset_id), int(limit)),
        ).fetchall()
    return [{"ts": r["ts"], "text": r["text"], "url": r["url"]} for r in rs]


def counts(dataset_id: int, kind: str) -> int:
    table = {"metric": "dashboard_points", "table": "dashboard_rows", "events": "dashboard_events"}.get(kind)
    if not table:
        return 0
    with _LOCK:
        return int(_conn().execute(f"SELECT COUNT(*) FROM {table} WHERE dataset_id = ?", (int(dataset_id),)).fetchone()[0])
