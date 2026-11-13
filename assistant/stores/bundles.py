"""Связки: набор разнотипных элементов (документы, саммари встреч, задачи, чаты, люди…).

Связка без названия показывается у элемента как «Связанное»; с названием — именованная
связка («Запуск тарифа Pro»). Один элемент может входить в несколько связок.

Хранилище не проверяет доступ: кто что может видеть, решает assistant/services/bundle_access.py.
Здесь только состав связок. Ключ элемента — (item_kind, item_id) как у чипов «+».
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from assistant.lib import sqlite_conn
from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

KINDS = (
    "note", "task", "chat", "reminder", "person", "event", "journal",
    # с 2026-11 (проекты): серия встреч, тема встреч, внешний Telegram-чат (или его топик),
    # переписка (ветка письма), почтовый адрес
    "series", "topic", "tgchat", "mail", "mailaddr",
)
MAX_ITEMS = 60  # у «Связанного»; у проекта предела нет
MAX_TITLE = 120

# kind связки: related — безымянное «Связанное»; project — проект (название, участники, статус).
BUNDLE_RELATED = "related"
BUNDLE_PROJECT = "project"

_PROJECT_COLS = (
    ("kind", "TEXT NOT NULL DEFAULT 'related'"),
    ("lead_user_id", "TEXT NOT NULL DEFAULT ''"),
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("description", "TEXT NOT NULL DEFAULT ''"),
    ("color", "TEXT NOT NULL DEFAULT ''"),
    ("org_id", "TEXT NOT NULL DEFAULT ''"),
    ("archived_at", "TEXT NOT NULL DEFAULT ''"),
)


@sqlite_conn.schema_once(lambda: notes_store._conn())  # type: ignore[attr-defined]
def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bundles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bundle_items (
            bundle_id INTEGER NOT NULL,
            item_kind TEXT NOT NULL,
            item_id TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            added_by TEXT NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY (bundle_id, item_kind, item_id)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bundle_items_item ON bundle_items(item_kind, item_id)")
    # Участники проекта: principal — u:<id> / g:<id отдела или команды> / all (вся организация org_id).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bundle_members (
            bundle_id INTEGER NOT NULL,
            principal TEXT NOT NULL,
            role TEXT NOT NULL,
            added_by TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            PRIMARY KEY (bundle_id, principal)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bundle_members_principal ON bundle_members(principal)")
    have = {str(r[1]) for r in conn.execute("PRAGMA table_info(bundles)")}
    added_kind = "kind" not in have
    for col, decl in _PROJECT_COLS:
        if col not in have:
            conn.execute(f"ALTER TABLE bundles ADD COLUMN {col} {decl}")
    if added_kind:
        _promote_named_bundles(conn)
    conn.commit()
    return conn


def _promote_named_bundles(conn: sqlite3.Connection) -> None:
    """Разовый переезд: именованные связки становятся проектами. Участники — автор (владелец)
    и все, кто добавлял элементы (редакторы): раньше они видели связку через свои элементы."""
    now = _now()
    rows = conn.execute("SELECT id, owner_user_id FROM bundles WHERE title != ''").fetchall()
    for r in rows:
        bid, owner = int(r[0]), str(r[1])
        conn.execute("UPDATE bundles SET kind = ? WHERE id = ?", (BUNDLE_PROJECT, bid))
        conn.execute(
            "INSERT OR IGNORE INTO bundle_members(bundle_id, principal, role, added_by, added_at) VALUES (?, ?, 'owner', ?, ?)",
            (bid, f"u:{owner}", owner, now),
        )
        for (adder,) in conn.execute("SELECT DISTINCT added_by FROM bundle_items WHERE bundle_id = ?", (bid,)).fetchall():
            if str(adder) and str(adder) != owner:
                conn.execute(
                    "INSERT OR IGNORE INTO bundle_members(bundle_id, principal, role, added_by, added_at) "
                    "VALUES (?, ?, 'editor', ?, ?)",
                    (bid, f"u:{adder}", owner, now),
                )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: Any) -> str:
    return str(int(user_id))


def norm_item(kind: Any, item_id: Any) -> tuple[str, str]:
    """(вид, id) в каноническом виде или ValueError."""
    k = str(kind or "").strip().lower()
    rid = str(item_id if item_id is not None else "").strip()[:200]
    if k not in KINDS:
        raise ValueError("Такой элемент нельзя связать")
    if not rid or any(ch in rid for ch in "\"'<>\n"):
        raise ValueError("Нет id элемента")
    if k in ("note", "task", "chat", "journal", "topic"):
        try:
            n = int(rid)
        except ValueError:
            raise ValueError("Нет id элемента") from None
        if n <= 0:
            raise ValueError("Нет id элемента")
        rid = str(n)
    elif k in ("person", "mailaddr"):
        rid = rid.lower()
        if k == "mailaddr" and ("@" not in rid or " " in rid):
            raise ValueError("Нужен почтовый адрес")
    elif k == "tgchat":
        # "<chat_id>" или "<chat_id>:<thread_id>" (топик форума)
        parts = rid.split(":")
        try:
            nums = [int(x) for x in parts]
        except ValueError:
            raise ValueError("Нет id чата") from None
        if len(nums) > 2 or nums[0] == 0 or (len(nums) == 2 and nums[1] < 0):
            raise ValueError("Нет id чата")
        rid = ":".join(str(n) for n in nums)
    return k, rid


def _bundle_row(conn: sqlite3.Connection, bundle_id: int) -> Optional[dict[str, Any]]:
    r = conn.execute("SELECT * FROM bundles WHERE id = ?", (int(bundle_id),)).fetchone()
    return dict(r) if r else None


def _items(conn: sqlite3.Connection, bundle_id: int) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT item_kind, item_id, label, added_by, added_at FROM bundle_items "
            "WHERE bundle_id = ? ORDER BY added_at, rowid",
            (int(bundle_id),),
        )
    ]


def get(bundle_id: Any) -> Optional[dict[str, Any]]:
    """Связка со всеми элементами (без фильтра доступа)."""
    try:
        bid = int(bundle_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        conn = _conn()
        b = _bundle_row(conn, bid)
        if not b:
            return None
        b["items"] = _items(conn, bid)
    return b


def bundles_for_item(kind: Any, item_id: Any) -> list[dict[str, Any]]:
    """Все связки, куда входит элемент (без фильтра доступа), с элементами."""
    k, rid = norm_item(kind, item_id)
    with _LOCK:
        conn = _conn()
        ids = [
            int(r[0])
            for r in conn.execute(
                "SELECT bundle_id FROM bundle_items WHERE item_kind = ? AND item_id = ?", (k, rid)
            )
        ]
        out = []
        for bid in ids:
            b = _bundle_row(conn, bid)
            if b:
                b["items"] = _items(conn, bid)
                out.append(b)
    # безымянные («Связанное») — первыми, потом именованные; внутри — свежие выше
    named = sorted([b for b in out if is_project(b)], key=lambda b: b["updated_at"], reverse=True)
    plain = sorted([b for b in out if not is_project(b)], key=lambda b: b["updated_at"], reverse=True)
    return plain + named


def _insert_items(
    conn: sqlite3.Connection, bundle_id: int, uid: str, items: Iterable[tuple[str, str, str]]
) -> int:
    have = conn.execute("SELECT COUNT(*) FROM bundle_items WHERE bundle_id = ?", (bundle_id,)).fetchone()[0]
    row = conn.execute("SELECT kind FROM bundles WHERE id = ?", (bundle_id,)).fetchone()
    limited = not row or str(row[0]) != BUNDLE_PROJECT
    added = 0
    now = _now()
    for k, rid, label in items:
        if limited and have + added >= MAX_ITEMS:
            raise ValueError(f"В связке не больше {MAX_ITEMS} элементов")
        cur = conn.execute(
            "INSERT OR IGNORE INTO bundle_items(bundle_id, item_kind, item_id, label, added_by, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (bundle_id, k, rid, str(label or "")[:200], uid, now),
        )
        added += cur.rowcount or 0
    conn.execute("UPDATE bundles SET updated_at = ? WHERE id = ?", (now, bundle_id))
    return added


def _clean_title(title: Any) -> str:
    return " ".join(str(title or "").split())[:MAX_TITLE]


def is_project(bundle: Optional[dict[str, Any]]) -> bool:
    return bool(bundle) and str((bundle or {}).get("kind") or "") == BUNDLE_PROJECT


def _add_owner(conn: sqlite3.Connection, bundle_id: int, uid: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO bundle_members(bundle_id, principal, role, added_by, added_at) VALUES (?, ?, 'owner', ?, ?)",
        (int(bundle_id), f"u:{uid}", uid, _now()),
    )


def create(user_id: Any, title: Any, items: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Новая связка. items — [(вид, id, подпись-снимок)], уже проверенные на доступ.
    С названием — это проект (автор — владелец, элементов может быть сколько угодно, хоть ноль);
    без названия — «Связанное» (минимум два элемента)."""
    uid = _uid(user_id)
    norm = []
    seen = set()
    for k, rid, label in items:
        key = norm_item(k, rid)
        if key in seen:
            continue
        seen.add(key)
        norm.append((key[0], key[1], label))
    clean = _clean_title(title)
    kind = BUNDLE_PROJECT if clean else BUNDLE_RELATED
    if kind == BUNDLE_RELATED and len(norm) < 2:
        raise ValueError("Выберите хотя бы два элемента")
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO bundles(owner_user_id, title, kind, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (uid, clean, kind, now, now),
        )
        bid = int(cur.lastrowid)
        if kind == BUNDLE_PROJECT:
            _add_owner(conn, bid, uid)
        if norm:
            _insert_items(conn, bid, uid, norm)
        conn.commit()
    return get(bid) or {}


