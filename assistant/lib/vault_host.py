"""Отдельный адрес сейфов (vault.getorbita.me) — совет внешнего аудита 2026-11-10, решение владельца.

Зачем: на основном сайте десятки скриптов, и любая XSS там читала бы открытые сейфы. На адресе сейфов —
только страница сейфов со строгим CSP (без 'unsafe-inline', всё только со своего адреса, Trusted Types),
своя короткая сессия (кука только этого адреса) и passkey, привязанные к этому адресу (rp.id).

Вход: основное приложение (уже вошедший человек) просит одноразовый код (60 с, один раз) и открывает
``https://vault…/#c=<код>``; страница сейфов меняет код на свою сессию. Код идёт во фрагменте — в логи
и Referer не попадает.

Настройки: ``VAULT_HOST`` (по умолчанию vault.getorbita.me; локально — vault.localhost),
``VAULT_MAIN_URL`` — куда отправлять за входом (по умолчанию https://getorbita.me).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from typing import Any, Optional

SESSION_COOKIE = "vault_session"
SESSION_TTL = 12 * 3600
HANDOFF_TTL = 60

_LOCK = threading.Lock()


def vault_host() -> str:
    return (os.getenv("VAULT_HOST", "") or "vault.getorbita.me").strip().lower()


def _dev() -> bool:
    return (os.getenv("MINIAPP_DEV_MODE", "") or "").strip().lower() in ("1", "true", "yes", "on")


def request_host(request: Any) -> str:
    return (request.headers.get("host") or request.url.hostname or "").split(":")[0].strip().lower()


def is_vault_host(request: Any) -> bool:
    h = request_host(request)
    return h == vault_host() or (_dev() and h == "vault.localhost")


def vault_origin(request: Any = None) -> str:
    """Адрес страницы сейфов для ссылок. Локально — тот же порт на vault.localhost."""
    if _dev() and request is not None:
        h = request_host(request)
        if h.endswith("localhost") or h in ("127.0.0.1",):
            port = request.url.port
            return f"http://vault.localhost{':' + str(port) if port else ''}"
    return f"https://{vault_host()}"


def main_url() -> str:
    return (os.getenv("VAULT_MAIN_URL", "") or "https://getorbita.me").strip().rstrip("/")


def main_url_for(request: Any) -> str:
    """Куда идти за входом. Локально (vault.localhost) — app.localhost того же порта."""
    if _dev() and request_host(request) == "vault.localhost":
        port = request.url.port
        return f"http://app.localhost{':' + str(port) if port else ''}"
    return main_url()


# ---------- сессия адреса сейфов ----------


def _key() -> bytes:
    from assistant.lib.telegram_login_auth import _session_key

    # своя подпись: токен основного сайта здесь не подходит, и наоборот
    return hmac.new(_session_key(), b"orbita-vault-host-session", hashlib.sha256).digest()


def issue_session(uid: int, user: dict[str, Any]) -> str:
    payload = {"uid": int(uid), "exp": int(time.time()) + SESSION_TTL, "aud": "vault",
               "u": {k: user[k] for k in ("id", "username", "first_name", "last_name", "email") if user.get(k) is not None}}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).decode().rstrip("=")
    return f"{body}.{hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest()}"


def verify_session(token: str) -> Optional[tuple[int, dict[str, Any]]]:
    raw = (token or "").strip()
    if "." not in raw:
        return None
    body, sig = raw.rsplit(".", 1)
    try:
        if not hmac.compare_digest(hmac.new(_key(), body.encode(), hashlib.sha256).hexdigest(), sig):
            return None
        p = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode())
        uid, exp = int(p["uid"]), int(p["exp"])
    except Exception:
        return None
    if p.get("aud") != "vault" or uid <= 0 or time.time() > exp:
        return None
    user = p.get("u") if isinstance(p.get("u"), dict) else {}
    user.setdefault("id", uid)
    return uid, user


# ---------- одноразовый код входа ----------


def _conn() -> sqlite3.Connection:
    from assistant.stores import orgs_store

    conn = orgs_store._conn()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS vault_handoffs (code_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
        "user_json TEXT NOT NULL, created_at REAL NOT NULL)"
    )
    return conn


def _hash(code: str) -> str:
    return hashlib.sha256(("vault-handoff:" + code).encode()).hexdigest()


def handoff_create(uid: int, user: dict[str, Any]) -> str:
    from assistant.stores import orgs_store

    code = secrets.token_urlsafe(32)
    now = time.time()
    with orgs_store._LOCK:
        conn = _conn()
        conn.execute("DELETE FROM vault_handoffs WHERE created_at < ?", (now - HANDOFF_TTL,))
        conn.execute(
            "INSERT INTO vault_handoffs (code_hash, user_id, user_json, created_at) VALUES (?,?,?,?)",
            (_hash(code), str(int(uid)), json.dumps({k: user.get(k) for k in ("id", "username", "first_name", "last_name", "email")
                                                      if user.get(k) is not None}, ensure_ascii=False), now),
        )
        conn.commit()
    return code


def handoff_take(code: str) -> Optional[tuple[int, dict[str, Any]]]:
    """Код — один раз и не дольше минуты."""
    from assistant.stores import orgs_store

    c = (code or "").strip()
    if not c or len(c) > 200:
        return None
    with orgs_store._LOCK:
        conn = _conn()
        row = conn.execute("SELECT user_id, user_json, created_at FROM vault_handoffs WHERE code_hash = ?", (_hash(c),)).fetchone()
        conn.execute("DELETE FROM vault_handoffs WHERE code_hash = ?", (_hash(c),))
        conn.commit()
    if not row or time.time() - float(row[2]) > HANDOFF_TTL:
        return None
    try:
        user = json.loads(row[1]) or {}
    except ValueError:
        user = {}
    return int(row[0]), user


# ---------- что отдаёт адрес сейфов ----------

STATIC_FILES = (
    "theme.js", "vkui-ios-dark.css", "styles.css", "org-kb.css", "vault.css", "vault-app.css",
    "vault-crypto.js", "vault.js", "vault-app.js", "icon-192.png", "favicon.ico",
)
API_PREFIXES = ("/api/miniapp/vaults", "/api/miniapp/vault-session")

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'; require-trusted-types-for 'script'; trusted-types vault-html"
)


def path_allowed(path: str) -> bool:
    if path in ("/", "/index.html"):
        return True
    if path.startswith("/webapp/"):
        return path[len("/webapp/"):] in STATIC_FILES
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?") for p in API_PREFIXES)


def security_headers() -> dict[str, str]:
    return {
        "Content-Security-Policy": CSP,
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), publickey-credentials-get=(self), "
                              "publickey-credentials-create=(self)",
        "X-Content-Type-Options": "nosniff",
    }
