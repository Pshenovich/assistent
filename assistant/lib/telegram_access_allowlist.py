"""Allowlist пользователей Telegram (одобрение администратором)."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from assistant.config import ROOT

_lock = threading.RLock()
_allowlist_cache: dict[str, tuple[float, frozenset[str], frozenset[int]]] = {}
_ALLOWLIST_CACHE_TTL_SEC = 5.0


def normalize_scope(scope: str | None = None) -> str:
    raw = (scope or "leo").strip().lower()
    if raw in {"donatello", "board"}:
        return "donatello"
    return "leo"


def _cached_allowlist(
    scope: str | None = None,
) -> tuple[frozenset[str], frozenset[int]]:
    key = normalize_scope(scope)
    now = time.time()
    hit = _allowlist_cache.get(key)
    if hit is not None and (now - hit[0]) < _ALLOWLIST_CACHE_TTL_SEC:
        return hit[1], hit[2]
    data = load_allowlist(scope=key)
    _allowlist_cache[key] = (now, data[0], data[1])
    return data


def invalidate_allowlist_cache(scope: str | None = None) -> None:
    if scope is None:
        _allowlist_cache.clear()
        return
    _allowlist_cache.pop(normalize_scope(scope), None)


def allowlist_path(scope: str | None = None) -> Path:
    key = normalize_scope(scope)
    if key == "donatello":
        raw = os.getenv("TELEGRAM_ACCESS_DONATELLO_ALLOWLIST_PATH", "").strip()
        default = ROOT / "data" / "allowed_telegram_access_donatello.json"
    else:
        raw = os.getenv("TELEGRAM_ACCESS_ALLOWLIST_PATH", "").strip()
        default = ROOT / "data" / "allowed_telegram_access.json"
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    return default.resolve()


def requests_path(scope: str | None = None) -> Path:
    key = normalize_scope(scope)
    if key == "donatello":
        raw = os.getenv("TELEGRAM_ACCESS_DONATELLO_REQUESTS_PATH", "").strip()
        default = ROOT / "data" / "access_requests_donatello.json"
    else:
        raw = os.getenv("TELEGRAM_ACCESS_REQUESTS_PATH", "").strip()
        default = ROOT / "data" / "access_requests.json"
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    return default.resolve()


def gate_enabled() -> bool:
    raw = os.getenv("TELEGRAM_ACCESS_GATE_ENABLED", "1").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(approver_user_ids())


def approver_user_ids() -> frozenset[int]:
    raw = os.getenv("TELEGRAM_ACCESS_APPROVER_IDS", "").strip()
    if not raw:
        fallback = os.getenv("MINIAPP_DEV_TELEGRAM_USER_ID", "").strip()
        if fallback:
            raw = fallback
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            pass
    return frozenset(out)


def load_allowlist(scope: str | None = None) -> tuple[frozenset[str], frozenset[int]]:
    path = allowlist_path(scope)
    if not path.is_file():
        return frozenset(), frozenset()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return frozenset(), frozenset()
    names: set[str] = set()
    ids: set[int] = set()
    for x in data.get("usernames") or []:
        if isinstance(x, str) and x.strip():
            names.add(x.strip().lstrip("@").lower())
    for x in data.get("user_ids") or []:
        try:
            ids.add(int(x))
        except (TypeError, ValueError):
            pass
    return frozenset(names), frozenset(ids)


def save_allowlist(
    usernames: set[str], user_ids: set[int], *, scope: str | None = None
) -> None:
    path = allowlist_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "usernames": sorted(usernames),
        "user_ids": sorted(user_ids),
        "_updated_unix": int(time.time()),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _lock:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    invalidate_allowlist_cache(scope)


def add_approved_user(
    *, username: str | None, user_id: int, scope: str | None = None
) -> None:
    unames, uids = load_allowlist(scope=scope)
    names = set(unames)
    ids = set(uids)
    ids.add(int(user_id))
    if username:
        names.add(username.strip().lstrip("@").lower())
    save_allowlist(names, ids, scope=scope)
    clear_request_state(int(user_id), scope=scope)


def is_extra_allowed(
    username: str | None, user_id: int, *, scope: str | None = None
) -> bool:
    unames, uids = _cached_allowlist(scope)
    if int(user_id) in uids:
        return True
    un = (username or "").strip().lstrip("@").lower()
    return bool(un) and un in unames


def _load_requests(scope: str | None = None) -> dict[str, Any]:
    path = requests_path(scope)
    with _lock:
        if not path.is_file():
            return {"pending": {}, "denied_ids": [], "tokens": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"pending": {}, "denied_ids": [], "tokens": {}}
        except Exception:
            return {"pending": {}, "denied_ids": [], "tokens": {}}


def _save_requests(data: dict[str, Any], *, scope: str | None = None) -> None:
    path = requests_path(scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_denied(user_id: int, *, scope: str | None = None) -> bool:
    data = _load_requests(scope)
    denied = data.get("denied_ids") or []
    return int(user_id) in {int(x) for x in denied}


def is_pending(user_id: int, *, scope: str | None = None) -> bool:
    pending = _load_requests(scope).get("pending") or {}
    return str(int(user_id)) in pending


def register_pending_request(
    *,
    user_id: int,
    username: str | None,
    first_name: str,
    last_name: str,
    scope: str | None = None,
) -> str:
    data = _load_requests(scope)
    pending = dict(data.get("pending") or {})
    uid = str(int(user_id))
    if uid not in pending:
        pending[uid] = {
            "username": (username or "").strip(),
            "first_name": first_name,
            "last_name": last_name,
            "requested_at": int(time.time()),
        }
    token = secrets.token_urlsafe(8)
    tokens = dict(data.get("tokens") or {})
    tokens[token] = {"user_id": int(user_id), "expires_at": time.time() + 86400 * 7}
    data["pending"] = pending
    data["tokens"] = tokens
    _save_requests(data, scope=scope)
    return token


def consume_action_token(token: str, *, scope: str | None = None) -> int | None:
    data = _load_requests(scope)
    tokens = dict(data.get("tokens") or {})
    rec = tokens.pop(token, None)
    if not isinstance(rec, dict):
        _save_requests({**data, "tokens": tokens}, scope=scope)
        return None
    try:
        if float(rec.get("expires_at") or 0) < time.time():
            _save_requests({**data, "tokens": tokens}, scope=scope)
            return None
        uid = int(rec.get("user_id") or 0)
    except (TypeError, ValueError):
        uid = 0
    _save_requests({**data, "tokens": tokens}, scope=scope)
    return uid or None


def get_pending_user(user_id: int, *, scope: str | None = None) -> dict[str, Any] | None:
    pending = (_load_requests(scope).get("pending") or {}).get(str(int(user_id)))
    return dict(pending) if isinstance(pending, dict) else None


def clear_request_state(user_id: int, *, scope: str | None = None) -> None:
    data = _load_requests(scope)
    pending = dict(data.get("pending") or {})
    pending.pop(str(int(user_id)), None)
    denied = [int(x) for x in (data.get("denied_ids") or []) if int(x) != int(user_id)]
    data["pending"] = pending
    data["denied_ids"] = denied
    _save_requests(data, scope=scope)


def deny_user(user_id: int, *, scope: str | None = None) -> None:
    data = _load_requests(scope)
    pending = dict(data.get("pending") or {})
    pending.pop(str(int(user_id)), None)
    denied = {int(x) for x in (data.get("denied_ids") or [])}
    denied.add(int(user_id))
    data["pending"] = pending
    data["denied_ids"] = sorted(denied)
    _save_requests(data, scope=scope)


def format_user_brief(
    *,
    user_id: int,
    username: str | None,
    first_name: str,
    last_name: str,
) -> str:
    name = " ".join(p for p in (first_name, last_name) if p).strip() or "—"
    un = f"@{username}" if username else "—"
    return f"{name}\nID: {user_id}\nUsername: {un}"
