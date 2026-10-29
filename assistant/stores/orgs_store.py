"""Организации: общее пространство команды (участники, роли, домен почты, приглашения).

В отличие от ``teams_store`` (личные группы контактов владельца) организация одна на всех
её участников: у каждого есть роль, вступить можно по домену подтверждённой почты,
по ссылке-приглашению или админ добавляет сам.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

ROLES = ("owner", "admin", "member")
JOIN_MODES = ("auto", "request")
INVITE_DAYS = 14

# Тарифы. Оплата ещё не подключена: значения хранятся и показываются, но ни на что не влияют.
PLANS = {"free": "Бесплатный", "pro": "Про", "business": "Бизнес"}

PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yandex.ru", "ya.ru", "yandex.com", "mail.ru", "inbox.ru", "list.ru",
    "bk.ru", "internet.ru", "outlook.com", "hotmail.com", "live.com", "icloud.com", "me.com", "mac.com",
    "rambler.ru", "proton.me", "protonmail.com", "yahoo.com", "aol.com", "gmx.com", "tut.by",
})

# Что может каждая роль. Владелец может всё, что админ, и ещё своё.
_ADMIN_ACTIONS = frozenset({"invite", "remove_member", "approve", "edit_digest"})
_OWNER_ACTIONS = _ADMIN_ACTIONS | frozenset({"edit_org", "set_role", "delete_org", "billing"})

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,38}[a-z0-9])$")
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
})


class OrgError(ValueError):
    """Ошибка с текстом для пользователя; ``code`` — HTTP-статус.

    Нехватку прав отдаём 400, а не 403: на 403 миниапп показывает экран «Доступ ограничен».
    """

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    # executescript сам делает COMMIT — запускаем его только на свежей базе, а не посреди транзакции.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'org_dismissed'").fetchone():
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS orgs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            logo_url TEXT NOT NULL DEFAULT '',
            join_mode TEXT NOT NULL DEFAULT 'request',
            plan TEXT NOT NULL DEFAULT 'free',
            seats INTEGER,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS org_domains (
            domain TEXT PRIMARY KEY,
            org_id INTEGER NOT NULL,
            verified_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_org_domains_org ON org_domains(org_id);
        CREATE TABLE IF NOT EXISTS org_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id INTEGER NOT NULL,
            user_id TEXT,
            invited_key TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT 'member',
            status TEXT NOT NULL DEFAULT 'active',
            plan TEXT NOT NULL DEFAULT 'free',
            added_by TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_org_members_user
            ON org_members(org_id, user_id) WHERE user_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_org_members_invited
            ON org_members(org_id, invited_key) WHERE user_id IS NULL;
        CREATE INDEX IF NOT EXISTS idx_org_members_uid ON org_members(user_id);
        CREATE TABLE IF NOT EXISTS org_invites (
            token TEXT PRIMARY KEY,
            org_id INTEGER NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked_at TEXT
        );
        CREATE TABLE IF NOT EXISTS org_settings (
            org_id INTEGER PRIMARY KEY,
            json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS org_dismissed (
            user_id TEXT NOT NULL,
            org_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, org_id)
        );
        """
    )
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now().isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


# ---------- личность пользователя: подтверждённые адреса и домены ----------


def verified_emails(user_id: int | str) -> list[str]:
    """Адреса, которыми пользователь владеет (вошёл через OAuth или IMAP)."""
    out: list[str] = []
    try:
        from assistant.stores import user_prefs

        em = str(user_prefs.load_prefs(int(user_id)).get("google_account_email") or "").strip().lower()
        if "@" in em:
            out.append(em)
    except Exception:
        pass
    try:
        from assistant.stores import mail_accounts

        for acc in mail_accounts.list_accounts(int(user_id)):
            em = str(acc.get("email") or "").strip().lower()
            if "@" in em and em not in out:
                out.append(em)
    except Exception:
        pass
    return out


def domain_of(email: str) -> str:
    return str(email or "").rsplit("@", 1)[-1].strip().lower() if "@" in str(email or "") else ""


def user_domains(user_id: int | str) -> list[str]:
    """Корпоративные домены подтверждённой почты (без gmail, yandex и т. п.)."""
    out: list[str] = []
    for em in verified_emails(user_id):
        d = domain_of(em)
        if d and d not in PUBLIC_DOMAINS and d not in out:
            out.append(d)
    return out