def add_items(user_id: Any, bundle_id: Any, items: list[tuple[str, str, str]]) -> dict[str, Any]:
    uid = _uid(user_id)
    norm = [(*norm_item(k, rid), label) for k, rid, label in items]
    with _LOCK:
        conn = _conn()
        b = _bundle_row(conn, int(bundle_id))
        if not b:
            raise ValueError("Связка не найдена")
        _insert_items(conn, int(bundle_id), uid, norm)
        conn.commit()
    return get(bundle_id) or {}


def link(
    user_id: Any,
    a: tuple[str, str, str],
    b: tuple[str, str, str],
    *,
    visible_bundle_ids: Optional[set[int]] = None,
) -> dict[str, Any]:
    """Быстрая связь двух элементов.

    Если у A (или у B) уже есть безымянная связка, видимая этому человеку, — второй элемент
    добавляется в неё; иначе создаётся новая безымянная связка {A, B}.
    visible_bundle_ids — какие связки человек видит (None — все).
    """
    ka = norm_item(a[0], a[1])
    kb = norm_item(b[0], b[1])
    if ka == kb:
        raise ValueError("Нельзя связать элемент с самим собой")

    def _plain_bundle(k: tuple[str, str]) -> Optional[int]:
        for bd in bundles_for_item(*k):
            if is_project(bd):
                continue
            if visible_bundle_ids is not None and int(bd["id"]) not in visible_bundle_ids:
                continue
            return int(bd["id"])
        return None

    target = _plain_bundle(ka)
    other = (kb[0], kb[1], b[2])
    if target is None:
        target = _plain_bundle(kb)
        other = (ka[0], ka[1], a[2])
    if target is None:
        return create(user_id, "", [(ka[0], ka[1], a[2]), (kb[0], kb[1], b[2])])
    return add_items(user_id, target, [other])


