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
Владельцы организации — всегда владельцы всех её сейфов (решение владельца 2026-11-08): конверт им кладётся
при создании сейфа и раздаётся, как всем участникам. Админы организации сейфов автоматически не видят.
Остальные участники (не гости) видят названия сейфов организации и могут запросить доступ (``vault_requests``).
Личный сейф (``org_id IS NULL``) — только владельцу.
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
VAULT_TABLES = ("vault_items", "vault_envelopes", "vault_acl", "vault_log", "vault_departures", "vault_requests")
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
        CREATE TABLE IF NOT EXISTS vault_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, vault_id INTEGER NOT NULL, user_id TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, decided_by TEXT, decided_at TEXT, role TEXT,
            notified INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_vault_requests_vault ON vault_requests(vault_id, status);
        CREATE TABLE IF NOT EXISTS vault_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, public_key TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, sealed TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, decided_at TEXT, notified INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_vault_links_user ON vault_links(user_id, status);
        """
    )
    _add_column(conn, "vaults", "key_version", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "vaults", "needs_rotation", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "vault_departures", "notified", "INTEGER NOT NULL DEFAULT 0")
    # Формат шифра 2 (аудит 2026-11-10): номер сейфа и записей придумывает браузер, они в AAD вместе с версией ключа
    _add_column(conn, "vaults", "fmt", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "vaults", "uid", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "vault_items", "uid", "TEXT NOT NULL DEFAULT ''")
    # kind=device (браузеры без PRF): открытый ключ passkey, алгоритм, rp_id и счётчик — для проверки касания
    _add_column(conn, "vault_unlockers", "public_key", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "vault_unlockers", "alg", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "vault_unlockers", "rp_id", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "vault_unlockers", "sign_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "vault_unlockers", "challenge", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "vault_unlockers", "challenge_at", "TEXT NOT NULL DEFAULT ''")
    conn.commit()
    _SCHEMA_CONN = conn
    return conn


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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


_UID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _check_uid(value: Any, field: str) -> str:
    u = str(value or "").strip()
    if not _UID.match(u):
        raise OrgError(f"Поле {field} повреждено", 400)
    return u


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
    # У kind=device обёртку не отдаём: только после проверенного касания (unlock_device)
    return {
        "id": int(r["id"]), "kind": r["kind"], "credential_id": r["credential_id"], "salt": r["salt"],
        "wrapped_umk": "" if r["kind"] == "device" else r["wrapped_umk"], "label": r["label"],
        "created_at": r["created_at"], "last_used_at": r["last_used_at"],
        "rp_id": r["rp_id"],  # адрес, к которому привязан passkey: страница берёт только свои
    }


DEVICE_KINDS = ("passkey", "device")
CHALLENGE_TTL = 180  # секунд на касание


def _check_unlocker(u: dict[str, Any]) -> tuple[str, str, str, str, str, str, int, str]:
    """(kind, credential_id, salt, wrapped_umk, label, public_key, alg, rp_id)."""
    kind = str(u.get("kind") or "")
    if kind not in ("passkey", "recovery", "device"):
        raise OrgError("Неизвестный способ входа", 400)
    cred = str(u.get("credential_id") or "") if kind in DEVICE_KINDS else ""
    if kind in DEVICE_KINDS:
        _blob(cred, "credential_id", 1024)
    pub, alg, rp_id = "", 0, ""
    if kind == "device":
        from assistant.lib import webauthn_verify as wa

        pub = _blob(u.get("public_key"), "public_key", 2048)
        try:
            alg = int(u.get("alg") or 0)
            wa.check_public_key(pub, alg)
        except (ValueError, TypeError) as e:
            raise OrgError("Ключ устройства не подходит", 400) from e
    if kind in DEVICE_KINDS:
        rp_id = str(u.get("rp_id") or "").strip().lower()
        if not rp_id or len(rp_id) > 253:
            raise OrgError("Нет адреса сайта для ключа устройства", 400)
    label = " ".join(str(u.get("label") or "").split())[:60]
    return (kind, cred, _blob(u.get("salt"), "salt", 256), _blob(u.get("wrapped_umk"), "wrapped_umk"), label,
            pub, alg, rp_id)


_INSERT_UNLOCKER = (
    "INSERT INTO vault_unlockers (user_id, kind, credential_id, salt, wrapped_umk, label, created_at, public_key, alg, rp_id) "
    "VALUES (?,?,?,?,?,?,?,?,?,?)"
)


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
        for kind, cred, salt, wrapped, label, pub, alg, rp_id in rows:
            conn.execute(_INSERT_UNLOCKER, (uid, kind, cred, salt, wrapped, label, now, pub, alg, rp_id))
        conn.commit()
    return my_keys(uid)


def add_unlocker(user_id: int | str, unlocker: dict[str, Any]) -> dict[str, Any]:
    """Ещё одно устройство (passkey) или новый аварийный код — ту же UMK обернули по-другому."""
    uid = _uid(user_id)
    kind, cred, salt, wrapped, label, pub, alg, rp_id = _check_unlocker(unlocker)
    with _LOCK:
        conn = _conn()
        if not conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone():
            raise OrgError("Сначала включите сейфы", 400)
        n = conn.execute("SELECT COUNT(*) FROM vault_unlockers WHERE user_id = ?", (uid,)).fetchone()[0]
        if n >= UNLOCKERS_MAX:
            raise OrgError("Слишком много способов входа — удалите старые", 400)
        if kind == "recovery":  # аварийный код один: новый заменяет старый
            conn.execute("DELETE FROM vault_unlockers WHERE user_id = ? AND kind = 'recovery'", (uid,))
        conn.execute(_INSERT_UNLOCKER, (uid, kind, cred, salt, wrapped, label, _now(), pub, alg, rp_id))
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
            "SELECT COUNT(*) FROM vault_unlockers WHERE user_id = ? AND kind IN ('passkey','device') AND id != ?",
            (uid, int(unlocker_id)),
        ).fetchone()[0]
        if not left:
            raise OrgError("Это последнее устройство — сначала добавьте другое", 400)
        conn.execute("DELETE FROM vault_unlockers WHERE id = ?", (int(unlocker_id),))
        conn.commit()
    return my_keys(uid)


def _device_row(conn: sqlite3.Connection, uid: str, unlocker_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM vault_unlockers WHERE id = ? AND user_id = ? AND kind = 'device'", (int(unlocker_id), uid)
    ).fetchone()
    if not row:
        raise OrgError("Способ входа не найден", 404)
    return row


def device_challenge(user_id: int | str, unlocker_id: int) -> dict[str, Any]:
    """Одноразовый вызов для касания на устройстве без PRF. Новый вызов заменяет прежний."""
    import secrets

    from assistant.lib.webauthn_verify import b64u_encode

    uid = _uid(user_id)
    ch = b64u_encode(secrets.token_bytes(32))
    with _LOCK:
        conn = _conn()
        row = _device_row(conn, uid, unlocker_id)
        conn.execute("UPDATE vault_unlockers SET challenge = ?, challenge_at = ? WHERE id = ?", (ch, _now(), int(row["id"])))
        conn.commit()
    return {"challenge": ch, "credential_id": row["credential_id"], "rp_id": row["rp_id"]}


def unlock_device(user_id: int | str, unlocker_id: int, client_data_json: str, authenticator_data: str,
                  signature: str) -> dict[str, Any]:
    """Проверить касание (подпись passkey с проверкой пользователя) и отдать обёртку ключа сейфов.

    Обёртку открывает только ключ, который лежит в IndexedDB этого браузера, — сервер его не видит.
    """
    from datetime import datetime, timezone

    from assistant.lib import webauthn_verify as wa

    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        row = _device_row(conn, uid, unlocker_id)
        ch, at = row["challenge"], row["challenge_at"]
        # вызов одноразовый: стираем сразу, даже если подпись не сойдётся
        conn.execute("UPDATE vault_unlockers SET challenge = '', challenge_at = '' WHERE id = ?", (int(row["id"]),))
        conn.commit()
        if not ch or not at:
            raise OrgError("Касание устарело — попробуйте ещё раз", 400)
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            age = CHALLENGE_TTL + 1
        if age > CHALLENGE_TTL:
            raise OrgError("Касание устарело — попробуйте ещё раз", 400)
        if not wa.rp_id_allowed(row["rp_id"]):
            raise OrgError("Этот способ входа привязан к другому адресу — добавьте Touch ID заново", 400)
        try:
            count = wa.verify_assertion(
                public_key=row["public_key"], alg=int(row["alg"]), rp_id=row["rp_id"], challenge=ch,
                client_data_json=client_data_json, authenticator_data=authenticator_data, signature=signature,
                prev_sign_count=int(row["sign_count"] or 0),
            )
        except wa.WebAuthnError as e:
            print(f"[vaults] device unlock rejected uid={uid} unlocker={int(row['id'])} reason={e}")
            raise OrgError("Касание не прошло проверку — попробуйте ещё раз", 400) from e
        conn.execute(
            "UPDATE vault_unlockers SET sign_count = ?, last_used_at = ? WHERE id = ?", (count, _now(), int(row["id"]))
        )
        conn.commit()
    return {"wrapped_umk": row["wrapped_umk"], "salt": row["salt"]}


# ---------- подтвердить новое устройство с другого (своего) ----------
# Новое устройство без ключей присылает одноразовый открытый ключ; устройство с ключами после касания
# запечатывает ему UMK. Сервер видит только запечатанное; код из открытого ключа люди сверяют глазами.

LINK_TTL = 600  # 10 минут на подтверждение
LINKS_PER_HOUR = 6


def _age_s(at: str) -> float:
    from datetime import datetime, timezone

    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(str(at).replace("Z", "+00:00"))).total_seconds()
    except ValueError:
        return 1e9


def link_start(user_id: int | str, public_key: str, label: str = "") -> dict[str, Any]:
    uid = _uid(user_id)
    pub = _blob(public_key, "public_key")
    label = " ".join(str(label or "").split())[:60]
    with _LOCK:
        conn = _conn()
        if not conn.execute("SELECT 1 FROM vault_user_keys WHERE user_id = ?", (uid,)).fetchone():
            raise OrgError("Сначала включите сейфы", 400)
        recent = [r["created_at"] for r in conn.execute(
            "SELECT created_at FROM vault_links WHERE user_id = ? ORDER BY id DESC LIMIT ?", (uid, LINKS_PER_HOUR))]
        if len(recent) >= LINKS_PER_HOUR and _age_s(recent[-1]) < 3600:
            raise OrgError("Слишком много запросов — попробуйте через час или войдите кодом с аварийного листа", 400)
        # одновременно ждёт один запрос: новый заменяет прежний
        conn.execute("UPDATE vault_links SET status = 'cancelled', sealed = '' WHERE user_id = ? AND status = 'pending'", (uid,))
        cur = conn.execute(
            "INSERT INTO vault_links (user_id, public_key, label, status, created_at) VALUES (?,?,?,'pending',?)",
            (uid, pub, label, _now()),
        )
        conn.commit()
        return {"id": int(cur.lastrowid), "ttl": LINK_TTL}


def _link(conn: sqlite3.Connection, uid: str, link_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM vault_links WHERE id = ? AND user_id = ?", (int(link_id), uid)).fetchone()
    if not row:
        raise OrgError("Запрос не найден", 404)
    return row


def _link_status(row: sqlite3.Row) -> str:
    if row["status"] == "pending" and _age_s(row["created_at"]) > LINK_TTL:
        return "expired"
    return str(row["status"])


def link_status(user_id: int | str, link_id: int) -> dict[str, Any]:
    """Новое устройство спрашивает, что с запросом. Запечатанное отдаётся один раз."""
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        row = _link(conn, uid, link_id)
        status = _link_status(row)
        if status == "approved":
            conn.execute("UPDATE vault_links SET status = 'used', sealed = '' WHERE id = ?", (int(row["id"]),))
            conn.commit()
            return {"status": "approved", "sealed": row["sealed"]}
    return {"status": status}


def links_pending(user_id: int | str) -> list[dict[str, Any]]:
    uid = _uid(user_id)
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM vault_links WHERE user_id = ? AND status = 'pending' ORDER BY id DESC", (uid,)
        ).fetchall()
    return [
        {"id": int(r["id"]), "public_key": r["public_key"], "label": r["label"], "created_at": r["created_at"]}
        for r in rows if _link_status(r) == "pending"
    ]


def link_decide(user_id: int | str, link_id: int, sealed: Optional[str]) -> None:
    """Устройство с ключами разрешило (sealed — UMK, запечатанная одноразовому ключу) или отклонило (None)."""
    uid = _uid(user_id)
    blob = _blob(sealed, "sealed") if sealed else ""
    with _LOCK:
        conn = _conn()
        row = _link(conn, uid, link_id)
        if _link_status(row) != "pending":
            raise OrgError("Запрос уже не ждёт ответа — попросите новый на том устройстве", 400)
        conn.execute(
            "UPDATE vault_links SET status = ?, sealed = ?, decided_at = ? WHERE id = ?",
            ("approved" if blob else "rejected", blob, _now(), int(row["id"])),
        )
        conn.commit()


def link_cancel(user_id: int | str, link_id: int) -> None:
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        _link(conn, uid, link_id)
        conn.execute(
            "UPDATE vault_links SET status = 'cancelled', sealed = '' WHERE id = ? AND status IN ('pending','approved')",
            (int(link_id),),
        )
        conn.commit()


def link_events() -> list[dict[str, Any]]:
    """Новые запросы устройств — уведомить самого человека (один раз)."""
    with _LOCK:
        conn = _conn()
        rows = conn.execute("SELECT * FROM vault_links WHERE status = 'pending' AND notified = 0").fetchall()
        out = []
        for r in rows:
            conn.execute("UPDATE vault_links SET notified = 1 WHERE id = ?", (int(r["id"]),))
            if _link_status(r) == "pending":
                out.append({"kind": "device_link", "vault_id": 0, "vault_name": "", "to": [r["user_id"]],
                            "who_name": r["label"] or "Новое устройство"})
        conn.commit()
    return out


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


def _org_owner_ids(org_id: Optional[int]) -> set[str]:
    """Владельцы организации. Берёт замок сам."""
    if org_id is None:
        return set()
    with _LOCK:
        return _org_owners(_conn(), org_id)


def _role_for(v: sqlite3.Row, acl: dict[str, str], user_id: int | str) -> str:
    """Роль в сейфе: владелец организации — всегда «owner», остальные — по правам сейфа. Без замка."""
    uid = _uid(user_id)
    if v["org_id"] is not None and uid in _org_owner_ids(v["org_id"]):
        return "owner"
    return _role_from(acl, _principals(v["org_id"], uid))


def role_in(vault_id: int, user_id: int | str) -> str:
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    return _role_for(v, acl, user_id)


def _require(vault_id: int, user_id: int | str, need: str) -> tuple[sqlite3.Row, str]:
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    role = _role_for(v, acl, user_id)
    if not role:
        raise OrgError("Сейф не найден", 404)
    if ROLE_RANK[role] < ROLE_RANK[need]:
        raise OrgError("Не хватает прав в этом сейфе", 400)
    return v, role


def _members(org_id: Optional[int], acl: dict[str, str]) -> set[str]:
    """Кому сейф открыт — конкретные люди: по правам сейфа и владельцы организации. Без замка."""
    out: set[str] = set(_org_owner_ids(org_id))
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
                    "ON CONFLICT(vault_id, user_id) DO UPDATE SET at = excluded.at, dismissed = 0, notified = 0",
                    (int(vault_id), uid, now),
                )
            # ушедший мог сохранить ключ сейфа — браузер участника сменит его (rotate)
            conn.execute("UPDATE vaults SET needs_rotation = 1 WHERE id = ?", (int(vault_id),))
        conn.commit()
    return members, have - gone


def _org_owners(conn: sqlite3.Connection, org_id: Optional[int]) -> set[str]:
    if org_id is None:
        return set()
    return {str(r["user_id"]) for r in conn.execute(
        "SELECT user_id FROM org_members WHERE org_id = ? AND role = 'owner' AND status = 'active'", (int(org_id),)
    )}


# ---------- сейфы ----------


def _org_ids(user_id: int | str) -> list[int]:
    return [int(o["id"]) for o in orgs_store.orgs_for_user(user_id)]


def list_for(user_id: int | str) -> list[dict[str, Any]]:
    """Сейфы человека: личный и организаций — с его конвертом и списком «ждут конверт».

    Сейфы организации, куда его не добавили, тоже видны (без содержимого) — чтобы запросить доступ.
    Гостям организации чужие сейфы не показываются.
    """
    uid = _uid(user_id)
    orgs = orgs_store.orgs_for_user(uid)
    org_ids = [int(o["id"]) for o in orgs]
    owned = {int(o["id"]) for o in orgs if o.get("my_role") == "owner"}
    with _LOCK:
        conn = _conn()
        marks = ",".join("?" * len(org_ids)) or "NULL"
        rows = conn.execute(
            f"SELECT * FROM vaults WHERE org_id IN ({marks}) OR (org_id IS NULL AND created_by = ?) ORDER BY name COLLATE NOCASE, id",
            (*org_ids, uid),
        ).fetchall()
        guest_in = {int(r["org_id"]) for r in conn.execute(
            f"SELECT org_id FROM org_members WHERE user_id = ? AND guest = 1 AND org_id IN ({marks})", (uid, *org_ids)
        )} if org_ids else set()
    principals_by_org: dict[Any, set[str]] = {}
    out: list[dict[str, Any]] = []
    for v in rows:
        key = v["org_id"]
        if key is not None and int(key) in owned:
            role = "owner"
        else:
            if key not in principals_by_org:
                principals_by_org[key] = _principals(key, uid)
            with _LOCK:
                acl = _acl(_conn(), v["id"])
            role = _role_from(acl, principals_by_org[key])
        if role:
            out.append(_vault_out(v, role, uid))
        elif key is not None and int(key) not in guest_in:
            out.append(_foreign_out(v, uid))
    return out


def _names(uids: Iterable[str]) -> dict[str, str]:
    from assistant.stores import note_members

    return {u: note_members.profile_display_name(note_members.get_profile(u), fallback="Участник") for u in uids}


def _foreign_out(v: sqlite3.Row, uid: str) -> dict[str, Any]:
    """Сейф организации, куда человека не добавили: название и запрос доступа, без содержимого."""
    vid = int(v["id"])
    with _LOCK:
        conn = _conn()
        acl = _acl(conn, vid)
        r = conn.execute(
            "SELECT * FROM vault_requests WHERE vault_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1", (vid, uid)
        ).fetchone()
    req = None
    if r and r["status"] in ("pending", "rejected"):
        req = {"status": r["status"], "created_at": r["created_at"], "decided_at": r["decided_at"],
               "decided_by_name": _names([str(r["decided_by"])]).get(str(r["decided_by"]), "") if r["decided_by"] else ""}
    return {
        "id": vid, "org_id": v["org_id"], "personal": False, "name": v["name"], "role": "", "foreign": True,
        "envelope": None, "items": None, "pending": [], "departed": 0, "departures": [], "requests": [],
        "access": _access_line(v["org_id"], acl), "key_version": int(v["key_version"]), "request": req,
    }


def _vault_out(v: sqlite3.Row, role: str, uid: str) -> dict[str, Any]:
    vid = int(v["id"])
    members, have = _sync_envelopes(vid)
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vid)  # после _sync_envelopes мог появиться флаг смены ключа
        mine = conn.execute(
            "SELECT envelope FROM vault_envelopes WHERE vault_id = ? AND user_id = ?", (vid, uid)
        ).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM vault_items WHERE vault_id = ?", (vid,)).fetchone()[0]
        owners = _org_owners(conn, v["org_id"])
        keys = _public_keys(conn, members)
        acl = _acl(conn, vid)
        deps = conn.execute(
            "SELECT user_id, at FROM vault_departures WHERE vault_id = ? AND dismissed = 0 ORDER BY at", (vid,)
        ).fetchall() if role in ("editor", "owner") else []
        if deps and not conn.execute(
            "SELECT 1 FROM vault_items WHERE vault_id = ? AND updated_at < ? LIMIT 1", (vid, deps[-1]["at"])
        ).fetchone():
            # всё, что ушедший мог видеть, уже сменили (или записей не было) — список больше не нужен
            conn.execute("UPDATE vault_departures SET dismissed = 1 WHERE vault_id = ? AND dismissed = 0", (vid,))
            conn.commit()
            deps = []
        reqs = conn.execute(
            "SELECT user_id, created_at FROM vault_requests WHERE vault_id = ? AND status = 'pending' ORDER BY id", (vid,)
        ).fetchall() if role == "owner" else []
    editor = role in ("editor", "owner")
    # ждут ключ — видит и выдаёт только владелец сейфа, кнопкой «Выдать»
    pending = [{"user_id": u, "public_key": keys[u]} for u in sorted(members - have) if u in keys] if role == "owner" else []
    rotate = bool(v["needs_rotation"]) and editor and bool(mine)
    names = _names([str(d["user_id"]) for d in deps] + [str(r["user_id"]) for r in reqs] + [p["user_id"] for p in pending])
    for p in pending:
        p["name"] = names.get(p["user_id"], "")
    return {
        "id": vid,
        "org_id": v["org_id"],
        "personal": v["org_id"] is None,
        "name": v["name"],
        "role": role,
        "org_owner": uid in owners,  # владелец организации: владелец сейфа всегда, снять нельзя
        "items": int(count),
        "envelope": mine["envelope"] if mine else None,
        "key_version": int(v["key_version"]),
        "fmt": int(v["fmt"] or 1), "uid": v["uid"],
        # старый формат шифра: первый же редактор переведёт сейф на формат 2 сменой ключа
        "upgrade": int(v["fmt"] or 1) < 2 and editor and bool(mine),
        "members": len(members),
        "holders": len(have),  # у скольких есть ключ: 0 — сейф уже никто не откроет
        # ключ нужен тем, у кого он есть: только они могут положить конверт
        "pending": pending if mine else [],
        "waiting_setup": len([u for u in members - have if u not in keys]),
        "departed": len(deps),
        "departures": [{"user_id": str(d["user_id"]), "name": names[str(d["user_id"])], "at": d["at"]} for d in deps],
        # сменить ключ: всем участникам с ключами — новые конверты
        "rotate": rotate,
        "rotate_keys": [{"user_id": u, "public_key": keys[u]} for u in sorted(members & have) if u in keys]
        if rotate or (int(v["fmt"] or 1) < 2 and editor and bool(mine)) else [],
        "requests": [{"user_id": str(r["user_id"]), "name": names[str(r["user_id"])], "at": r["created_at"]} for r in reqs],
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
           access: Optional[list[dict[str, str]]] = None,
           owner_envelopes: Optional[list[dict[str, str]]] = None, vault_uid: str = "") -> dict[str, Any]:
    """Новый сейф. Создатель — владелец; ``envelope`` — ключ сейфа, запечатанный его открытым ключом.

    Сейф организации сразу получает конверты владельцев организации (``owner_envelopes``) — они владельцы
    всех её сейфов. Иначе, если создатель уйдёт, не открыв сейф ещё раз, ключ остался бы только у него.
    Пока ни один владелец организации не включил сейфы, сейф в ней создать нельзя.
    """
    uid = _uid(user_id)
    title = _clean_name(name)
    env = _blob(envelope, "envelope")
    vuid = _check_uid(vault_uid, "uid") if vault_uid else ""  # с номером — сразу формат 2
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
        esc: dict[str, str] = {}
        if org_id is not None:
            owner_keys = _public_keys(conn, _org_owners(conn, org_id))
            if not owner_keys:
                raise OrgError(
                    "Сначала владелец организации должен включить сейфы — иначе при уходе сотрудника "
                    "сейф будет некому открыть", 400,
                )
            for e in owner_envelopes or []:
                target = _uid(e.get("user_id") or 0)
                if target in owner_keys and target != uid:
                    esc[target] = _blob(e.get("envelope"), "envelope")
            if not (set(owner_keys) - {uid}) <= set(esc):
                raise OrgError("Не хватает ключа для владельца организации — обновите страницу", 400)
        now = _now()
        cur = conn.execute(
            "INSERT INTO vaults (org_id, name, created_by, created_at, fmt, uid) VALUES (?,?,?,?,?,?)",
            (None if org_id is None else int(org_id), title, uid, now, 2 if vuid else 1, vuid),
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
        for target, e in esc.items():
            conn.execute(
                "INSERT INTO vault_envelopes (vault_id, user_id, envelope, created_by, created_at) VALUES (?,?,?,?,?)",
                (vid, target, e, uid, now),
            )
        conn.commit()
    return get(vid, uid)


def owner_keys_for(user_id: int | str) -> dict[str, list[dict[str, str]]]:
    """Открытые ключи владельцев каждой организации человека — чтобы при создании сейфа сразу
    положить им конверт. Пусто у организации — её владельцы ещё не включили сейфы."""
    org_ids = _org_ids(user_id)
    out: dict[str, list[dict[str, str]]] = {}
    with _LOCK:
        conn = _conn()
        for oid in org_ids:
            keys = _public_keys(conn, _org_owners(conn, oid))
            out[str(oid)] = [{"user_id": u, "public_key": k} for u, k in sorted(keys.items())]
    return out


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
        for table in VAULT_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE vault_id = ?", (int(vault_id),))
        conn.execute("DELETE FROM vaults WHERE id = ?", (int(vault_id),))
        conn.commit()


def delete_org(org_id: int) -> None:
    """Организацию удалили — её сейфы тоже (вызывается из orgs_store.delete_org, под замком)."""
    conn = _conn()
    ids = [int(r["id"]) for r in conn.execute("SELECT id FROM vaults WHERE org_id = ?", (int(org_id),))]
    for vid in ids:
        for table in VAULT_TABLES:
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

    owners = _org_owner_ids(v["org_id"])
    people = []
    for u in sorted(members):
        prof = note_members.get_profile(u)
        people.append({
            "user_id": u,
            "name": note_members.profile_display_name(prof, fallback=u),
            "state": "ok" if u in have else ("pending" if u in keys else "no_keys"),
            "org_owner": u in owners,  # владелец организации — владелец всех её сейфов
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
    org_owners = _org_owner_ids(v["org_id"])  # владельцы организации — владельцы сейфа всегда
    with _LOCK:
        conn = _conn()
        acl = _acl(conn, vault_id)
        if role:
            acl[p] = _check_role(role)
        else:
            acl.pop(p, None)
        if "owner" not in acl.values() and not org_owners:
            raise OrgError("У сейфа должен остаться владелец", 400)
        if role:
            conn.execute("INSERT OR REPLACE INTO vault_acl (vault_id, principal, role) VALUES (?,?,?)",
                         (int(vault_id), p, acl[p]))
        else:
            conn.execute("DELETE FROM vault_acl WHERE vault_id = ? AND principal = ?", (int(vault_id), p))
        conn.commit()
    return access(vault_id, user_id)


def _check_writable(vault_id: int) -> None:
    """Пока ключ сейфа не сменён после ухода участника, старым ключом не шифруем (совет аудитора 2026-11-10).

    Смену делает браузер редактора при открытии сейфов — клиент запускает её перед записью сам.
    """
    _sync_envelopes(vault_id)  # уход мог случиться только что
    with _LOCK:
        v = _vault(_conn(), vault_id)
    if int(v["needs_rotation"] or 0):
        raise OrgError("Сначала сменится ключ сейфа после ухода участника — откройте сейф заново", 400)


def _check_version(conn: sqlite3.Connection, vault_id: int, key_version: Optional[int]) -> None:
    """Браузер шифрует ключом сейфа версии ``key_version``; если ключ уже сменили — пусть откроет заново."""
    if key_version is None:
        return
    v = _vault(conn, vault_id)
    if int(v["key_version"]) != int(key_version):
        raise OrgError("Ключ сейфа только что сменился — откройте сейф заново", 400)


def put_envelopes(user_id: int | str, vault_id: int, envelopes: list[dict[str, str]],
                  key_version: Optional[int] = None) -> dict[str, Any]:
    """Положить конверты тем, кто ждёт. Только владелец сейфа и только по его решению («Выдать»).

    Раньше ключ выдавал браузер любого участника сам — тогда админ, добавивший себя в команду,
    получал ключ без ведома владельца (аудит 2026-11-10).
    """
    uid = _uid(user_id)
    _require(vault_id, uid, "owner")
    _check_writable(vault_id)
    members, have = _sync_envelopes(vault_id)
    if uid not in have:
        raise OrgError("Сначала откройте сейф у себя", 400)
    with _LOCK:
        conn = _conn()
        _check_version(conn, vault_id, key_version)
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
        "id": int(r["id"]), "uid": r["uid"], "blob": r["blob"], "created_at": r["created_at"], "updated_at": r["updated_at"],
        "updated_by": str(r["updated_by"]), "updated_by_name": name(str(r["updated_by"])),
    } for r in rows]


def add_item(user_id: int | str, vault_id: int, blob: str, key_version: Optional[int] = None,
             item_uid: str = "") -> dict[str, Any]:
    _require(vault_id, user_id, "editor")
    _check_writable(vault_id)
    uid = _uid(user_id)
    data = _blob(blob, "blob", BLOB_MAX)
    now = _now()
    with _LOCK:
        conn = _conn()
        _check_version(conn, vault_id, key_version)
        iuid = ""
        if int(_vault(conn, vault_id)["fmt"] or 1) >= 2:
            iuid = _check_uid(item_uid, "uid")
            if conn.execute("SELECT 1 FROM vault_items WHERE vault_id = ? AND uid = ?", (int(vault_id), iuid)).fetchone():
                raise OrgError("Такая запись уже есть — обновите страницу", 400)
        cur = conn.execute(
            "INSERT INTO vault_items (vault_id, blob, created_by, created_at, updated_by, updated_at, uid) VALUES (?,?,?,?,?,?,?)",
            (int(vault_id), data, uid, now, uid, now, iuid),
        )
        conn.commit()
        iid = int(cur.lastrowid)
    return {"id": iid, "uid": iuid, "updated_at": now}


def _item(conn: sqlite3.Connection, vault_id: int, item_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM vault_items WHERE id = ? AND vault_id = ?", (int(item_id), int(vault_id))
    ).fetchone()
    if not row:
        raise OrgError("Запись не найдена", 404)
    return row


def update_item(user_id: int | str, vault_id: int, item_id: int, blob: str,
                key_version: Optional[int] = None) -> dict[str, Any]:
    _require(vault_id, user_id, "editor")
    _check_writable(vault_id)
    data = _blob(blob, "blob", BLOB_MAX)
    now = _now()
    with _LOCK:
        conn = _conn()
        _check_version(conn, vault_id, key_version)
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


# ---------- смена ключа сейфа ----------


def rotate(user_id: int | str, vault_id: int, key_version: int, items_in: list[dict[str, Any]],
           envelopes: list[dict[str, str]], fmt: int = 1, vault_uid: str = "") -> dict[str, Any]:
    """Новый ключ сейфа: все записи перешифрованы, конверты — заново. Делает браузер участника.

    Нужно после ухода человека: старый ключ мог остаться у него. Записи меняются целиком и разом;
    дата «изменена» не трогается — смена ключа не значит, что сменили пароль.
    """
    uid = _uid(user_id)
    v, _ = _require(vault_id, uid, "editor")
    members, have = _sync_envelopes(vault_id)
    if uid not in have:
        raise OrgError("Сначала откройте сейф у себя", 400)
    blobs: dict[int, str] = {}
    uids: dict[int, str] = {}
    for it in items_in or []:
        blobs[int(it.get("id") or 0)] = _blob(it.get("blob"), "blob", BLOB_MAX)
        if int(fmt or 1) >= 2:
            uids[int(it.get("id") or 0)] = _check_uid(it.get("uid"), "uid")
    if len(set(uids.values())) != len(uids):
        raise OrgError("Номера записей повторяются — попробуйте ещё раз", 400)
    with _LOCK:
        conn = _conn()
        _check_version(conn, vault_id, key_version)
        cur_v = _vault(conn, vault_id)
        cur_fmt = int(cur_v["fmt"] or 1)
        new_fmt = max(cur_fmt, int(fmt or 1))
        if new_fmt > 2 or int(fmt or 1) < cur_fmt:
            raise OrgError("Формат сейфа не подходит — обновите страницу", 400)  # назад на старый формат нельзя
        new_vuid = cur_v["uid"] or ""
        if new_fmt >= 2:
            new_vuid = new_vuid or _check_uid(vault_uid, "uid")
            if vault_uid and vault_uid != new_vuid:
                raise OrgError("Номер сейфа не совпадает — обновите страницу", 400)
        ids = {int(r["id"]) for r in conn.execute("SELECT id FROM vault_items WHERE vault_id = ?", (int(vault_id),))}
        if ids != set(blobs):
            raise OrgError("Записи в сейфе только что изменились — попробуйте ещё раз", 400)
        keys = _public_keys(conn, members & have)  # новый ключ — только тем, у кого ключ уже был
        envs: dict[str, str] = {}
        for e in envelopes or []:
            target = _uid(e.get("user_id") or 0)
            if target in keys:
                envs[target] = _blob(e.get("envelope"), "envelope")
        if uid not in envs or not (have & set(keys)) <= set(envs):
            raise OrgError("Не хватает ключей для участников — попробуйте ещё раз", 400)
        now = _now()
        for iid, blob in blobs.items():
            if new_fmt >= 2:
                conn.execute("UPDATE vault_items SET blob = ?, uid = ? WHERE id = ?", (blob, uids[iid], iid))
            else:
                conn.execute("UPDATE vault_items SET blob = ? WHERE id = ?", (blob, iid))
        conn.execute("DELETE FROM vault_envelopes WHERE vault_id = ?", (int(vault_id),))
        for target, env in envs.items():
            conn.execute(
                "INSERT INTO vault_envelopes (vault_id, user_id, envelope, created_by, created_at) VALUES (?,?,?,?,?)",
                (int(vault_id), target, env, uid, now),
            )
        conn.execute(
            "UPDATE vaults SET key_version = key_version + 1, needs_rotation = 0, fmt = ?, uid = ? WHERE id = ?",
            (new_fmt, new_vuid, int(vault_id)),
        )
        conn.commit()
    return get(vault_id, uid)


def dismiss_departures(user_id: int | str, vault_id: int) -> dict[str, Any]:
    """«Всё сменили» — убрать список «смените пароли»."""
    _require(vault_id, user_id, "editor")
    with _LOCK:
        conn = _conn()
        conn.execute("UPDATE vault_departures SET dismissed = 1 WHERE vault_id = ?", (int(vault_id),))
        conn.commit()
    return get(vault_id, user_id)


# ---------- запрос доступа ----------


def _vault_log_event(conn: sqlite3.Connection, vault_id: int, user_id: str, action: str) -> None:
    conn.execute(
        "INSERT INTO vault_log (vault_id, item_id, user_id, action, at) VALUES (?,?,?,?,?)",
        (int(vault_id), None, user_id, action, _now()),
    )


def request_access(user_id: int | str, vault_id: int) -> None:
    """Участник организации просит доступ к сейфу — владельцы сейфа получат уведомление."""
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        v = _vault(conn, vault_id)
        acl = _acl(conn, vault_id)
    if v["org_id"] is None:
        raise OrgError("Сейф не найден", 404)
    if _role_for(v, acl, uid):
        raise OrgError("Этот сейф вам уже открыт", 400)
    with _LOCK:
        conn = _conn()
        m = conn.execute(
            "SELECT guest FROM org_members WHERE org_id = ? AND user_id = ? AND status = 'active'", (int(v["org_id"]), uid)
        ).fetchone()
        if not m or m["guest"]:
            raise OrgError("Сейф не найден", 404)
        if conn.execute(
            "SELECT 1 FROM vault_requests WHERE vault_id = ? AND user_id = ? AND status = 'pending'", (int(vault_id), uid)
        ).fetchone():
            raise OrgError("Запрос уже отправлен", 400)
        conn.execute(
            "INSERT INTO vault_requests (vault_id, user_id, status, created_at) VALUES (?,?,?,?)",
            (int(vault_id), uid, "pending", _now()),
        )
        _vault_log_event(conn, vault_id, uid, "access_request")
        conn.commit()


def cancel_request(user_id: int | str, vault_id: int) -> None:
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE vault_requests SET status = 'cancelled', decided_by = ?, decided_at = ?, notified = 2 "
            "WHERE vault_id = ? AND user_id = ? AND status = 'pending'",
            (uid, _now(), int(vault_id), uid),
        )
        conn.commit()


def decide_request(user_id: int | str, vault_id: int, target: int | str, role: str) -> dict[str, Any]:
    """Владелец сейфа отвечает на запрос: ``role`` — открыть с этой ролью, пусто — отклонить."""
    uid, who = _uid(user_id), _uid(target)
    v, _ = _require(vault_id, uid, "owner")
    with _LOCK:
        conn = _conn()
        r = conn.execute(
            "SELECT id FROM vault_requests WHERE vault_id = ? AND user_id = ? AND status = 'pending'", (int(vault_id), who)
        ).fetchone()
    if not r:
        raise OrgError("Запроса уже нет", 400)
    if role:
        set_access(uid, vault_id, f"u:{who}", _check_role(role))
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE vault_requests SET status = ?, decided_by = ?, decided_at = ?, role = ? WHERE id = ?",
            ("approved" if role else "rejected", uid, _now(), role or None, int(r["id"])),
        )
        _vault_log_event(conn, vault_id, uid, "access_grant" if role else "access_reject")
        conn.commit()
    return get(vault_id, uid)


def vault_events(vault_id: int, user_id: int | str, limit: int = 20) -> list[dict[str, Any]]:
    """События сейфа целиком (запросы доступа) — для участников."""
    _require(vault_id, user_id, "viewer")
    with _LOCK:
        rows = _conn().execute(
            "SELECT user_id, action, at FROM vault_log WHERE vault_id = ? AND item_id IS NULL ORDER BY at DESC, id DESC LIMIT ?",
            (int(vault_id), int(limit)),
        ).fetchall()
    names = _names({str(r["user_id"]) for r in rows})
    return [{"user_id": str(r["user_id"]), "name": names[str(r["user_id"])], "action": r["action"], "at": r["at"]} for r in rows]


# ---------- фоновая проверка: уходы и запросы доступа → уведомления ----------


def watch() -> list[dict[str, Any]]:
    """Раз в несколько минут (assistant/bot/vault_watch_job.py): заметить уходы и вернуть, кого о чём
    уведомить — уходы, новые запросы доступа, ответы на них. В уведомлении только название сейфа и имена,
    содержимое сейфа сюда не попадает. Каждое событие возвращается один раз."""
    with _LOCK:
        ids = [int(r["id"]) for r in _conn().execute("SELECT id FROM vaults WHERE org_id IS NOT NULL")]
    for vid in ids:
        try:
            _sync_envelopes(vid)
        except OrgError:
            continue
    events: list[dict[str, Any]] = []
    with _LOCK:
        conn = _conn()
        vaults_by_id = {int(r["id"]): r for r in conn.execute("SELECT * FROM vaults WHERE org_id IS NOT NULL")}
        deps = conn.execute("SELECT * FROM vault_departures WHERE notified = 0 AND dismissed = 0").fetchall()
        reqs = conn.execute(
            "SELECT * FROM vault_requests WHERE (status = 'pending' AND notified = 0) "
            "OR (status IN ('approved', 'rejected') AND notified < 2)"
        ).fetchall()
    acl_cache: dict[int, dict[str, str]] = {}

    def people(vid: int, roles: tuple[str, ...]) -> set[str]:
        v = vaults_by_id[vid]
        if vid not in acl_cache:
            with _LOCK:
                acl_cache[vid] = _acl(_conn(), vid)
        return {u for u in _members(v["org_id"], acl_cache[vid]) if _role_for(v, acl_cache[vid], u) in roles}

    for d in deps:
        vid = int(d["vault_id"])
        if vid in vaults_by_id:
            events.append({
                "kind": "departure", "vault_id": vid, "vault_name": vaults_by_id[vid]["name"],
                "who": str(d["user_id"]), "to": sorted(people(vid, ("editor", "owner")) - {str(d["user_id"])}),
            })
    for r in reqs:
        vid = int(r["vault_id"])
        if vid not in vaults_by_id:
            continue
        who = str(r["user_id"])
        if r["status"] == "pending":
            kind, to = "access_request", people(vid, ("owner",)) - {who}
        else:
            kind, to = ("access_approved" if r["status"] == "approved" else "access_rejected"), {who}
        events.append({
            "kind": kind, "vault_id": vid, "vault_name": vaults_by_id[vid]["name"], "who": who,
            "by": str(r["decided_by"] or ""), "role": r["role"] or "", "to": sorted(to),
        })
    with _LOCK:
        conn = _conn()
        for d in deps:
            conn.execute(
                "UPDATE vault_departures SET notified = 1 WHERE vault_id = ? AND user_id = ?", (d["vault_id"], d["user_id"])
            )
        for r in reqs:
            conn.execute(
                "UPDATE vault_requests SET notified = ? WHERE id = ?", (1 if r["status"] == "pending" else 2, int(r["id"]))
            )
        conn.commit()
    names = _names({x for ev in events for x in (ev["who"], ev.get("by") or "") if x})
    for ev in events:
        ev["who_name"] = names.get(ev["who"], "")
        ev["by_name"] = names.get(ev.get("by") or "", "")
    return events + link_events()