def _username(user_id: int | str) -> str:
    try:
        from assistant.stores import note_members

        prof = note_members.get_profile(user_id) or {}
        return str(prof.get("username") or "").strip().lstrip("@").lower()
    except Exception:
        return ""


# ---------- slug ----------


def slugify(text: str) -> str:
    s = str(text or "").strip().lower().translate(_TRANSLIT)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40].strip("-")


def check_slug(slug: str, *, exclude_org_id: int | None = None) -> str:
    """'' — свободен, иначе причина, почему нельзя."""
    s = str(slug or "").strip().lower()
    if not _SLUG_RE.match(s):
        return "Только латиница, цифры и дефис, от 3 до 40 символов"
    with _LOCK:
        row = _conn().execute("SELECT id FROM orgs WHERE slug = ?", (s,)).fetchone()
    if row and int(row["id"]) != (exclude_org_id or 0):
        return "Адрес уже занят"
    return ""


def _free_slug(base: str) -> str:
    base = slugify(base) or "org"
    if len(base) < 3:
        base = (base + "-org")[:40]
    cand = base
    n = 2
    while check_slug(cand):
        cand = f"{base[:36]}-{n}"
        n += 1
    return cand


# ---------- чтение ----------


def _org_dict(row: sqlite3.Row) -> dict[str, Any]:
    oid = int(row["id"])
    domains = [r["domain"] for r in _conn().execute(
        "SELECT domain FROM org_domains WHERE org_id = ? ORDER BY created_at", (oid,)
    ).fetchall()]
    count = _conn().execute(
        "SELECT COUNT(*) FROM org_members WHERE org_id = ? AND status = 'active'", (oid,)
    ).fetchone()[0]
    return {
        "id": oid,
        "slug": str(row["slug"]),
        "name": str(row["name"]),
        "logo_url": str(row["logo_url"] or ""),
        "join_mode": str(row["join_mode"]),
        "plan": str(row["plan"]),
        "seats": int(row["seats"] or 0),
        "seats_used": int(_conn().execute(
            "SELECT COUNT(*) FROM org_members WHERE org_id = ? AND plan != 'free' AND status != 'pending'", (oid,)
        ).fetchone()[0]),
        "domains": domains,
        "member_count": int(count),
        "created_at": row["created_at"],
    }


