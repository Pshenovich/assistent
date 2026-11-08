"""Сейфы: пароли и ключи, зашифрованные на устройстве пользователя.

Сервер хранит только шифр и не может его открыть. Схема ключей (всё шифрует браузер, см. webapp/vault.js):

* у человека пара ключей (ECDH P-256). Открытый ключ лежит здесь как есть; закрытый зашифрован его
  «ключом сейфов» (UMK, случайный AES-ключ);
* UMK хранится несколько раз — по одному на каждый способ входа (``vault_unlockers``): passkey (ключ
  выводится из ответа PRF устройства) и код с аварийного листа;
* у сейфа свой ключ (VK). Для каждого, кому открыт сейф, лежит «конверт» — VK, запечатанный открытым
  ключом этого человека (``vault_envelopes``);
* записи сейфа зашифрованы VK (``vault_items``). Название сейфа — открытым текстом (нужно для списка
  и доступа), названия и поля записей — только в шифре.

Конверт новому участнику кладёт браузер того, у кого конверт уже есть: сервер ключа не знает. Поэтому
сервер считает, кому сейф открыт по правам, и отдаёт участникам список «ждут конверт» с открытыми
ключами. Положить конверт можно только тем, кто в этом списке.

Содержимое сейфов никогда не попадает в Лео, поиск, эмбеддинги, аналитику, логи и MCP (правило 17).
Права — «кому → роль» как в базе знаний, но без ``all``: открыть сейф всем в организации нельзя.
Админы организации сейфов автоматически не видят. Личный сейф (``org_id IS NULL``) — только владельцу.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable, Optional

from assistant.stores import org_groups, orgs_store
from assistant.stores.orgs_store import OrgError

_LOCK = orgs_store._LOCK

ROLES = ("viewer", "editor", "owner")
ROLE_RANK = {r: i + 1 for i, r in enumerate(ROLES)}
ROLE_LABEL = {"viewer": "Смотрит", "editor": "Правит", "owner": "Владелец"}
NAME_MAX = 80
BLOB_MAX = 64 * 1024  # шифр одной записи
KEY_MAX = 8 * 1024  # открытый ключ, конверт, обёртка UMK
UNLOCKERS_MAX = 10
LOG_ACTIONS = ("reveal", "copy")
_B64 = re.compile(r"^[A-Za-z0-9_\-+/=]+$")

_SCHEMA_CONN: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _SCHEMA_CONN
    conn = orgs_store._conn()
    if _SCHEMA_CONN is conn:
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vault_user_keys (
            user_id TEXT PRIMARY KEY, public_key TEXT NOT NULL, enc_private TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS vault_unlockers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL,
            credential_id TEXT NOT NULL DEFAULT '', salt TEXT NOT NULL, wrapped_umk TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, last_used_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_vault_unlockers_user ON vault_unlockers(user_id);
        CREATE TABLE IF NOT EXISTS vaults (
            id INTEGER PRIMARY KEY AUTOINCREMENT, org_id INTEGER, name TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_vaults_org ON vaults(org_id);
        CREATE TABLE IF NOT EXISTS vault_acl (
            vault_id INTEGER NOT NULL, principal TEXT NOT NULL, role TEXT NOT NULL,
            PRIMARY KEY (vault_id, principal));
        CREATE TABLE IF NOT EXISTS vault_envelopes (
            vault_id INTEGER NOT NULL, user_id TEXT NOT NULL, envelope TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (vault_id, user_id));
        CREATE TABLE IF NOT EXISTS vault_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vault_id INTEGER NOT NULL, blob TEXT NOT NULL,
            created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_vault_items_vault ON vault_items(vault_id);
        CREATE TABLE IF NOT EXISTS vault_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vault_id INTEGER NOT NULL, item_id INTEGER,
            user_id TEXT NOT NULL, action TEXT NOT NULL, at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_vault_log_item ON vault_log(vault_id, item_id, at);
        CREATE TABLE IF NOT EXISTS vault_departures (
            vault_id INTEGER NOT NULL, user_id TEXT NOT NULL, at TEXT NOT NULL, dismissed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (vault_id, user_id));
        """
    )
    conn.commit()
    _SCHEMA_CONN = conn
    return conn


def _uid(user_id: int | str) -> str:
    return orgs_store._uid(user_id)


