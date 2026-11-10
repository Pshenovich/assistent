"""Аккаунты Орбиты: почта — главный ключ, Telegram — привязанный канал.

Числовой ``user_id`` остаётся ключом всех хранилищ. У старых аккаунтов он равен
Telegram id (``tg_user_id == user_id``), новые аккаунты по почте получают
синтетический id ≥ ``SYNTHETIC_BASE`` — выше любого Telegram id (< 2^52) и ниже 2^53,
так что в JS он точное число.

Коды подтверждения почты: 6 цифр, 10 минут, в базе только хэш, 5 попыток на код,
ограничение выпуска по адресу и по IP.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]

SYNTHETIC_BASE = 5_000_000_000_000_000

CODE_TTL_SEC = 10 * 60
CODE_MAX_ATTEMPTS = 5
RESEND_GAP_SEC = 60
PER_EMAIL_HOUR = 5
PER_IP_HOUR = 20

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(ValueError):
    """Ошибка с текстом для пользователя; ``code`` — HTTP-статус."""

    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def _conn() -> sqlite3.Connection:
    conn = notes_store._conn()  # type: ignore[attr-defined]
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'account_aliases'").fetchone():
        return conn
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            user_id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            email_verified_at INTEGER,
            tg_user_id TEXT UNIQUE,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS email_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'login',
            expires_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            used_at INTEGER,
            ip TEXT NOT NULL DEFAULT '',
            created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_codes_email ON email_codes(email, created_at);
        CREATE INDEX IF NOT EXISTS idx_email_codes_ip ON email_codes(ip, created_at);
        CREATE TABLE IF NOT EXISTS tg_link_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at INTEGER NOT NULL
        );
        -- Пустой аккаунт по почте склеен со старым аккаунтом из Telegram: сессии на старый id ведут сюда.
        CREATE TABLE IF NOT EXISTS account_aliases (
            old_user_id TEXT PRIMARY KEY,
            new_user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        """
    )
    return conn


def _now() -> int:
    return int(time.time())


def normalize_email(raw: str | None) -> str:
    email = str(raw or "").strip().lower()
    if email.startswith("mailto:"):
        email = email[7:]
    if not _EMAIL_RE.match(email) or len(email) > 254:
        raise AccountError("Укажите почту в виде name@company.ru")
    return email


def _row(r: sqlite3.Row | tuple | None) -> Optional[dict[str, Any]]:
    if not r:
        return None
    user_id, email, verified_at, tg, created = r
    return {
        "user_id": int(user_id),
        "email": email,
        "email_verified": bool(verified_at),
        "email_verified_at": verified_at,
        "tg_user_id": int(tg) if tg else None,
        "created_at": created,
    }


_COLS = "user_id, email, email_verified_at, tg_user_id, created_at"


def get(user_id: int | str) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(f"SELECT {_COLS} FROM accounts WHERE user_id = ?", (str(int(user_id)),)).fetchone()
    return _row(r)


def get_by_email(email: str) -> Optional[dict[str, Any]]:
    try:
        key = normalize_email(email)
    except AccountError:
        return None
    with _LOCK:
        r = _conn().execute(f"SELECT {_COLS} FROM accounts WHERE email = ?", (key,)).fetchone()
    return _row(r)


def get_by_tg(tg_user_id: int | str) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(f"SELECT {_COLS} FROM accounts WHERE tg_user_id = ?", (str(int(tg_user_id)),)).fetchone()
    return _row(r)


def is_synthetic(user_id: int | str) -> bool:
    try:
        return int(user_id) >= SYNTHETIC_BASE
    except (TypeError, ValueError):
        return False


def account_for_tg(tg_user_id: int) -> int:
    """Telegram id → id аккаунта. Не привязан — сам Telegram id (старые аккаунты)."""
    acc = get_by_tg(tg_user_id)
    return int(acc["user_id"]) if acc else int(tg_user_id)


def tg_chat_for(user_id: int) -> Optional[int]:
    """Id аккаунта → личный чат в Telegram; None — Telegram не привязан."""
    uid = int(user_id)
    acc = get(uid)
    if acc:
        return acc["tg_user_id"]
    return None if is_synthetic(uid) else uid