def get_org(org_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT * FROM orgs WHERE id = ?", (int(org_id),)).fetchone()
        return _org_dict(row) if row else None


def get_org_by_slug(slug: str) -> Optional[dict[str, Any]]:
    with _LOCK:
        row = _conn().execute("SELECT * FROM orgs WHERE slug = ?", (str(slug or "").lower(),)).fetchone()
        return _org_dict(row) if row else None


def _membership(org_id: int, user_id: int | str) -> Optional[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM org_members WHERE org_id = ? AND user_id = ?", (int(org_id), _uid(user_id))
    ).fetchone()


def role_of(org_id: int, user_id: int | str) -> str:
    """Роль активного участника или ''."""
    with _LOCK:
        m = _membership(org_id, user_id)
    if not m or m["status"] != "active":
        return ""
    return str(m["role"])


def can(user_id: int | str, org_id: int, action: str) -> bool:
    role = role_of(org_id, user_id)
    if role == "owner":
        return action in _OWNER_ACTIONS or action == "view"
    if role == "admin":
        return action in _ADMIN_ACTIONS or action == "view"
    return role == "member" and action == "view"


def _require(user_id: int | str, org_id: int, action: str) -> None:
    if not get_org(org_id):
        raise OrgError("Организация не найдена", 404)
    if not can(user_id, org_id, action):
        raise OrgError("Недостаточно прав", 400)


def orgs_for_user(user_id: int | str) -> list[dict[str, Any]]:
    """Организации, где пользователь — активный участник, с его ролью."""
    with _LOCK:
        rows = _conn().execute(
            """
            SELECT o.*, m.role AS my_role FROM org_members m JOIN orgs o ON o.id = m.org_id
            WHERE m.user_id = ? AND m.status = 'active' ORDER BY m.added_at, o.id
            """,
            (_uid(user_id),),
        ).fetchall()
        return [{**_org_dict(r), "my_role": str(r["my_role"])} for r in rows]


def current_org(user_id: int | str) -> Optional[dict[str, Any]]:
    """Текущая организация: выбранная в prefs, иначе первая."""
    items = orgs_for_user(user_id)
    if not items:
        return None
    try:
        from assistant.stores import user_prefs

        want = int(user_prefs.load_prefs(int(user_id)).get("current_org_id") or 0)
    except Exception:
        want = 0
    return next((o for o in items if o["id"] == want), items[0])


def set_current_org(user_id: int | str, org_id: int) -> None:
    from assistant.stores import user_prefs

    if not role_of(org_id, user_id):
        raise OrgError("Вы не участник этой организации", 400)
    prefs = user_prefs.load_prefs(int(user_id))
    prefs["current_org_id"] = int(org_id)
    user_prefs.save_prefs(int(user_id), prefs)


def list_members(org_id: int) -> list[dict[str, Any]]:
    from assistant.stores import note_members

    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM org_members WHERE org_id = ? ORDER BY "
            "CASE role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, added_at, id",
            (int(org_id),),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        uid = r["user_id"]
        prof = note_members.get_profile(uid) if uid else None
        out.append({
            "id": int(r["id"]),
            "user_id": str(uid) if uid else None,
            "invited_key": str(r["invited_key"] or ""),
            "name": note_members.profile_display_name(prof, fallback=str(r["invited_key"] or "")) if uid
            else str(r["invited_key"] or ""),
            "username": (prof or {}).get("username"),
            "role": str(r["role"]),
            "status": str(r["status"]),
            "plan": str(r["plan"]),
            "added_at": r["added_at"],
        })
    return out


def member_ids(org_id: int) -> list[int]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT user_id FROM org_members WHERE org_id = ? AND status = 'active' AND user_id IS NOT NULL",
            (int(org_id),),
        ).fetchall()
    return [int(r["user_id"]) for r in rows]


def all_org_ids() -> list[int]:
    with _LOCK:
        return [int(r["id"]) for r in _conn().execute("SELECT id FROM orgs ORDER BY id").fetchall()]


# ---------- создание и правка ----------


def create_org(user_id: int | str, name: str, *, slug: str = "", domain: str = "") -> dict[str, Any]:
    uid = _uid(user_id)
    name = str(name or "").strip()[:80]
    if not name:
        raise OrgError("Укажите название")
    domain = str(domain or "").strip().lower().lstrip("@")
    if domain:
        if domain in PUBLIC_DOMAINS:
            raise OrgError("Публичный почтовый домен нельзя привязать к организации")
        if domain not in user_domains(uid):
            raise OrgError("Домен должен совпадать с вашей подключённой почтой")
    if slug:
        err = check_slug(slug)
        if err:
            raise OrgError(err)
        slug = slug.strip().lower()
    else:
        slug = _free_slug(name if slugify(name) else domain.split(".")[0])
    now = _now_iso()
    with _LOCK:
        conn = _conn()
        if domain and conn.execute("SELECT 1 FROM org_domains WHERE domain = ?", (domain,)).fetchone():
            raise OrgError("Этот домен уже привязан к другой организации — попросите там приглашение", 409)
        cur = conn.execute(
            "INSERT INTO orgs (slug, name, created_by, created_at) VALUES (?, ?, ?, ?)", (slug, name, uid, now)
        )
        oid = int(cur.lastrowid)
        conn.execute(
            "INSERT INTO org_members (org_id, user_id, role, status, added_by, added_at) "
            "VALUES (?, ?, 'owner', 'active', ?, ?)",
            (oid, uid, uid, now),
        )
        if domain:
            conn.execute(
                "INSERT INTO org_domains (domain, org_id, verified_by, created_at) VALUES (?, ?, ?, ?)",
                (domain, oid, uid, now),
            )
        conn.commit()
    return get_org(oid) or {}