def rename(bundle_id: Any, title: Any, *, user_id: Any = None) -> dict[str, Any]:
    """Переименовать. «Связанное», получив название, становится проектом (назвавший — владелец).
    Проект без названия оставить нельзя."""
    clean = _clean_title(title)
    with _LOCK:
        conn = _conn()
        row = _bundle_row(conn, int(bundle_id))
        if not row:
            raise ValueError("Связка не найдена")
        if is_project(row):
            if not clean:
                raise ValueError("У проекта должно быть название")
            conn.execute(
                "UPDATE bundles SET title = ?, updated_at = ? WHERE id = ?", (clean, _now(), int(bundle_id))
            )
        elif clean:
            uid = _uid(user_id if user_id is not None else row["owner_user_id"])
            conn.execute(
                "UPDATE bundles SET title = ?, kind = ?, updated_at = ? WHERE id = ?",
                (clean, BUNDLE_PROJECT, _now(), int(bundle_id)),
            )
            _add_owner(conn, int(bundle_id), str(row["owner_user_id"]))
            if uid != str(row["owner_user_id"]):
                conn.execute(
                    "INSERT OR IGNORE INTO bundle_members(bundle_id, principal, role, added_by, added_at) "
                    "VALUES (?, ?, 'editor', ?, ?)",
                    (int(bundle_id), f"u:{uid}", uid, _now()),
                )
        conn.commit()
    return get(bundle_id) or {}