def email_of(user_id: int | str) -> Optional[str]:
    acc = get(user_id)
    return acc["email"] if acc and acc["email_verified"] else None


def _new_synthetic_id(conn: sqlite3.Connection) -> int:
    r = conn.execute(
        "SELECT MAX(CAST(user_id AS INTEGER)) FROM accounts WHERE CAST(user_id AS INTEGER) >= ?", (SYNTHETIC_BASE,)
    ).fetchone()
    top = int(r[0]) if r and r[0] is not None else SYNTHETIC_BASE
    return top + 1 + secrets.randbelow(1000)


def create_email_account(email: str, *, tg_user_id: int | None = None) -> dict[str, Any]:
    """Новый аккаунт по подтверждённой почте. Уже есть — возвращает существующий."""
    key = normalize_email(email)
    now = _now()
    with _LOCK:
        conn = _conn()
        r = conn.execute(f"SELECT {_COLS} FROM accounts WHERE email = ?", (key,)).fetchone()
        if r:
            return _row(r)  # type: ignore[return-value]
        uid = _new_synthetic_id(conn)
        conn.execute(
            "INSERT INTO accounts (user_id, email, email_verified_at, tg_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(uid), key, now, str(int(tg_user_id)) if tg_user_id else None, now),
        )
        conn.commit()
    return get(uid)  # type: ignore[return-value]


def attach_email(user_id: int, email: str) -> dict[str, Any]:
    """Подтверждённая почта старому аккаунту (id = Telegram id) или смена почты."""
    uid = int(user_id)
    key = normalize_email(email)
    now = _now()
    with _LOCK:
        conn = _conn()
        other = conn.execute("SELECT user_id FROM accounts WHERE email = ?", (key,)).fetchone()
        if other and int(other[0]) != uid:
            raise AccountError("Эта почта уже привязана к другому аккаунту Орбиты.", 409)
        mine = conn.execute("SELECT user_id FROM accounts WHERE user_id = ?", (str(uid),)).fetchone()
        if mine:
            conn.execute("UPDATE accounts SET email = ?, email_verified_at = ? WHERE user_id = ?", (key, now, str(uid)))
        else:
            tg = None if is_synthetic(uid) else str(uid)
            if tg and conn.execute("SELECT 1 FROM accounts WHERE tg_user_id = ?", (tg,)).fetchone():
                raise AccountError("Этот Telegram уже привязан к другому аккаунту Орбиты.", 409)
            conn.execute(
                "INSERT INTO accounts (user_id, email, email_verified_at, tg_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uid), key, now, tg, now),
            )
        conn.commit()
    return get(uid)  # type: ignore[return-value]


def link_tg(user_id: int, tg_user_id: int) -> dict[str, Any]:
    uid, tg = int(user_id), int(tg_user_id)
    with _LOCK:
        conn = _conn()
        other = conn.execute("SELECT user_id FROM accounts WHERE tg_user_id = ?", (str(tg),)).fetchone()
        if other and int(other[0]) != uid:
            raise AccountError("Этот Telegram уже привязан к другому аккаунту Орбиты.", 409)
        if not conn.execute("SELECT 1 FROM accounts WHERE user_id = ?", (str(uid),)).fetchone():
            raise AccountError("Аккаунт не найден.", 404)
        conn.execute("UPDATE accounts SET tg_user_id = ? WHERE user_id = ?", (str(tg), str(uid)))
        conn.commit()
    return get(uid)  # type: ignore[return-value]


# ---------- коды подтверждения ----------

def _secret() -> bytes:
    raw = os.getenv("MINIAPP_SESSION_SECRET") or os.getenv("TELEGRAM_BOT_TOKEN") or "orbita-email-codes"
    return hashlib.sha256(("email_codes:" + raw).encode()).digest()