def update_org(user_id: int | str, org_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    org = get_org(org_id)
    if not org:
        raise OrgError("Организация не найдена", 404)
    sets: dict[str, Any] = {}
    if "name" in patch or "slug" in patch or "logo_url" in patch:
        _require(user_id, org_id, "edit_org")
    if "join_mode" in patch:
        _require(user_id, org_id, "invite")
    if "name" in patch:
        name = str(patch.get("name") or "").strip()[:80]
        if not name:
            raise OrgError("Укажите название")
        sets["name"] = name
    if "slug" in patch:
        slug = str(patch.get("slug") or "").strip().lower()
        err = check_slug(slug, exclude_org_id=int(org_id))
        if err:
            raise OrgError(err)
        sets["slug"] = slug
    if "logo_url" in patch:
        sets["logo_url"] = str(patch.get("logo_url") or "")[:500]
    if "join_mode" in patch:
        mode = str(patch.get("join_mode") or "")
        if mode not in JOIN_MODES:
            raise OrgError("Неизвестный режим вступления")
        sets["join_mode"] = mode
    if sets:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE orgs SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                (*sets.values(), int(org_id)),
            )
            conn.commit()
    return get_org(org_id) or {}


def add_domain(user_id: int | str, org_id: int, domain: str) -> dict[str, Any]:
    _require(user_id, org_id, "edit_org")
    domain = str(domain or "").strip().lower().lstrip("@")
    if not domain or domain in PUBLIC_DOMAINS:
        raise OrgError("Публичный почтовый домен нельзя привязать к организации")
    if domain not in user_domains(user_id):
        raise OrgError("Домен должен совпадать с вашей подключённой почтой")
    with _LOCK:
        conn = _conn()
        row = conn.execute("SELECT org_id FROM org_domains WHERE domain = ?", (domain,)).fetchone()
        if row and int(row["org_id"]) != int(org_id):
            raise OrgError("Этот домен уже привязан к другой организации", 409)
        if not row:
            conn.execute(
                "INSERT INTO org_domains (domain, org_id, verified_by, created_at) VALUES (?, ?, ?, ?)",
                (domain, int(org_id), _uid(user_id), _now_iso()),
            )
            conn.commit()
    return get_org(org_id) or {}


def remove_domain(user_id: int | str, org_id: int, domain: str) -> dict[str, Any]:
    _require(user_id, org_id, "edit_org")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "DELETE FROM org_domains WHERE domain = ? AND org_id = ?",
            (str(domain or "").strip().lower(), int(org_id)),
        )
        conn.commit()
    return get_org(org_id) or {}


def delete_org(user_id: int | str, org_id: int) -> None:
    _require(user_id, org_id, "delete_org")
    with _LOCK:
        conn = _conn()
        for table in ("org_members", "org_domains", "org_invites", "org_settings", "org_dismissed"):
            conn.execute(f"DELETE FROM {table} WHERE org_id = ?", (int(org_id),))
        conn.execute("DELETE FROM orgs WHERE id = ?", (int(org_id),))
        conn.commit()


# ---------- участники ----------


def _insert_member(org_id: int, user_id: str | None, *, key: str = "", role: str = "member",
                   status: str = "active", added_by: str = "") -> None:
    _conn().execute(
        "INSERT INTO org_members (org_id, user_id, invited_key, role, status, added_by, added_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(org_id), user_id, key, role, status, added_by, _now_iso()),
    )


def _resolve_key(actor_id: int | str, key: str) -> Optional[int]:
    """email / @ник → id пользователя Орбиты, если он уже есть."""
    if key.startswith("tg:"):
        from assistant.stores import telegram_registry

        return telegram_registry.lookup_user_id(key[3:])
    try:
        from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

        found = lookup_user_id_by_calendar_email(key)
        if found:
            return int(found)
    except Exception:
        pass
    try:
        from assistant.stores import note_members

        found, _ = note_members.resolve_contact_telegram_id(owner_user_id=actor_id, email=key)
        return int(found)
    except Exception:
        return None


