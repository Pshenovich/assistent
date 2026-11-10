"""Совместный доступ к заметкам: участники, заявки на редактирование, присутствие."""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from datetime import datetime, timezone
from html import escape as html_escape
from typing import Any, Optional

from assistant.lib import sqlite_conn
from assistant.stores import notes as notes_store
from assistant.stores.contacts_store import (
    contact_display_name,
    normalize_telegram_username,
)

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
_PRESENCE_LOCK = threading.Lock()
_PRESENCE: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
_PHOTO_CACHE: dict[int, tuple[float, bytes | None]] = {}
_USERNAME_PHOTO_CACHE: dict[str, tuple[float, bytes | None]] = {}
_PHOTO_TTL_SEC = 3600.0
_PRESENCE_TTL_SEC = 12.0
_MEMBER_COLORS = (
    "#e25555",
    "#e2a055",
    "#3dba7e",
    "#529ef4",
    "#9b6bff",
    "#e25aa8",
    "#2cb8c8",
    "#c9a227",
)
_TOKEN_BYTES = 12
_VALID_ROLES = frozenset({"edit"})


@sqlite_conn.schema_once(lambda: notes_store._conn())  # type: ignore[attr-defined]
def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_members (
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            member_user_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'edit',
            added_at TEXT NOT NULL,
            PRIMARY KEY (owner_user_id, note_id, member_user_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_members_member "
        "ON note_members(member_user_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_edit_requests (
            token TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            note_id INTEGER NOT NULL,
            requester_user_id TEXT NOT NULL,
            requester_username TEXT,
            requester_name TEXT,
            share_token TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_note_edit_requests_note "
        "ON note_edit_requests(owner_user_id, note_id, requester_user_id, status)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id TEXT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            photo_url TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return _migrate_profiles(conn)


_PROFILES_MIGRATED: Optional[sqlite3.Connection] = None


def _migrate_profiles(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Своё имя (профиль → «Аккаунт») — отдельно от имени из Telegram, которое обновляется при каждом входе."""
    global _PROFILES_MIGRATED
    if _PROFILES_MIGRATED is conn:
        return conn
    cols = {r[1] for r in conn.execute("PRAGMA table_info(user_profiles)").fetchall()}
    for col in ("custom_first_name", "custom_last_name"):
        if col not in cols:
            conn.execute(f"ALTER TABLE user_profiles ADD COLUMN {col} TEXT")
    conn.commit()
    _PROFILES_MIGRATED = conn
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _uid(user_id: int | str) -> str:
    return str(int(user_id))


def member_color(user_id: int | str) -> str:
    try:
        n = abs(int(user_id))
    except (TypeError, ValueError):
        n = 0
    return _MEMBER_COLORS[n % len(_MEMBER_COLORS)]


def upsert_profile(
    user_id: int | str,
    *,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    photo_url: str | None = None,
) -> None:
    uid = _uid(user_id)
    uname = normalize_telegram_username(username or "") or None
    fn = (first_name or "").strip() or None
    ln = (last_name or "").strip() or None
    photo = (photo_url or "").strip() or None
    ts = _now_iso()
    with _LOCK:
        conn = _conn()
        existing = conn.execute(
            "SELECT username, first_name, last_name, photo_url FROM user_profiles WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if existing:
            uname = uname or (str(existing["username"] or "").strip() or None)
            fn = fn or (str(existing["first_name"] or "").strip() or None)
            ln = ln or (str(existing["last_name"] or "").strip() or None)
            photo = photo or (str(existing["photo_url"] or "").strip() or None)
            if (uname, fn, ln, photo) == tuple(
                (str(existing[k] or "").strip() or None) for k in ("username", "first_name", "last_name", "photo_url")
            ):
                return  # ничего не поменялось — без записи (зовётся на каждый запрос с авторизацией)
        conn.execute(
            """
            INSERT INTO user_profiles (
                user_id, username, first_name, last_name, photo_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                photo_url = excluded.photo_url,
                updated_at = excluded.updated_at
            """,
            (uid, uname, fn, ln, photo, ts),
        )
        conn.commit()
    if uname:
        try:
            from assistant.stores import telegram_registry

            telegram_registry.register_user(
                telegram_user_id=int(uid), telegram_username=uname
            )
        except Exception as e:
            print(f"[note_members] registry update failed user={uid} err={e!r}")


def get_profile(user_id: int | str) -> Optional[dict[str, Any]]:
    uid = _uid(user_id)
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (uid,)
        ).fetchone()
    if not row:
        return None
    custom = str(row["custom_first_name"] or "").strip()
    return {
        "user_id": str(row["user_id"]),
        "username": str(row["username"] or "") or None,
        # Своё имя (если задано) главнее имени из Telegram — и для фамилии тоже, даже пустой.
        "first_name": custom or str(row["first_name"] or "") or None,
        "last_name": (str(row["custom_last_name"] or "") if custom else str(row["last_name"] or "")) or None,
        "photo_url": str(row["photo_url"] or "") or None,
    }


def set_custom_name(user_id: int | str, first_name: str, last_name: str = "") -> dict[str, Any]:
    """Имя в Орбите. Пустое имя — снова как в Telegram."""
    uid = _uid(user_id)
    fn = " ".join(str(first_name or "").split())[:64]
    ln = " ".join(str(last_name or "").split())[:64]
    with _LOCK:
        conn = _conn()
        if not conn.execute("SELECT 1 FROM user_profiles WHERE user_id = ?", (uid,)).fetchone():
            conn.execute(
                "INSERT INTO user_profiles (user_id, updated_at) VALUES (?, ?)", (uid, _now_iso())
            )
        conn.execute(
            "UPDATE user_profiles SET custom_first_name = ?, custom_last_name = ?, updated_at = ? WHERE user_id = ?",
            (fn or None, (ln or None) if fn else None, _now_iso(), uid),
        )
        conn.commit()
    return get_profile(uid) or {}


def profile_display_name(profile: dict[str, Any] | None, *, fallback: str = "") -> str:
    if not profile:
        return fallback or "Участник"
    fn = str(profile.get("first_name") or "").strip()
    ln = str(profile.get("last_name") or "").strip()
    name = " ".join(p for p in (fn, ln) if p).strip()
    if name:
        return name
    uname = str(profile.get("username") or "").strip()
    if uname:
        return "@" + uname.lstrip("@")
    return fallback or "Участник"


def _public_member(
    user_id: str, *, is_owner: bool = False, role: str = "edit"
) -> dict[str, Any]:
    profile = get_profile(user_id) or {}
    uname = str(profile.get("username") or "") or None
    if not uname:
        try:
            from assistant.stores import telegram_registry

            uname = telegram_registry.lookup_username(int(user_id))
        except Exception:
            uname = None
    name = profile_display_name(
        {**profile, "username": uname},
        fallback=f"id {user_id}",
    )
    return {
        "user_id": user_id,
        "username": uname,
        "name": name,
        "first_name": profile.get("first_name"),
        "is_owner": bool(is_owner),
        "role": "owner" if is_owner else (role or "edit"),
        "color": member_color(user_id),
        "photo_url": f"/api/miniapp/users/{user_id}/photo",
    }


def list_member_rows(owner_user_id: int | str, note_id: int) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT * FROM note_members
            WHERE owner_user_id = ? AND note_id = ?
            ORDER BY added_at ASC
            """,
            (owner, int(note_id)),
        )
        rows = cur.fetchall()
    return [
        {
            "owner_user_id": str(r["owner_user_id"]),
            "note_id": int(r["note_id"]),
            "member_user_id": str(r["member_user_id"]),
            "role": str(r["role"] or "edit"),
            "added_at": r["added_at"],
        }
        for r in rows
    ]


def list_members_public(owner_user_id: int | str, note_id: int) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    out = [_public_member(owner, is_owner=True, role="owner")]
    seen = {owner}
    for row in list_member_rows(owner, note_id):
        mid = row["member_user_id"]
        if mid in seen:
            continue
        seen.add(mid)
        out.append(_public_member(mid, is_owner=False, role=row.get("role") or "edit"))
    return out


def members_public_for_notes(pairs: list[tuple[str, int]]) -> dict[tuple[str, int], list[dict[str, Any]]]:
    """list_members_public для многих документов сразу: участники — одним запросом на пачку,
    профиль каждого человека — один раз (раньше на список из 120 документов — сотни запросов
    и чтений реестра ников)."""
    keys = [(_uid(o), int(n)) for o, n in pairs]
    rows_by_note: dict[tuple[str, int], list[dict[str, Any]]] = {k: [] for k in keys}
    note_ids = sorted({n for _, n in keys})
    with _LOCK:
        conn = _conn()
        for i in range(0, len(note_ids), 500):
            chunk = note_ids[i : i + 500]
            cur = conn.execute(
                f"""
                SELECT owner_user_id, note_id, member_user_id, role FROM note_members
                WHERE note_id IN ({",".join("?" * len(chunk))})
                ORDER BY added_at ASC
                """,
                chunk,
            )
            for r in cur.fetchall():
                key = (str(r["owner_user_id"]), int(r["note_id"]))
                if key in rows_by_note:
                    rows_by_note[key].append({"member_user_id": str(r["member_user_id"]), "role": r["role"]})
    people: dict[str, dict[str, Any]] = {}

    def person(uid: str, *, is_owner: bool, role: str) -> dict[str, Any]:
        if uid not in people:
            people[uid] = _public_member(uid)
        return {**people[uid], "is_owner": bool(is_owner), "role": "owner" if is_owner else (role or "edit")}

    out: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for owner, nid in keys:
        members = [person(owner, is_owner=True, role="owner")]
        seen = {owner}
        for row in rows_by_note[(owner, nid)]:
            mid = row["member_user_id"]
            if mid in seen:
                continue
            seen.add(mid)
            members.append(person(mid, is_owner=False, role=row.get("role") or "edit"))
        out[(owner, nid)] = members
    return out


def is_member(owner_user_id: int | str, note_id: int, user_id: int | str) -> bool:
    owner = _uid(owner_user_id)
    uid = _uid(user_id)
    if owner == uid:
        return True
    ids = _member_keys(uid)
    marks = ",".join("?" * len(ids))
    with _LOCK:
        cur = _conn().execute(
            f"""
            SELECT 1 FROM note_members
            WHERE owner_user_id = ? AND note_id = ? AND member_user_id IN ({marks})
            LIMIT 1
            """,
            (owner, int(note_id), *ids),
        )
        return cur.fetchone() is not None


def _member_keys(uid: str) -> list[str]:
    """Под какими id в note_members может лежать доступ человека.

    Аккаунт по почте (синтетический id) с привязанным Telegram: доступ могли выдать на сам
    Telegram id — по контакту владельца или до привязки. Такие строки тоже его.
    """
    keys = [uid]
    try:
        from assistant.stores import accounts

        if accounts.is_synthetic(uid):
            acc = accounts.get(uid)
            tg = (acc or {}).get("tg_user_id")
            if tg and str(int(tg)) != uid:
                keys.append(str(int(tg)))
    except Exception as e:
        print(f"[note_members] account keys failed uid={uid} err={e!r}")
    return keys


def list_note_ids_for_member(user_id: int | str) -> list[tuple[str, int]]:
    ids = _member_keys(_uid(user_id))
    marks = ",".join("?" * len(ids))
    with _LOCK:
        cur = _conn().execute(
            f"""
            SELECT DISTINCT owner_user_id, note_id FROM note_members
            WHERE member_user_id IN ({marks})
            """,
            tuple(ids),
        )
        return [(str(r["owner_user_id"]), int(r["note_id"])) for r in cur.fetchall()]


def add_member(
    owner_user_id: int | str,
    note_id: int,
    member_user_id: int | str,
    *,
    role: str = "edit",
    allow_knowledge: bool = False,
) -> dict[str, Any]:
    owner = _uid(owner_user_id)
    mid = _uid(member_user_id)
    if owner == mid:
        raise ValueError("Нельзя добавить владельца как участника")
    note = notes_store.get_note(owner, int(note_id))
    if not note:
        raise ValueError("Документ не найден")
    if note.get("is_knowledge") and not allow_knowledge:
        raise ValueError("Документ базы знаний нельзя расшарить участникам")
    member_role = (role or "edit").strip() or "edit"
    if member_role not in _VALID_ROLES:
        raise ValueError("Некорректная роль")
    ts = _now_iso()
    with _LOCK:
        _conn().execute(
            """
            INSERT INTO note_members (
                owner_user_id, note_id, member_user_id, role, added_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(owner_user_id, note_id, member_user_id) DO UPDATE SET
                role = excluded.role
            """,
            (owner, int(note_id), mid, member_role, ts),
        )
        _conn().commit()
    return _public_member(mid, is_owner=False, role=member_role)


def remove_member(
    owner_user_id: int | str, note_id: int, member_user_id: int | str
) -> bool:
    owner = _uid(owner_user_id)
    mid = _uid(member_user_id)
    if owner == mid:
        return False
    ids = _member_keys(mid)
    marks = ",".join("?" * len(ids))
    with _LOCK:
        cur = _conn().execute(
            f"""
            DELETE FROM note_members
            WHERE owner_user_id = ? AND note_id = ? AND member_user_id IN ({marks})
            """,
            (owner, int(note_id), *ids),
        )
        _conn().commit()
        return cur.rowcount > 0


def delete_all_for_note(owner_user_id: int | str, note_id: int) -> int:
    owner = _uid(owner_user_id)
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM note_members WHERE owner_user_id = ? AND note_id = ?",
            (owner, int(note_id)),
        )
        _conn().commit()
        return int(cur.rowcount or 0)


def get_shared_note(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    uid = _uid(user_id)
    note = notes_store.get_note_by_id(int(note_id))
    if not note:
        return None
    owner = str(note.get("owner_user_id") or "")
    if owner == uid:
        return note
    if not is_member(owner, int(note_id), uid):
        return None
    return note


def can_edit_note(user_id: int | str, note_id: int) -> Optional[dict[str, Any]]:
    return get_shared_note(user_id, note_id)


def shares_any_note_with(viewer_id: int | str, other_id: int | str) -> bool:
    a = _uid(viewer_id)
    b = _uid(other_id)
    if a == b:
        return True
    with _LOCK:
        cur = _conn().execute(
            """
            SELECT 1 FROM note_members
            WHERE (owner_user_id = ? AND member_user_id = ?)
               OR (owner_user_id = ? AND member_user_id = ?)
            LIMIT 1
            """,
            (a, b, b, a),
        )
        if cur.fetchone():
            return True
        cur = _conn().execute(
            """
            SELECT 1
            FROM note_members m1
            JOIN note_members m2
              ON m1.owner_user_id = m2.owner_user_id AND m1.note_id = m2.note_id
            WHERE m1.member_user_id = ? AND m2.member_user_id = ?
            LIMIT 1
            """,
            (a, b),
        )
        return cur.fetchone() is not None


def resolve_contact_telegram_id(
    *,
    owner_user_id: int | str,
    email: str | None = None,
    telegram_user_id: int | str | None = None,
    telegram_username: str | None = None,
    owner_username: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Находит telegram_user_id контакта. Бросает ValueError, если нельзя."""
    from assistant.stores import contacts_store
    from assistant.stores import telegram_registry

    owner = int(_uid(owner_user_id))
    try:
        direct = int(telegram_user_id or 0)
    except (TypeError, ValueError):
        direct = 0
    contact: dict[str, Any] | None = None
    if email:
        items = contacts_store.load_contacts(
            telegram_user_id=owner, telegram_username=owner_username
        )
        key = str(email).strip().lower()
        for row in items:
            if str(row.get("email") or "").strip().lower() == key:
                contact = row
                break
        # Почта — основной ключ аккаунта: человек мог войти по ней и ни разу не открыть бота.
        acc_uid = _account_by_email(key)
        if acc_uid and not contact:
            direct = direct or acc_uid
        if not contact and not acc_uid:
            raise ValueError(
                f"Не нашёл в Орбите никого с почтой {key}. Пусть человек войдёт в Орбиту по этой почте — "
                "после этого повторите добавление."
            )
    if contact:
        try:
            direct = direct or int(contact.get("telegram_user_id") or 0)
        except (TypeError, ValueError):
            direct = 0
        telegram_username = telegram_username or contact.get("telegram_username")
        if not telegram_username:
            extras = [contact.get("name"), *((contact.get("aliases") or []) if isinstance(contact.get("aliases"), list) else [])]
            for extra in extras:
                cand = normalize_telegram_username(str(extra or ""))
                if cand:
                    telegram_username = cand
                    break
    if direct <= 0 and contact and contact.get("email"):
        direct = _account_by_email(str(contact.get("email"))) or 0
    uname = normalize_telegram_username(telegram_username or "")
    if direct <= 0 and uname:
        found = telegram_registry.lookup_user_id(uname)
        if found:
            direct = _account_for_tg(int(found))
    if direct <= 0 and uname:
        profile_uid = _lookup_profile_by_username(uname)
        if profile_uid:
            direct = profile_uid
    if direct <= 0 and uname:
        try:
            from assistant.lib import telegram_access_allowlist as access

            pending_uid = access.lookup_pending_user_id(uname)
            if pending_uid:
                direct = _account_for_tg(int(pending_uid))
        except Exception as e:
            print(f"[note_members] pending lookup failed username={uname} err={e!r}")
    if direct <= 0 and uname:
        # Ник указали, когда добавляли человека в организацию, а сам он вошёл по почте.
        try:
            from assistant.stores import orgs_store

            direct = orgs_store.user_by_member_nick(uname) or 0
        except Exception as e:
            print(f"[note_members] org nick lookup failed username={uname} err={e!r}")
    if direct > 0:
        # Telegram id из контакта, из запроса или из профиля → аккаунт Орбиты: у вошедшего по почте
        # и привязавшего Telegram документы лежат под синтетическим id, а не под Telegram id.
        direct = _account_for_tg(direct)
    if direct <= 0:
        who = f"@{uname}" if uname else "контакта"
        raise ValueError(
            f"Не удалось найти {who} в Орбите. Добавьте человека по почте, под которой он входит, "
            "или пусть он напишет боту /start — после этого повторите добавление."
        )
    if contact:
        upsert_profile(
            direct,
            username=contact.get("telegram_username") or uname,
            first_name=contact.get("first_name") or contact_display_name(contact),
        )
    elif uname:
        upsert_profile(direct, username=uname)
    return direct, contact or {}


def email_twin(member_id: int | str, username: str | None = None) -> int | None:
    """Аккаунт по почте того же человека, к которому его Telegram не привязан.

    Человек когда-то писал боту (аккаунт = Telegram id), а теперь входит по почте — это два
    аккаунта. Владелец добавляет его из контактов, где записан Telegram, и доступ уходит не туда,
    где человек работает. Ищем почтовый аккаунт по тому же @нику: ник из приглашения в
    организацию или профиль, который владельцы заводили, добавляя его по нику.
    Только аккаунты по почте без Telegram — привязанный Telegram уже переводится в аккаунт сам.
    """
    try:
        mid = int(_uid(member_id))
    except (TypeError, ValueError):
        return None
    try:
        from assistant.stores import accounts

        if accounts.is_synthetic(mid) or accounts.get_by_tg(mid) or accounts.get(mid):
            return None
    except Exception as e:
        print(f"[note_members] twin account check failed uid={mid} err={e!r}")
        return None
    uname = normalize_telegram_username(username or "") or normalize_telegram_username(
        str((get_profile(mid) or {}).get("username") or "")
    )
    if not uname:
        return None
    cands: list[int] = []
    try:
        from assistant.stores import orgs_store

        found = orgs_store.user_by_member_nick(uname)
        if found:
            cands.append(int(found))
    except Exception as e:
        print(f"[note_members] twin org nick lookup failed username={uname} err={e!r}")
    try:
        from assistant.stores import telegram_registry

        found = telegram_registry.lookup_user_id(uname)
        if found:
            cands.append(int(found))
    except Exception:
        pass
    with _LOCK:
        rows = _conn().execute(
            "SELECT user_id FROM user_profiles WHERE lower(username) = ?", (uname,)
        ).fetchall()
    for r in rows:
        try:
            cands.append(int(r["user_id"]))
        except (TypeError, ValueError):
            continue
    for cand in cands:
        if cand == mid or not accounts.is_synthetic(cand):
            continue
        acc = accounts.get(cand)
        if acc and not acc.get("tg_user_id"):
            return cand
    return None


def _account_by_email(email: str) -> int | None:
    """Аккаунт Орбиты по почте: вход по почте, затем почта, с которой добавили в организацию."""
    try:
        from assistant.stores import accounts

        acc = accounts.get_by_email(email)
        if acc:
            return int(acc["user_id"])
    except Exception:
        pass
    try:
        from assistant.stores import orgs_store

        return orgs_store.user_by_member_email(email)
    except Exception:
        return None


def _account_for_tg(tg_id: int) -> int:
    """Telegram id → аккаунт Орбиты (если Telegram привязан к почтовому аккаунту)."""
    try:
        from assistant.stores import accounts

        return accounts.account_for_tg(int(tg_id))
    except Exception:
        return int(tg_id)


def _lookup_profile_by_username(username: str) -> int | None:
    uname = normalize_telegram_username(username)
    if not uname:
        return None
    with _LOCK:
        row = _conn().execute(
            "SELECT user_id FROM user_profiles WHERE lower(username) = ? LIMIT 1",
            (uname,),
        ).fetchone()
    if not row:
        return None
    try:
        return int(row["user_id"])
    except (TypeError, ValueError):
        return None


def create_edit_request(
    *,
    owner_user_id: int | str,
    note_id: int,
    requester_user_id: int | str,
    requester_username: str | None = None,
    requester_name: str | None = None,
    share_token: str | None = None,
) -> dict[str, Any]:
    owner = _uid(owner_user_id)
    req = _uid(requester_user_id)
    if owner == req:
        raise ValueError("Вы уже владелец документа")
    if is_member(owner, int(note_id), req):
        raise ValueError("Редактирование уже разрешено")
    from assistant.stores import org_kb

    if org_kb.at_least(org_kb.role_for_note(req, int(note_id)), "editor"):
        raise ValueError("Редактирование уже разрешено")
    with _LOCK:
        existing = _conn().execute(
            """
            SELECT * FROM note_edit_requests
            WHERE owner_user_id = ? AND note_id = ? AND requester_user_id = ?
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (owner, int(note_id), req),
        ).fetchone()
        if existing:
            out = _request_to_dict(existing)
            out["new"] = False
            return out
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        ts = _now_iso()
        _conn().execute(
            """
            INSERT INTO note_edit_requests (
                token, owner_user_id, note_id, requester_user_id,
                requester_username, requester_name, share_token, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                token,
                owner,
                int(note_id),
                req,
                normalize_telegram_username(requester_username or "") or None,
                (requester_name or "").strip() or None,
                (share_token or "").strip() or None,
                ts,
            ),
        )
        _conn().commit()
        row = _conn().execute(
            "SELECT * FROM note_edit_requests WHERE token = ?", (token,)
        ).fetchone()
    assert row is not None
    out = _request_to_dict(row)
    out["new"] = True
    return out


def get_edit_request(token: str) -> Optional[dict[str, Any]]:
    raw = (token or "").strip()
    if not raw:
        return None
    with _LOCK:
        row = _conn().execute(
            "SELECT * FROM note_edit_requests WHERE token = ?", (raw,)
        ).fetchone()
    return _request_to_dict(row) if row else None


def resolve_edit_request(token: str, *, allow: bool) -> Optional[dict[str, Any]]:
    req = get_edit_request(token)
    if not req or req.get("status") != "pending":
        return None
    ts = _now_iso()
    status = "allowed" if allow else "denied"
    with _LOCK:
        _conn().execute(
            """
            UPDATE note_edit_requests
            SET status = ?, resolved_at = ?
            WHERE token = ? AND status = 'pending'
            """,
            (status, ts, req["token"]),
        )
        _conn().commit()
    if allow:
        from assistant.stores import org_kb

        node = org_kb.node_for_note(int(req["note_id"]))
        if node:
            # документ базы знаний организации: роль редактора на документе, а не участник заметки
            org_kb.set_access(
                req["owner_user_id"], int(node["org_id"]), int(node["id"]), f"u:{req['requester_user_id']}", "editor"
            )
        else:
            add_member(req["owner_user_id"], int(req["note_id"]), req["requester_user_id"])
    updated = get_edit_request(token)
    return updated


def _request_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "token": str(row["token"]),
        "owner_user_id": str(row["owner_user_id"]),
        "note_id": int(row["note_id"]),
        "requester_user_id": str(row["requester_user_id"]),
        "requester_username": str(row["requester_username"] or "") or None,
        "requester_name": str(row["requester_name"] or "") or None,
        "share_token": str(row["share_token"] or "") or None,
        "status": str(row["status"] or "pending"),
        "created_at": row["created_at"],
        "resolved_at": row["resolved_at"],
    }


def heartbeat(
    owner_user_id: int | str,
    note_id: int,
    user_id: int | str,
    *,
    cursor: int | None = None,
    display_name: str = "",
    username: str | None = None,
) -> list[dict[str, Any]]:
    owner = _uid(owner_user_id)
    uid = _uid(user_id)
    key = (owner, int(note_id))
    now = time.time()
    with _PRESENCE_LOCK:
        room = _PRESENCE.setdefault(key, {})
        stale = [
            pid
            for pid, info in room.items()
            if now - float(info.get("ts") or 0) > _PRESENCE_TTL_SEC
        ]
        for pid in stale:
            room.pop(pid, None)
        room[uid] = {
            "user_id": uid,
            "cursor": int(cursor) if cursor is not None else None,
            "name": (display_name or "").strip() or profile_display_name(get_profile(uid)),
            "username": normalize_telegram_username(username or "") or None,
            "color": member_color(uid),
            "ts": now,
        }
    return list_peers(owner, int(note_id), exclude_user_id=None)


def leave_presence(owner_user_id: int | str, note_id: int, user_id: int | str) -> None:
    key = (_uid(owner_user_id), int(note_id))
    uid = _uid(user_id)
    with _PRESENCE_LOCK:
        room = _PRESENCE.get(key)
        if not room:
            return
        room.pop(uid, None)
        if not room:
            _PRESENCE.pop(key, None)


def list_peers(
    owner_user_id: int | str,
    note_id: int,
    *,
    exclude_user_id: int | str | None = None,
) -> list[dict[str, Any]]:
    key = (_uid(owner_user_id), int(note_id))
    skip = _uid(exclude_user_id) if exclude_user_id is not None else ""
    now = time.time()
    out: list[dict[str, Any]] = []
    with _PRESENCE_LOCK:
        room = _PRESENCE.get(key) or {}
        for pid, info in list(room.items()):
            if now - float(info.get("ts") or 0) > _PRESENCE_TTL_SEC:
                room.pop(pid, None)
                continue
            if skip and pid == skip:
                continue
            out.append(
                {
                    "user_id": pid,
                    "name": info.get("name") or "Участник",
                    "username": info.get("username"),
                    "color": info.get("color") or member_color(pid),
                    "cursor": info.get("cursor"),
                    "photo_url": f"/api/miniapp/users/{pid}/photo",
                }
            )
    out.sort(key=lambda x: str(x.get("user_id") or ""))
    return out


def cached_profile_photo(user_id: int) -> bytes | None:
    uid = int(user_id)
    now = time.time()
    hit = _PHOTO_CACHE.get(uid)
    if hit and now - hit[0] < _PHOTO_TTL_SEC:
        return hit[1]
    from assistant.lib.telegram_notify import get_user_profile_photo_bytes

    blob = get_user_profile_photo_bytes(uid)
    _PHOTO_CACHE[uid] = (now, blob)
    return blob


def cached_username_photo(username: str) -> bytes | None:
    uname = normalize_telegram_username(username)
    if not uname:
        return None
    now = time.time()
    hit = _USERNAME_PHOTO_CACHE.get(uname)
    if hit and now - hit[0] < _PHOTO_TTL_SEC:
        return hit[1]
    from assistant.lib.telegram_notify import get_username_profile_photo_bytes

    blob = get_username_profile_photo_bytes(uname)
    _USERNAME_PHOTO_CACHE[uname] = (now, blob)
    return blob


def note_webapp_url(note_id: int) -> str:
    from assistant.lib.webapp_public import webapp_entry_url

    base = webapp_entry_url()
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}note={int(note_id)}"


def note_title_link_html(title: str, note_id: int) -> str:
    label = html_escape((title or "").strip() or "Без названия")
    url = html_escape(note_webapp_url(note_id), quote=True)
    return f'<a href="{url}">{label}</a>'


def requester_label(username: str | None, name: str | None, user_id: str) -> str:
    uname = normalize_telegram_username(username or "")
    if uname:
        return f"@{uname}"
    disp = (name or "").strip()
    if disp:
        return html_escape(disp)
    return f"id {html_escape(user_id)}"