def _hash(email: str, code: str) -> str:
    return hmac.new(_secret(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def issue_code(email: str, *, purpose: str = "login", ip: str = "") -> dict[str, Any]:
    """Новый код для почты. Код возвращается вызывающему — отправить письмом, в базе только хэш."""
    key = normalize_email(email)
    now = _now()
    hour_ago = now - 3600
    with _LOCK:
        conn = _conn()
        last = conn.execute(
            "SELECT MAX(created_at) FROM email_codes WHERE email = ?", (key,)
        ).fetchone()[0]
        if last and now - int(last) < RESEND_GAP_SEC:
            raise AccountError(f"Код уже отправлен. Новый можно запросить через {RESEND_GAP_SEC - (now - int(last))} с.", 429)
        n_email = conn.execute(
            "SELECT COUNT(*) FROM email_codes WHERE email = ? AND created_at > ?", (key, hour_ago)
        ).fetchone()[0]
        if n_email >= PER_EMAIL_HOUR:
            raise AccountError("Слишком много кодов на эту почту. Попробуйте через час.", 429)
        if ip:
            n_ip = conn.execute(
                "SELECT COUNT(*) FROM email_codes WHERE ip = ? AND created_at > ?", (ip, hour_ago)
            ).fetchone()[0]
            if n_ip >= PER_IP_HOUR:
                raise AccountError("Слишком много запросов. Попробуйте позже.", 429)
        code = f"{secrets.randbelow(1_000_000):06d}"
        # Прежние коды этой почты гасим: действует только последний.
        conn.execute("UPDATE email_codes SET used_at = ? WHERE email = ? AND used_at IS NULL", (now, key))
        conn.execute(
            "INSERT INTO email_codes (email, code_hash, purpose, expires_at, ip, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (key, _hash(key, code), purpose, now + CODE_TTL_SEC, ip, now),
        )
        conn.execute("DELETE FROM email_codes WHERE created_at < ?", (now - 86400,))
        conn.commit()
    return {"email": key, "code": code, "expires_at": now + CODE_TTL_SEC, "ttl_sec": CODE_TTL_SEC}


def verify_code(email: str, raw_code: str, *, purpose: str = "login") -> str:
    """Проверяет код; вернёт нормализованную почту или бросит AccountError."""
    key = normalize_email(email)
    code = re.sub(r"\D", "", str(raw_code or ""))
    now = _now()
    with _LOCK:
        conn = _conn()
        r = conn.execute(
            "SELECT id, code_hash, expires_at, attempts FROM email_codes "
            "WHERE email = ? AND purpose = ? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1",
            (key, purpose),
        ).fetchone()
        if not r or int(r[2]) <= now:
            raise AccountError("Код устарел — запросите новый.")
        cid, h, _exp, attempts = r
        if int(attempts) >= CODE_MAX_ATTEMPTS:
            raise AccountError("Слишком много неверных попыток — запросите новый код.", 429)
        if len(code) != 6 or not hmac.compare_digest(h, _hash(key, code)):
            conn.execute("UPDATE email_codes SET attempts = attempts + 1 WHERE id = ?", (cid,))
            conn.commit()
            left = CODE_MAX_ATTEMPTS - int(attempts) - 1
            raise AccountError("Неверный код." + (f" Осталось попыток: {left}." if left > 0 else " Запросите новый."))
        conn.execute("UPDATE email_codes SET used_at = ? WHERE id = ?", (now, cid))
        conn.commit()
    return key


# ---------- привязка Telegram по ссылке в бота ----------

TG_LINK_TTL_SEC = 15 * 60


def tg_changeable(user_id: int | str) -> bool:
    """Сменить Telegram можно только у аккаунта по почте: у старых аккаунтов id и есть Telegram id."""
    return is_synthetic(user_id)


def issue_tg_link(user_id: int) -> str:
    uid = int(user_id)
    if not tg_changeable(uid):
        raise AccountError("Telegram этого аккаунта сменить нельзя: аккаунт создан через него. Напишите в поддержку.")
    if not get(uid):
        raise AccountError("Аккаунт не найден.", 404)
    token = secrets.token_urlsafe(12)
    now = _now()
    with _LOCK:
        conn = _conn()
        # Прежние ссылки не гасим: экран мог запросить новую, пока человек уже жмёт Start по старой.
        conn.execute("DELETE FROM tg_link_tokens WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO tg_link_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                     (token, str(uid), now + TG_LINK_TTL_SEC))
        conn.commit()
    return token


def resolve_alias(user_id: int | str) -> Optional[int]:
    """Аккаунт склеили с другим — куда теперь ведёт этот id."""
    with _LOCK:
        r = _conn().execute("SELECT new_user_id FROM account_aliases WHERE old_user_id = ?", (str(int(user_id)),)).fetchone()
    return int(r[0]) if r else None


def _tg_has_history(tg_id: int) -> bool:
    """Telegram уже пользовался Орбитой (старый аккаунт: id = Telegram id)."""
    try:
        from assistant.stores import note_members

        if note_members.get_profile(tg_id):
            return True
    except Exception:
        pass
    return _has_data(tg_id)


def _has_data(user_id: int) -> bool:
    try:
        from assistant.stores import notes as notes_store_mod

        return bool(notes_store_mod.list_notes(int(user_id), limit=1, include_knowledge=True, include_transcription=True))
    except Exception:
        return True  # не смогли проверить — считаем, что данные есть, и не склеиваем


def merge_into_legacy(new_uid: int, tg_id: int) -> dict[str, Any]:
    """Вход по почте завёл пустой аккаунт, а Telegram — старый аккаунт с данными: почта переходит
    на старый аккаунт (id = Telegram id), новый пустой становится его псевдонимом."""
    new_uid, tg = int(new_uid), int(tg_id)
    acc = get(new_uid)
    if not acc or not is_synthetic(new_uid):
        raise AccountError("Аккаунт не найден.", 404)
    if get(tg) or get_by_tg(tg):
        raise AccountError("Этот Telegram уже привязан к другой почте.")
    if _has_data(new_uid):
        raise AccountError(
            "У этого Telegram есть свой аккаунт Орбиты, а в новом аккаунте уже есть документы — "
            "объединить автоматически не получится. Напишите в поддержку."
        )
    now = _now()
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM accounts WHERE user_id = ?", (str(new_uid),))
        conn.execute(
            "INSERT INTO accounts (user_id, email, email_verified_at, tg_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (str(tg), acc["email"], acc["email_verified_at"] or now, str(tg), now),
        )
        conn.execute(
            "INSERT OR REPLACE INTO account_aliases (old_user_id, new_user_id, created_at) VALUES (?, ?, ?)",
            (str(new_uid), str(tg), now),
        )
        conn.commit()
    try:
        from assistant.stores import orgs_store

        orgs_store.reassign_user(new_uid, tg)
    except Exception as e:
        print(f"[accounts] merge orgs {new_uid}->{tg} err={e!r}")
    try:
        from assistant.stores import org_contacts

        org_contacts.reassign_user(new_uid, tg)  # контакты (коллеги по организации) — следом за членством
    except Exception as e:
        print(f"[accounts] merge contacts {new_uid}->{tg} err={e!r}")
    return get(tg)  # type: ignore[return-value]


def link_tg_by_token(token: str, tg_user_id: int) -> tuple[dict[str, Any], Optional[int], bool]:
    """Бот получил /start link_<token>: привязать (или сменить) Telegram.

    (аккаунт, прежний Telegram id, склеили ли со старым аккаунтом этого Telegram).
    """
    tg = int(tg_user_id)
    now = _now()
    with _LOCK:
        conn = _conn()
        r = conn.execute("SELECT user_id, expires_at FROM tg_link_tokens WHERE token = ?", (str(token or ""),)).fetchone()
        if r:
            conn.execute("DELETE FROM tg_link_tokens WHERE token = ?", (str(token),))
            conn.commit()
    if not r or int(r[1]) < now:
        raise AccountError("Ссылка устарела — откройте её заново из профиля Орбиты.")
    uid = int(r[0])
    if get(tg) and tg != uid:
        raise AccountError("Этот Telegram — основной для другого аккаунта Орбиты (со своей почтой). Сменить его нельзя.")
    acc = get(uid)
    if not acc:
        raise AccountError("Аккаунт не найден.", 404)
    old = acc["tg_user_id"]
    if old == tg:
        return acc, None, False
    if not old and _tg_has_history(tg):
        # Старый пользователь вошёл по почте (завёлся пустой аккаунт) и привязывает свой Telegram.
        return merge_into_legacy(uid, tg), None, True
    return link_tg(uid, tg), old, False