def normalize_member_key(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    if "@" in s and not s.startswith("@"):
        return s if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", s) else ""
    nick = s.lstrip("@").removeprefix("tg:").removeprefix("https://t.me/").removeprefix("t.me/")
    return "tg:" + nick if re.match(r"^[a-z0-9_]{4,32}$", nick) else ""


def add_member(actor_id: int | str, org_id: int, raw_key: str, *, role: str = "member") -> dict[str, Any]:
    """Админ добавляет человека по email или @нику. Кого нет в Орбите — ждёт первого входа."""
    _require(actor_id, org_id, "invite")
    if role not in ("admin", "member"):
        raise OrgError("Неизвестная роль")
    if role == "admin" and not can(actor_id, org_id, "set_role"):
        raise OrgError("Назначать админов может только владелец", 400)
    key = normalize_member_key(raw_key)
    if not key:
        raise OrgError("Укажите email или @ник в Telegram")
    target = _resolve_key(actor_id, key)
    with _LOCK:
        conn = _conn()
        if target:
            m = _membership(org_id, target)
            if m and m["status"] == "active":
                raise OrgError("Уже в организации", 409)
            if m:
                conn.execute("UPDATE org_members SET status = 'active', role = ? WHERE id = ?", (role, m["id"]))
            else:
                _insert_member(org_id, _uid(target), key=key, role=role, added_by=_uid(actor_id))
        else:
            if conn.execute(
                "SELECT 1 FROM org_members WHERE org_id = ? AND user_id IS NULL AND invited_key = ?",
                (int(org_id), key),
            ).fetchone():
                raise OrgError("Уже приглашён", 409)
            _insert_member(org_id, None, key=key, role=role, status="invited", added_by=_uid(actor_id))
        conn.commit()
    return {"status": "active" if target else "invited", "user_id": str(target) if target else None, "key": key}


def claim_pending(user_id: int | str) -> list[int]:
    """При входе: приглашения на email/@ник пользователя превращаются в членство."""
    uid = _uid(user_id)
    keys = list(verified_emails(uid))
    uname = _username(uid)
    if uname:
        keys.append("tg:" + uname)
    if not keys:
        return []
    joined: list[int] = []
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            f"SELECT * FROM org_members WHERE user_id IS NULL AND invited_key IN ({','.join('?' * len(keys))})",
            keys,
        ).fetchall()
        for r in rows:
            if _membership(r["org_id"], uid):
                conn.execute("DELETE FROM org_members WHERE id = ?", (r["id"],))
                continue
            conn.execute("UPDATE org_members SET user_id = ?, status = 'active' WHERE id = ?", (uid, r["id"]))
            joined.append(int(r["org_id"]))
        conn.commit()
    return joined


def suggestions(user_id: int | str) -> list[dict[str, Any]]:
    """Организации по домену почты, куда пользователь ещё не вступил и не отказался."""
    uid = _uid(user_id)
    domains = user_domains(uid)
    if not domains:
        return []
    with _LOCK:
        conn = _conn()
        rows = conn.execute(
            f"""
            SELECT DISTINCT o.* FROM org_domains d JOIN orgs o ON o.id = d.org_id
            WHERE d.domain IN ({','.join('?' * len(domains))})
              AND o.id NOT IN (SELECT org_id FROM org_dismissed WHERE user_id = ?)
            """,
            (*domains, uid),
        ).fetchall()
        out = []
        for r in rows:
            m = _membership(r["id"], uid)
            if m and m["status"] == "active":
                continue
            out.append({**_org_dict(r), "pending": bool(m and m["status"] == "pending")})
        return out


def dismiss_suggestion(user_id: int | str, org_id: int) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("INSERT OR IGNORE INTO org_dismissed (user_id, org_id) VALUES (?, ?)", (_uid(user_id), int(org_id)))
        conn.commit()


def join_by_domain(user_id: int | str, org_id: int) -> str:
    """Вступить по домену почты. Возвращает 'active' или 'pending' (ждёт одобрения админа)."""
    uid = _uid(user_id)
    org = get_org(org_id)
    if not org:
        raise OrgError("Организация не найдена", 404)
    if not set(org["domains"]) & set(user_domains(uid)):
        raise OrgError("Ваша почта не на домене этой организации", 400)
    status = "active" if org["join_mode"] == "auto" else "pending"
    with _LOCK:
        conn = _conn()
        m = _membership(org_id, uid)
        if m and m["status"] == "active":
            return "active"
        if m:
            conn.execute("UPDATE org_members SET status = ? WHERE id = ?", (status, m["id"]))
        else:
            _insert_member(org_id, uid, status=status, added_by=uid)
        conn.commit()
    return status


