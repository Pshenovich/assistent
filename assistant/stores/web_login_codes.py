"""Одноразовые коды/ссылки для входа в браузер без oauth.telegram.org (VPN/прокси)."""

from __future__ import annotations

import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()
_TTL_SEC = 10 * 60
_CODE_LEN = 8
# Без 0/O/1/I — удобнее диктовать и вводить вручную.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_PATH = Path(__file__).resolve().parents[2] / "data" / "web_login_codes.json"


def _now() -> int:
    return int(time.time())


def _load() -> dict[str, Any]:
    if not _PATH.is_file():
        return {"codes": {}}
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"codes": {}}
    if not isinstance(data, dict):
        return {"codes": {}}
    codes = data.get("codes")
    if not isinstance(codes, dict):
        codes = {}
    return {"codes": codes}


def _save(data: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PATH)


def _purge(codes: dict[str, Any], now: int | None = None) -> None:
    ts = _now() if now is None else now
    dead = [k for k, v in codes.items() if not isinstance(v, dict) or int(v.get("exp") or 0) <= ts]
    for k in dead:
        codes.pop(k, None)


def _new_code(codes: dict[str, Any]) -> str:
    for _ in range(32):
        code = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
        if code not in codes:
            return code
    return secrets.token_urlsafe(12).upper()[:_CODE_LEN]


def issue_code(
    telegram_user_id: int,
    *,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    photo_url: str | None = None,
) -> dict[str, Any]:
    uid = int(telegram_user_id)
    if uid <= 0:
        raise ValueError("Некорректный user id")
    now = _now()
    exp = now + _TTL_SEC
    user: dict[str, Any] = {"id": uid}
    if username:
        user["username"] = str(username).strip().lstrip("@")
    if first_name:
        user["first_name"] = str(first_name).strip()
    if last_name:
        user["last_name"] = str(last_name).strip()
    if photo_url:
        user["photo_url"] = str(photo_url).strip()
    with _LOCK:
        data = _load()
        codes = data["codes"]
        _purge(codes, now)
        # Один активный код на пользователя — старые сбрасываем.
        for k, v in list(codes.items()):
            if isinstance(v, dict) and int(v.get("uid") or 0) == uid:
                codes.pop(k, None)
        code = _new_code(codes)
        codes[code] = {"uid": uid, "exp": exp, "user": user, "created": now}
        _save(data)
    return {"code": code, "expires_at": exp, "ttl_sec": _TTL_SEC}


def consume_code(raw: str) -> dict[str, Any] | None:
    code = str(raw or "").strip().upper().replace(" ", "").replace("-", "")
    if len(code) < 6:
        return None
    now = _now()
    with _LOCK:
        data = _load()
        codes = data["codes"]
        _purge(codes, now)
        row = codes.pop(code, None)
        _save(data)
    if not isinstance(row, dict):
        return None
    if int(row.get("exp") or 0) <= now:
        return None
    user = row.get("user")
    if not isinstance(user, dict):
        user = {"id": int(row.get("uid") or 0)}
    uid = int(user.get("id") or row.get("uid") or 0)
    if uid <= 0:
        return None
    user["id"] = uid
    return user