def _now() -> str:
    return orgs_store._now_iso()


def _blob(value: Any, field: str, limit: int = KEY_MAX) -> str:
    """Шифр, ключ или конверт — непрозрачная строка: проверяем только форму и размер."""
    text = str(value or "").strip()
    if not text:
        raise OrgError(f"Нет поля {field}", 400)
    if len(text) > limit:
        raise OrgError("Слишком большие данные", 400)
    if text.startswith("{"):
        try:
            json.loads(text)
        except ValueError as e:
            raise OrgError(f"Поле {field} повреждено", 400) from e
    elif not _B64.match(text):
        raise OrgError(f"Поле {field} повреждено", 400)
    return text


def _clean_name(name: str) -> str:
    text = " ".join(str(name or "").split())
    if not text:
        raise OrgError("Введите название сейфа", 400)
    if len(text) > NAME_MAX:
        raise OrgError(f"Название — до {NAME_MAX} символов", 400)
    return text


# ---------- ключи человека ----------


def my_keys(user_id: int | str) -> dict[str, Any]:
    """Открытый ключ, зашифрованный закрытый и способы входа. Без них расшифровать ничего нельзя."""
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        k = conn.execute("SELECT * FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone()
        rows = conn.execute("SELECT * FROM vault_unlockers WHERE user_id = ? ORDER BY id", (uid,)).fetchall()
    return {
        "enabled": bool(k),
        "public_key": k["public_key"] if k else None,
        "enc_private": k["enc_private"] if k else None,
        "unlockers": [_unlocker_out(r) for r in rows],
    }


def _unlocker_out(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(r["id"]), "kind": r["kind"], "credential_id": r["credential_id"], "salt": r["salt"],
        "wrapped_umk": r["wrapped_umk"], "label": r["label"], "created_at": r["created_at"],
        "last_used_at": r["last_used_at"],
    }


def _check_unlocker(u: dict[str, Any]) -> tuple[str, str, str, str, str]:
    kind = str(u.get("kind") or "")
    if kind not in ("passkey", "recovery"):
        raise OrgError("Неизвестный способ входа", 400)
    cred = str(u.get("credential_id") or "") if kind == "passkey" else ""
    if kind == "passkey":
        _blob(cred, "credential_id", 1024)
    label = " ".join(str(u.get("label") or "").split())[:60]
    return kind, cred, _blob(u.get("salt"), "salt", 256), _blob(u.get("wrapped_umk"), "wrapped_umk"), label


def setup(user_id: int | str, public_key: str, enc_private: str, unlockers: list[dict[str, Any]], *,
          reset: bool = False) -> dict[str, Any]:
    """Включить сейфы: пара ключей и хотя бы passkey + аварийный код.

    ``reset`` — начать с нуля (потеряны все устройства и аварийный лист): старые конверты удаляются,
    доступ к сейфам заново выдадут коллеги.
    """
    uid = _uid(user_id)
    pub = _blob(public_key, "public_key")
    priv = _blob(enc_private, "enc_private")
    rows = [_check_unlocker(u) for u in (unlockers or [])]
    kinds = {r[0] for r in rows}
    if "recovery" not in kinds:
        raise OrgError("Нужен код с аварийного листа", 400)
    if len(rows) > UNLOCKERS_MAX:
        raise OrgError("Слишком много способов входа", 400)
    now = _now()
    with _LOCK:
        conn = _conn()
        had = conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone()
        if had and not reset:
            raise OrgError("Сейфы уже включены", 400)
        if had:
            conn.execute("DELETE FROM vault_unlockers WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM vault_envelopes WHERE user_id = ?", (uid,))
            conn.execute("DELETE FROM vault_user_keys WHERE user_id = ?", (uid,))
        conn.execute(
            "INSERT INTO vault_user_keys (user_id, public_key, enc_private, created_at, updated_at) VALUES (?,?,?,?,?)",
            (uid, pub, priv, now, now),
        )
        for kind, cred, salt, wrapped, label in rows:
            conn.execute(
                "INSERT INTO vault_unlockers (user_id, kind, credential_id, salt, wrapped_umk, label, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, kind, cred, salt, wrapped, label, now),
            )
        conn.commit()
    return my_keys(uid)


def add_unlocker(user_id: int | str, unlocker: dict[str, Any]) -> dict[str, Any]:
    """Ещё одно устройство (passkey) или новый аварийный код — ту же UMK обернули по-другому."""
    uid = _uid(user_id)
    kind, cred, salt, wrapped, label = _check_unlocker(unlocker)
    with _LOCK:
        conn = _conn()
        if not conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone():
            raise OrgError("Сначала включите сейфы", 400)
        n = conn.execute("SELECT COUNT(*) FROM vault_unlockers WHERE user_id = ?", (uid,)).fetchone()[0]
        if n >= UNLOCKERS_MAX:
            raise OrgError("Слишком много способов входа — удалите старые", 400)
        if kind == "recovery":  # аварийный код один: новый заменяет старый
            conn.execute("DELETE FROM vault_unlockers WHERE user_id = ? AND kind = 'recovery'", (uid,))
        conn.execute(
            "INSERT INTO vault_unlockers (user_id, kind, credential_id, salt, wrapped_umk, label, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, kind, cred, salt, wrapped, label, _now()),
        )
        conn.commit()
    return my_keys(uid)


def remove_unlocker(user_id: int | str, unlocker_id: int) -> dict[str, Any]:
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        row = conn.execute(
            "SELECT kind FROM vault_unlockers WHERE id = ? AND user_id = ?", (int(unlocker_id), uid)
        ).fetchone()
        if not row:
            raise OrgError("Способ входа не найден", 404)
        if row["kind"] == "recovery":
            raise OrgError("Аварийный код можно только заменить новым", 400)
        left = conn.execute(
            "SELECT COUNT(*) FROM vault_unlockers WHERE user_id = ? AND kind = 'passkey' AND id != ?",
            (uid, int(unlocker_id)),
        ).fetchone()[0]
        if not left:
            raise OrgError("Это последнее устройство — сначала добавьте другое", 400)
        conn.execute("DELETE FROM vault_unlockers WHERE id = ?", (int(unlocker_id),))
        conn.commit()
    return my_keys(uid)


def touch_unlocker(user_id: int | str, unlocker_id: int) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE vault_unlockers SET last_used_at = ? WHERE id = ? AND user_id = ?",
            (_now(), int(unlocker_id), _uid(user_id)),
        )
        conn.commit()