def approve(actor_id: int | str, org_id: int, target_id: int | str, *, allow: bool = True) -> None:
    _require(actor_id, org_id, "approve")
    with _LOCK:
        conn = _conn()
        m = _membership(org_id, target_id)
        if not m or m["status"] != "pending":
            raise OrgError("Заявка не найдена", 404)
        if allow:
            conn.execute("UPDATE org_members SET status = 'active' WHERE id = ?", (m["id"],))
        else:
            conn.execute("DELETE FROM org_members WHERE id = ?", (m["id"],))
        conn.commit()


def _owner_count(org_id: int) -> int:
    return int(_conn().execute(
        "SELECT COUNT(*) FROM org_members WHERE org_id = ? AND role = 'owner' AND status = 'active'", (int(org_id),)
    ).fetchone()[0])


def set_role(actor_id: int | str, org_id: int, target_id: int | str, role: str) -> None:
    """Роли меняет владелец. Назначить другого владельцем — передать владение (сам станет админом)."""
    _require(actor_id, org_id, "set_role")
    if role not in ROLES:
        raise OrgError("Неизвестная роль")
    actor, target = _uid(actor_id), _uid(target_id)
    with _LOCK:
        conn = _conn()
        m = _membership(org_id, target)
        if not m or m["status"] != "active":
            raise OrgError("Участник не найден", 404)
        if target == actor and role != "owner" and _owner_count(org_id) <= 1:
            raise OrgError("Сначала передайте владение другому участнику")
        conn.execute("UPDATE org_members SET role = ? WHERE id = ?", (role, m["id"]))
        if role == "owner" and target != actor:
            conn.execute(
                "UPDATE org_members SET role = 'admin' WHERE org_id = ? AND user_id = ?", (int(org_id), actor)
            )
        conn.commit()


def remove_member(actor_id: int | str, org_id: int, member_row_id: int) -> None:
    """Исключить участника или отозвать приглашение. Владельца исключить нельзя, админа — только владелец."""
    _require(actor_id, org_id, "remove_member")
    actor_role = role_of(org_id, actor_id)
    with _LOCK:
        conn = _conn()
        m = conn.execute(
            "SELECT * FROM org_members WHERE id = ? AND org_id = ?", (int(member_row_id), int(org_id))
        ).fetchone()
        if not m:
            raise OrgError("Участник не найден", 404)
        if m["role"] == "owner":
            raise OrgError("Владельца исключить нельзя — сначала передайте владение", 400)
        if m["role"] == "admin" and actor_role != "owner":
            raise OrgError("Админа может исключить только владелец", 400)
        conn.execute("DELETE FROM org_members WHERE id = ?", (m["id"],))
        conn.commit()


def leave(user_id: int | str, org_id: int) -> None:
    with _LOCK:
        conn = _conn()
        m = _membership(org_id, user_id)
        if not m:
            raise OrgError("Вы не участник этой организации", 404)
        if m["role"] == "owner" and _owner_count(org_id) <= 1:
            raise OrgError("Вы единственный владелец — передайте владение или удалите организацию")
        conn.execute("DELETE FROM org_members WHERE id = ?", (m["id"],))
        conn.execute("INSERT OR IGNORE INTO org_dismissed (user_id, org_id) VALUES (?, ?)", (_uid(user_id), int(org_id)))
        conn.commit()


# ---------- ссылки-приглашения ----------


def create_invite(actor_id: int | str, org_id: int, *, days: int = INVITE_DAYS) -> dict[str, Any]:
    _require(actor_id, org_id, "invite")
    token = secrets.token_urlsafe(12)
    now = _now()
    expires = now + timedelta(days=max(1, min(int(days), 90)))
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO org_invites (token, org_id, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (token, int(org_id), _uid(actor_id), now.isoformat(), expires.isoformat()),
        )
        conn.commit()
    return {"token": token, "expires_at": expires.isoformat()}


def list_invites(actor_id: int | str, org_id: int) -> list[dict[str, Any]]:
    _require(actor_id, org_id, "invite")
    now = _now_iso()
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM org_invites WHERE org_id = ? AND revoked_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
            (int(org_id), now),
        ).fetchall()
    return [{"token": r["token"], "expires_at": r["expires_at"], "created_at": r["created_at"]} for r in rows]


