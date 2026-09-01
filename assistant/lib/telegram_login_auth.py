"""Telegram Login Widget (browser) + signed mini-app session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

MINIAPP_SESSION_COOKIE = "miniapp_session"


def session_cookie_max_age() -> int:
    return _session_ttl_sec()


def _auth_max_age_sec() -> int:
    raw = os.getenv("TELEGRAM_LOGIN_AUTH_MAX_AGE_SEC", "").strip()
    if raw:
        try:
            return max(60, int(raw))
        except ValueError:
            pass
    return 86400


def _session_ttl_sec() -> int:
    raw = os.getenv("MINIAPP_BROWSER_SESSION_TTL_SEC", "").strip()
    if raw:
        try:
            return max(3600, int(raw))
        except ValueError:
            pass
    return 30 * 86400


def _session_key() -> bytes:
    extra = (os.getenv("MINIAPP_SESSION_SECRET", "") or "").strip()
    if extra:
        return hashlib.sha256(extra.encode("utf-8")).digest()
    token = (os.getenv("TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан (нужен для сессии браузера).")
    return hashlib.sha256(f"miniapp_session:{token}".encode("utf-8")).digest()


def verify_login_widget_payload(data: dict[str, Any], *, bot_token: str) -> dict[str, Any]:
    """Проверка данных от Telegram Login Widget (https://core.telegram.org/widgets/login)."""
    if not isinstance(data, dict):
        raise ValueError("Некорректные данные авторизации.")
    token = (bot_token or "").strip()
    if not token:
        raise ValueError("Токен бота не задан на сервере.")

    received_hash = str(data.get("hash") or "").strip()
    if not received_hash:
        raise ValueError("В данных Telegram Login нет hash.")

    check_pairs: list[str] = []
    for key in sorted(data.keys()):
        if key == "hash":
            continue
        val = data[key]
        if val is None:
            continue
        check_pairs.append(f"{key}={val}")
    data_check_string = "\n".join(check_pairs)

    secret_key = hashlib.sha256(token.encode("utf-8")).digest()
    calculated = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise ValueError("Неверная подпись Telegram Login.")

    auth_raw = str(data.get("auth_date") or "").strip()
    try:
        auth_ts = int(auth_raw)
    except ValueError as e:
        raise ValueError("Некорректное поле auth_date.") from e
    now = int(time.time())
    if auth_ts <= 0 or now - auth_ts > _auth_max_age_sec():
        raise ValueError("Устаревшие данные входа. Попробуйте снова.")

    try:
        uid = int(data.get("id"))
    except (TypeError, ValueError) as e:
        raise ValueError("В данных нет корректного id пользователя.") from e
    if uid <= 0:
        raise ValueError("Некорректный id пользователя.")

    user: dict[str, Any] = {"id": uid}
    for key in ("first_name", "last_name", "username", "photo_url", "language_code"):
        v = data.get(key)
        if v is not None and str(v).strip():
            user[key] = str(v).strip()
    return user


def issue_browser_session(telegram_user_id: int, user: dict[str, Any]) -> tuple[str, int]:
    """Возвращает (token, expires_at_unix)."""
    exp = int(time.time()) + _session_ttl_sec()
    payload = {
        "uid": int(telegram_user_id),
        "exp": exp,
        "u": {
            k: user[k]
            for k in ("id", "username", "first_name", "last_name", "photo_url")
            if k in user and user[k] is not None
        },
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_session_key(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}", exp


def verify_browser_session(token: str) -> tuple[int, dict[str, Any]] | None:
    raw = (token or "").strip()
    if not raw or "." not in raw:
        return None
    body, sig = raw.rsplit(".", 1)
    if not body or not sig:
        return None
    try:
        key = _session_key()
    except RuntimeError:
        return None
    expected = hmac.new(key, body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    pad = "=" * (-len(body) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        uid = int(payload.get("uid"))
        exp = int(payload.get("exp"))
    except (TypeError, ValueError):
        return None
    if uid <= 0 or int(time.time()) > exp:
        return None
    user = payload.get("u")
    if not isinstance(user, dict):
        user = {"id": uid}
    else:
        user = dict(user)
        user.setdefault("id", uid)
    return uid, user