def remove_item(bundle_id: Any, kind: Any, item_id: Any) -> Optional[dict[str, Any]]:
    """Убрать элемент. Если в «Связанном» остался один элемент — оно удаляется (возвращает None).
    Проект остаётся и пустым."""
    k, rid = norm_item(kind, item_id)
    bid = int(bundle_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM bundle_items WHERE bundle_id = ? AND item_kind = ? AND item_id = ?", (bid, k, rid)
        )
        left = conn.execute("SELECT COUNT(*) FROM bundle_items WHERE bundle_id = ?", (bid,)).fetchone()[0]
        if left < 2 and not is_project(_bundle_row(conn, bid)):
            conn.execute("DELETE FROM bundle_items WHERE bundle_id = ?", (bid,))
            conn.execute("DELETE FROM bundles WHERE id = ?", (bid,))
            conn.commit()
            return None
        conn.execute("UPDATE bundles SET updated_at = ? WHERE id = ?", (_now(), bid))
        conn.commit()
    return get(bid)


def delete(bundle_id: Any) -> bool:
    bid = int(bundle_id)
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM bundle_items WHERE bundle_id = ?", (bid,))
        conn.execute("DELETE FROM bundle_members WHERE bundle_id = ?", (bid,))
        cur = conn.execute("DELETE FROM bundles WHERE id = ?", (bid,))
        conn.commit()
    return bool(cur.rowcount)


def forget_item(kind: Any, item_id: Any) -> None:
    """Элемент удалён насовсем — убрать его из всех связок (одиночные связки исчезают)."""
    try:
        k, rid = norm_item(kind, item_id)
    except ValueError:
        return
    for bd in bundles_for_item(k, rid):
        remove_item(bd["id"], k, rid)


def candidate_ids_for_user(user_id: Any, *, note_ids: Iterable[Any] = (), named_only: bool = True) -> list[int]:
    """Связки, которые человеку стоит показать в списке: свои, куда он что-то добавлял,
    и те, где есть его доступные документы. Доступ к элементам дальше проверяет bundle_access."""
    uid = _uid(user_id)
    nids = sorted({str(int(n)) for n in note_ids if str(n).strip().isdigit()})
    with _LOCK:
        conn = _conn()
        ids = {
            int(r[0])
            for r in conn.execute(
                "SELECT id FROM bundles WHERE owner_user_id = ? UNION "
                "SELECT bundle_id FROM bundle_items WHERE added_by = ?",
                (uid, uid),
            )
        }
        for i in range(0, len(nids), 500):
            chunk = nids[i : i + 500]
            ids.update(
                int(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT bundle_id FROM bundle_items WHERE item_kind = 'note' AND item_id IN (%s)"
                    % ",".join("?" * len(chunk)),
                    chunk,
                )
            )
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        where = f"id IN ({q})" + (" AND title != ''" if named_only else "")
        return [
            int(r[0])
            for r in conn.execute(f"SELECT id FROM bundles WHERE {where} ORDER BY updated_at DESC", sorted(ids))
        ]