def revoke_invite(actor_id: int | str, org_id: int, token: str) -> None:
    _require(actor_id, org_id, "invite")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE org_invites SET revoked_at = ? WHERE token = ? AND org_id = ?", (_now_iso(), token, int(org_id))
        )
        conn.commit()


def resolve_invite(token: str) -> Optional[dict[str, Any]]:
    """Действующая ссылка → организация (для экрана «Вас приглашают в …»)."""
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM org_invites WHERE token = ? AND revoked_at IS NULL AND expires_at > ?",
            (str(token or ""), _now_iso()),
        ).fetchone()
    if not row:
        return None
    org = get_org(int(row["org_id"]))
    return {"org": org, "expires_at": row["expires_at"]} if org else None


def accept_invite(user_id: int | str, token: str) -> dict[str, Any]:
    inv = resolve_invite(token)
    if not inv:
        raise OrgError("Ссылка недействительна или истекла", 404)
    org = inv["org"]
    uid = _uid(user_id)
    with _LOCK:
        conn = _conn()
        m = _membership(org["id"], uid)
        if m:
            conn.execute("UPDATE org_members SET status = 'active' WHERE id = ?", (m["id"],))
        else:
            _insert_member(org["id"], uid, added_by="invite:" + str(token)[:6])
        conn.execute("DELETE FROM org_dismissed WHERE user_id = ? AND org_id = ?", (uid, org["id"]))
        conn.commit()
    return get_org(org["id"]) or org


# ---------- настройки (профиль выпуска и пр.) ----------


# ---------- админка Орбиты (операторы, не участники организации) ----------
# Проверку «это оператор» делает вызывающий код (assistant/admin); здесь — только действия.


def is_operator(user_id: int | str) -> bool:
    """Операторы Орбиты — те же, кто одобряет заявки на доступ (TELEGRAM_ACCESS_APPROVER_IDS)."""
    from assistant.lib.telegram_access_allowlist import approver_user_ids

    try:
        return int(user_id) in approver_user_ids()
    except (TypeError, ValueError):
        return False


def admin_list_orgs() -> list[dict[str, Any]]:
    """Все организации с владельцами."""
    from assistant.stores import note_members

    with _LOCK:
        rows = _conn().execute("SELECT * FROM orgs ORDER BY created_at DESC, id DESC").fetchall()
        items = [_org_dict(r) for r in rows]
        owners: dict[int, list[str]] = {}
        for r in _conn().execute(
            "SELECT org_id, user_id FROM org_members WHERE role = 'owner' AND status = 'active'"
        ).fetchall():
            owners.setdefault(int(r["org_id"]), []).append(str(r["user_id"]))
    for it in items:
        it["owners"] = [
            {"user_id": uid, "name": note_members.profile_display_name(note_members.get_profile(uid), fallback=uid)}
            for uid in owners.get(it["id"], [])
        ]
    return items


def orgs_of_user(user_id: int | str) -> list[dict[str, Any]]:
    """Где пользователь состоит (любой статус) — для карточки пользователя в админке."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT o.id, o.name, m.role, m.status, m.plan FROM org_members m JOIN orgs o ON o.id = m.org_id "
            "WHERE m.user_id = ? ORDER BY o.name",
            (_uid(user_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def org_names_by_user() -> dict[str, list[str]]:
    """user_id → названия организаций (активное членство) — колонка в списке пользователей."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT m.user_id, o.name FROM org_members m JOIN orgs o ON o.id = m.org_id "
            "WHERE m.status = 'active' AND m.user_id IS NOT NULL ORDER BY o.name"
        ).fetchall()
    out: dict[str, list[str]] = {}
    for r in rows:
        out.setdefault(str(r["user_id"]), []).append(str(r["name"]))
    return out


def resolve_user(actor_id: int | str, raw_key: str) -> Optional[int]:
    """Telegram id, @ник или почта → id пользователя Орбиты."""
    raw = str(raw_key or "").strip()
    if raw.isdigit():
        return int(raw)
    key = normalize_member_key(raw)
    return _resolve_key(actor_id, key) if key else None