def _public_keys(conn: sqlite3.Connection, uids: Iterable[str]) -> dict[str, str]:
    ids = list(uids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {
        str(r["user_id"]): r["public_key"]
        for r in conn.execute(f"SELECT user_id, public_key FROM vault_user_keys WHERE user_id IN ({marks})", ids)
    }


# ---------- кто что может ----------


def _vault(conn: sqlite3.Connection, vault_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM vaults WHERE id = ?", (int(vault_id),)).fetchone()
    if not row:
        raise OrgError("Сейф не найден", 404)
    return row


def _acl(conn: sqlite3.Connection, vault_id: int) -> dict[str, str]:
    return {r["principal"]: r["role"] for r in conn.execute(
        "SELECT principal, role FROM vault_acl WHERE vault_id = ?", (int(vault_id),)
    )}


def _role_from(acl: dict[str, str], principals: set[str]) -> str:
    best = ""
    for p, r in acl.items():
        if p in principals and ROLE_RANK.get(r, 0) > ROLE_RANK.get(best, 0):
            best = r
    return best


def _principals(org_id: Optional[int], user_id: int | str) -> set[str]:
    """Без замка (org_groups берёт его сам). Личный сейф — только ``u:<id>``."""
    if org_id is None:
        return {f"u:{_uid(user_id)}"}
    return org_groups.principals(int(org_id), user_id)


def role_in(vault_id: int, user_id: int | str) -> str:
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    return _role_from(acl, _principals(v["org_id"], user_id))


def _require(vault_id: int, user_id: int | str, need: str) -> tuple[sqlite3.Row, str]:
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    role = _role_from(acl, _principals(v["org_id"], user_id))
    if not role:
        raise OrgError("Сейф не найден", 404)
    if ROLE_RANK[role] < ROLE_RANK[need]:
        raise OrgError("Не хватает прав в этом сейфе", 400)
    return v, role


def _members(org_id: Optional[int], acl: dict[str, str]) -> set[str]:
    """Кому сейф открыт по правам — конкретные люди. Без замка."""
    out: set[str] = set()
    for p in acl:
        if p.startswith("u:"):
            uid = p[2:]
            if org_id is None or orgs_store.role_of(int(org_id), uid):
                out.add(uid)
        elif p.startswith("g:") and org_id is not None:
            try:
                out.update(org_groups.member_ids(int(org_id), int(p[2:])))
            except OrgError:
                continue  # отдел удалили — права на него ничего не дают
    return out


def _sync_envelopes(vault_id: int) -> tuple[set[str], set[str]]:
    """Убрать конверты тех, кому сейф больше не открыт (ушёл из команды, организации, сняли доступ).

    Возвращает (участники, у кого есть конверт). Уход записывается в ``vault_departures`` —
    по нему участникам показывается, какие пароли сменить.
    """
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    members = _members(v["org_id"], acl)
    with _LOCK:
        conn = _conn()
        have = {str(r["user_id"]) for r in conn.execute(
            "SELECT user_id FROM vault_envelopes WHERE vault_id = ?", (int(vault_id),)
        )}
        gone = have - members
        if gone:
            now = _now()
            for uid in gone:
                conn.execute("DELETE FROM vault_envelopes WHERE vault_id = ? AND user_id = ?", (int(vault_id), uid))
                conn.execute(
                    "INSERT INTO vault_departures (vault_id, user_id, at) VALUES (?,?,?) "
                    "ON CONFLICT(vault_id, user_id) DO UPDATE SET at = excluded.at, dismissed = 0",
                    (int(vault_id), uid, now),
                )
            conn.commit()
    return members, have - gone


# ---------- сейфы ----------


def _org_ids(user_id: int | str) -> list[int]:
    return [int(o["id"]) for o in orgs_store.orgs_for_user(user_id)]


def list_for(user_id: int | str) -> list[dict[str, Any]]:
    """Сейфы, открытые человеку: личный и организаций. С его конвертом и списком «ждут конверт»."""
    uid = _uid(user_id)
    org_ids = _org_ids(uid)
    with _LOCK:
        conn = _conn()
        marks = ",".join("?" * len(org_ids)) or "NULL"
        rows = conn.execute(
            f"SELECT * FROM vaults WHERE org_id IN ({marks}) OR (org_id IS NULL AND created_by = ?) ORDER BY name COLLATE NOCASE, id",
            (*org_ids, uid),
        ).fetchall()
    principals_by_org: dict[Any, set[str]] = {}
    out: list[dict[str, Any]] = []
    for v in rows:
        key = v["org_id"]
        if key not in principals_by_org:
            principals_by_org[key] = _principals(key, uid)
        with _LOCK:
            acl = _acl(_conn(), v["id"])
        role = _role_from(acl, principals_by_org[key])
        if not role:
            continue
        out.append(_vault_out(v, role, uid))
    return out


def _vault_out(v: sqlite3.Row, role: str, uid: str) -> dict[str, Any]:
    vid = int(v["id"])
    members, have = _sync_envelopes(vid)
    with _LOCK:
        conn = _conn()
        mine = conn.execute(
            "SELECT envelope FROM vault_envelopes WHERE vault_id = ? AND user_id = ?", (vid, uid)
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM vault_items WHERE vault_id = ?", (vid,)).fetchone()[0]
        keys = _public_keys(conn, members - have)
        acl = _acl(conn, vid)
        departed = conn.execute(
            "SELECT COUNT(*) FROM vault_departures WHERE vault_id = ? AND dismissed = 0", (vid,)
        ).fetchone()[0]
    pending = [{"user_id": u, "public_key": keys[u]} for u in sorted(members - have) if u in keys]
    return {
        "id": vid,
        "org_id": v["org_id"],
        "personal": v["org_id"] is None,
        "name": v["name"],
        "role": role,
        "items": int(count),
        "envelope": mine["envelope"] if mine else None,
        "members": len(members),
        "holders": len(have),  # у скольких есть ключ: 0 — сейф уже никто не откроет
        # ключ нужен тем, у кого он есть: только они могут положить конверт
        "pending": pending if mine else [],
        "waiting_setup": len([u for u in members - have if u not in keys]),
        "departed": int(departed) if role in ("editor", "owner") else 0,
        "access": _access_line(v["org_id"], acl),
    }


def _access_line(org_id: Optional[int], acl: dict[str, str]) -> str:
    """«Кому открыт» одной строкой: отделы и команды по названиям, люди — числом (или имя, если один)."""
    if org_id is None:
        return "только вы"
    from assistant.stores import note_members

    gids = [int(p[2:]) for p in acl if p.startswith("g:")]
    users = [p[2:] for p in acl if p.startswith("u:")]
    names: list[str] = []
    if gids:
        marks = ",".join("?" * len(gids))
        with _LOCK:
            rows = _conn().execute(
                f"SELECT name FROM org_groups WHERE org_id = ? AND id IN ({marks}) ORDER BY name", (int(org_id), *gids)
            ).fetchall()
        names = [str(r["name"]) for r in rows]
    if len(users) == 1 and not names:
        return note_members.profile_display_name(note_members.get_profile(users[0]), fallback="1 человек")
    if users:
        names.append(f"{len(users)} чел.")
    return ", ".join(names) or "никому"


def create(user_id: int | str, org_id: Optional[int], name: str, envelope: str,
           access: Optional[list[dict[str, str]]] = None) -> dict[str, Any]:
    """Новый сейф. Создатель — владелец; ``envelope`` — ключ сейфа, запечатанный его открытым ключом."""
    uid = _uid(user_id)
    title = _clean_name(name)
    env = _blob(envelope, "envelope")
    if org_id is not None and not orgs_store.role_of(int(org_id), uid):
        raise OrgError("Организация не найдена", 404)
    extra: list[tuple[str, str]] = []
    for a in access or []:
        extra.append((_check_principal(org_id, a.get("principal")), _check_role(a.get("role"))))
    with _LOCK:
        conn = _conn()
        if not conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone():
            raise OrgError("Сначала включите сейфы", 400)
        if org_id is None and conn.execute(
            "SELECT 1 FROM vaults WHERE org_id IS NULL AND created_by = ?", (uid,)
        ).fetchone():
            raise OrgError("Личный сейф уже есть", 400)
        now = _now()
        cur = conn.execute(
            "INSERT INTO vaults (org_id, name, created_by, created_at) VALUES (?,?,?,?)",
            (None if org_id is None else int(org_id), title, uid, now),
        )
        vid = int(cur.lastrowid)
        conn.execute("INSERT INTO vault_acl (vault_id, principal, role) VALUES (?,?,?)", (vid, f"u:{uid}", "owner"))
        for p, r in extra:
            if p != f"u:{uid}":
                conn.execute("INSERT OR REPLACE INTO vault_acl (vault_id, principal, role) VALUES (?,?,?)", (vid, p, r))
        conn.execute(
            "INSERT INTO vault_envelopes (vault_id, user_id, envelope, created_by, created_at) VALUES (?,?,?,?,?)",
            (vid, uid, env, uid, now),
        )
        conn.commit()
    return get(vid, uid)


def get(vault_id: int, user_id: int | str) -> dict[str, Any]:
    v, role = _require(vault_id, user_id, "viewer")
    return _vault_out(v, role, _uid(user_id))


def rename(user_id: int | str, vault_id: int, name: str) -> dict[str, Any]:
    _require(vault_id, user_id, "owner")
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE vaults SET name = ? WHERE id = ?", (_clean_name(name), int(vault_id)))
        conn.commit()
    return get(vault_id, user_id)


def delete(user_id: int | str, vault_id: int) -> None:
    _require(vault_id, user_id, "owner")
    with _LOCK:
        conn = _conn()
        for table in ("vault_items", "vault_envelopes", "vault_acl", "vault_log", "vault_departures"):
            conn.execute(f"DELETE FROM {table} WHERE vault_id = ?", (int(vault_id),))
        conn.execute("DELETE FROM vaults WHERE id = ?", (int(vault_id),))
        conn.commit()


def delete_org(org_id: int) -> None:
    """Организацию удалили — её сейфы тоже (вызывается из orgs_store.delete_org, под замком)."""
    conn = _conn()
    ids = [int(r["id"]) for r in conn.execute("SELECT id FROM vaults WHERE org_id = ?", (int(org_id),))]
    for vid in ids:
        for table in ("vault_items", "vault_envelopes", "vault_acl", "vault_log", "vault_departures"):
            conn.execute(f"DELETE FROM {table} WHERE vault_id = ?", (vid,))
    conn.execute("DELETE FROM vaults WHERE org_id = ?", (int(org_id),))


# ---------- доступ ----------


def _check_role(role: Any) -> str:
    r = str(role or "")
    if r not in ROLES:
        raise OrgError("Неизвестная роль", 400)
    return r


def _check_principal(org_id: Optional[int], principal: Any) -> str:
    p = str(principal or "")
    if p == org_groups.ALL:
        raise OrgError("Сейф нельзя открыть всем в организации — выберите отдел, команду или людей", 400)
    if org_id is None:
        raise OrgError("Личный сейф нельзя открыть другим", 400)
    if p.startswith("u:") and p[2:].isdigit():
        if not orgs_store.role_of(int(org_id), p[2:]):
            raise OrgError("Этот человек не в организации", 400)
        return p
    if p.startswith("g:") and p[2:].isdigit():
        org_groups.member_ids(int(org_id), int(p[2:]))  # проверит, что отдел из этой организации
        return p
    raise OrgError("Кому открыть — непонятно", 400)


def access(vault_id: int, user_id: int | str) -> dict[str, Any]:
    """Права сейфа и люди с отметкой: есть конверт / ждёт ключ / ещё не включил сейфы."""
    v, role = _require(vault_id, user_id, "viewer")
    members, have = _sync_envelopes(vault_id)
    with _LOCK:
        conn = _conn()
        acl = _acl(conn, vault_id)
        keys = _public_keys(conn, members)
    from assistant.stores import note_members

    people = []
    for u in sorted(members):
        prof = note_members.get_profile(u)
        people.append({
            "user_id": u,
            "name": note_members.profile_display_name(prof, fallback=u),
            "state": "ok" if u in have else ("pending" if u in keys else "no_keys"),
        })
    return {
        "vault_id": int(vault_id),
        "role": role,
        "acl": [{"principal": p, "role": r} for p, r in sorted(acl.items())],
        "people": people,
        "roles": [{"id": r, "label": ROLE_LABEL[r]} for r in ROLES],
    }


def set_access(user_id: int | str, vault_id: int, principal: str, role: str) -> dict[str, Any]:
    """Открыть (``role``) или закрыть (``role=''``) сейф. Последнего владельца убрать нельзя."""
    v, _ = _require(vault_id, user_id, "owner")
    p = _check_principal(v["org_id"], principal)
    with _LOCK:
        conn = _conn()
        acl = _acl(conn, vault_id)
        if role:
            acl[p] = _check_role(role)
        else:
            acl.pop(p, None)
        if "owner" not in acl.values():
            raise OrgError("У сейфа должен остаться владелец", 400)
        if role:
            conn.execute("INSERT OR REPLACE INTO vault_acl (vault_id, principal, role) VALUES (?,?,?)",
                         (int(vault_id), p, acl[p]))
        else:
            conn.execute("DELETE FROM vault_acl WHERE vault_id = ? AND principal = ?", (int(vault_id), p))
        conn.commit()
    return access(vault_id, user_id)


def put_envelopes(user_id: int | str, vault_id: int, envelopes: list[dict[str, str]]) -> dict[str, Any]:
    """Положить конверты тем, кто ждёт. Класть может только тот, у кого конверт уже есть."""
    uid = _uid(user_id)
    _require(vault_id, uid, "viewer")
    members, have = _sync_envelopes(vault_id)
    if uid not in have:
        raise OrgError("Сначала откройте сейф у себя", 400)
    with _LOCK:
        conn = _conn()
        keys = _public_keys(conn, members - have)
        now = _now()
        put = 0
        for e in envelopes or []:
            target = _uid(e.get("user_id") or 0)
            if target not in keys:
                continue  # не ждёт: уже есть конверт, не участник или не включил сейфы
            conn.execute(
                "INSERT OR REPLACE INTO vault_envelopes (vault_id, user_id, envelope, created_by, created_at) "
                "VALUES (?,?,?,?,?)",
                (int(vault_id), target, _blob(e.get("envelope"), "envelope"), uid, now),
            )
            put += 1
        conn.commit()
    return {"put": put}


# ---------- записи ----------


def items(vault_id: int, user_id: int | str) -> list[dict[str, Any]]:
    _require(vault_id, user_id, "viewer")
    from assistant.stores import note_members

    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM vault_items WHERE vault_id = ? ORDER BY id", (int(vault_id),)
        ).fetchall()
    names: dict[str, str] = {}

    def name(u: str) -> str:
        if u not in names:
            names[u] = note_members.profile_display_name(note_members.get_profile(u), fallback="")
        return names[u]

    return [{
        "id": int(r["id"]), "blob": r["blob"], "created_at": r["created_at"], "updated_at": r["updated_at"],
        "updated_by": str(r["updated_by"]), "updated_by_name": name(str(r["updated_by"])),
    } for r in rows]


def add_item(user_id: int | str, vault_id: int, blob: str) -> dict[str, Any]:
    _require(vault_id, user_id, "editor")
    uid = _uid(user_id)
    data = _blob(blob, "blob", BLOB_MAX)
    now = _now()
    with _LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO vault_items (vault_id, blob, created_by, created_at, updated_by, updated_at) VALUES (?,?,?,?,?,?)",
            (int(vault_id), data, uid, now, uid, now),
        )
        conn.commit()
        iid = int(cur.lastrowid)
    return {"id": iid, "updated_at": now}


def _item(conn: sqlite3.Connection, vault_id: int, item_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM vault_items WHERE id = ? AND vault_id = ?", (int(item_id), int(vault_id))
    ).fetchone()
    if not row:
        raise OrgError("Запись не найдена", 404)
    return row


def update_item(user_id: int | str, vault_id: int, item_id: int, blob: str) -> dict[str, Any]:
    _require(vault_id, user_id, "editor")
    data = _blob(blob, "blob", BLOB_MAX)
    now = _now()
    with _LOCK:
        conn = _conn()
        _item(conn, vault_id, item_id)
        conn.execute(
            "UPDATE vault_items SET blob = ?, updated_by = ?, updated_at = ? WHERE id = ?",
            (data, _uid(user_id), now, int(item_id)),
        )
        conn.commit()
    return {"id": int(item_id), "updated_at": now}


def delete_item(user_id: int | str, vault_id: int, item_id: int) -> None:
    _require(vault_id, user_id, "editor")
    with _LOCK:
        conn = _conn()
        _item(conn, vault_id, item_id)
        conn.execute("DELETE FROM vault_items WHERE id = ?", (int(item_id),))
        conn.execute("DELETE FROM vault_log WHERE item_id = ?", (int(item_id),))
        conn.commit()


# ---------- журнал ----------


def log(user_id: int | str, vault_id: int, item_id: int, action: str) -> None:
    """Кто открыл или скопировал секрет. Сам секрет сюда не попадает — только факт."""
    if action not in LOG_ACTIONS:
        raise OrgError("Неизвестное действие", 400)
    _require(vault_id, user_id, "viewer")
    with _LOCK:
        conn = _conn()
        _item(conn, vault_id, item_id)
        conn.execute(
            "INSERT INTO vault_log (vault_id, item_id, user_id, action, at) VALUES (?,?,?,?,?)",
            (int(vault_id), int(item_id), _uid(user_id), action, _now()),
        )
        conn.commit()


def item_log(user_id: int | str, vault_id: int, item_id: int, limit: int = 30) -> list[dict[str, Any]]:
    _require(vault_id, user_id, "viewer")
    from assistant.stores import note_members

    with _LOCK:
        conn = _conn()
        _item(conn, vault_id, item_id)
        rows = conn.execute(
            "SELECT user_id, action, at FROM vault_log WHERE vault_id = ? AND item_id = ? ORDER BY at DESC, id DESC LIMIT ?",
            (int(vault_id), int(item_id), int(limit)),
        ).fetchall()
        item = _item(conn, vault_id, item_id)
    out = [{
        "user_id": str(r["user_id"]), "action": r["action"], "at": r["at"],
        "name": note_members.profile_display_name(note_members.get_profile(r["user_id"]), fallback=""),
    } for r in rows]
    # создание и правка — тоже события журнала, берём из самой записи
    out.append({
        "user_id": str(item["updated_by"]), "action": "update" if item["updated_at"] != item["created_at"] else "create",
        "at": item["updated_at"],
        "name": note_members.profile_display_name(note_members.get_profile(item["updated_by"]), fallback=""),
    })
    out.sort(key=lambda x: x["at"], reverse=True)
    return out[:limit]