def admin_set_owner(org_id: int, target_id: int | str, *, by: str = "") -> dict[str, Any]:
    """Назначить владельца (прежний уходил, аккаунт потерян). Прежние владельцы становятся админами."""
    if not get_org(org_id):
        raise OrgError("Организация не найдена", 404)
    uid = _uid(target_id)
    with _LOCK:
        conn = _conn()
        conn.execute(
            "UPDATE org_members SET role = 'admin' WHERE org_id = ? AND role = 'owner' AND user_id != ?",
            (int(org_id), uid),
        )
        m = _membership(org_id, uid)
        if m:
            conn.execute("UPDATE org_members SET role = 'owner', status = 'active' WHERE id = ?", (m["id"],))
        else:
            _insert_member(org_id, uid, role="owner", added_by="admin:" + str(by))
        conn.commit()
    return get_org(org_id) or {}


# ---------- тарифы (пока без оплаты) ----------


def seats_used(org_id: int) -> int:
    """Места заняты участниками (и приглашёнными) с платным тарифом."""
    with _LOCK:
        return int(_conn().execute(
            "SELECT COUNT(*) FROM org_members WHERE org_id = ? AND plan != 'free' AND status != 'pending'",
            (int(org_id),),
        ).fetchone()[0])


def set_billing(actor_id: int | str, org_id: int, *, plan: str | None = None, seats: int | None = None) -> dict[str, Any]:
    """Тариф организации и число мест. Меняет только владелец."""
    _require(actor_id, org_id, "billing")
    return admin_set_billing(org_id, plan=plan, seats=seats)


def admin_set_billing(org_id: int, *, plan: str | None = None, seats: int | None = None) -> dict[str, Any]:
    if not get_org(org_id):
        raise OrgError("Организация не найдена", 404)
    sets: dict[str, Any] = {}
    if plan is not None:
        if plan not in PLANS:
            raise OrgError("Неизвестный тариф")
        sets["plan"] = plan
    if seats is not None:
        seats = int(seats)
        if seats < 0 or seats > 10000:
            raise OrgError("Число мест — от 0 до 10 000")
        used = seats_used(org_id)
        if seats < used:
            raise OrgError(f"Платный тариф уже у {used} — сначала переведите кого-то на бесплатный")
        sets["seats"] = seats
    if sets:
        with _LOCK:
            conn = _conn()
            conn.execute(
                "UPDATE orgs SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                (*sets.values(), int(org_id)),
            )
            conn.commit()
    return get_org(org_id) or {}


def set_member_plan(actor_id: int | str, org_id: int, member_row_id: int, plan: str) -> None:
    """Владелец выдаёт участнику тариф. Платный занимает место из купленных организацией."""
    _require(actor_id, org_id, "billing")
    admin_set_member_plan(org_id, member_row_id, plan)


def admin_set_member_plan(org_id: int, member_row_id: int, plan: str) -> None:
    if plan not in PLANS:
        raise OrgError("Неизвестный тариф")
    org = get_org(org_id) or {}
    used = seats_used(org_id)
    with _LOCK:
        conn = _conn()
        m = conn.execute(
            "SELECT * FROM org_members WHERE id = ? AND org_id = ?", (int(member_row_id), int(org_id))
        ).fetchone()
        if not m:
            raise OrgError("Участник не найден", 404)
        if plan != "free" and m["plan"] == "free" and used >= int(org.get("seats") or 0):
            raise OrgError("Свободных мест нет — увеличьте число мест в тарифе организации")
        conn.execute("UPDATE org_members SET plan = ? WHERE id = ?", (plan, m["id"]))
        conn.commit()


def get_settings(org_id: int) -> dict[str, Any]:
    with _LOCK:
        row = _conn().execute("SELECT json FROM org_settings WHERE org_id = ?", (int(org_id),)).fetchone()
    try:
        data = json.loads(row["json"]) if row else {}
    except (TypeError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def set_settings(actor_id: int | str, org_id: int, section: str, value: Any) -> dict[str, Any]:
    """Заменяет один раздел настроек (например, ``market``)."""
    _require(actor_id, org_id, "edit_digest")
    data = get_settings(org_id)
    data[str(section)] = value
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO org_settings (org_id, json, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(org_id) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at",
            (int(org_id), json.dumps(data, ensure_ascii=False), _now_iso()),
        )
        conn.commit()
    return data
