"""Соответствие @username → telegram_user_id (для календаря участника)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from assistant.config import ROOT
from assistant.stores.contacts_store import normalize_telegram_username

_lock = threading.RLock()


def _path() -> Path:
    raw = os.getenv("TELEGRAM_REGISTRY_PATH", str(ROOT / "data" / "telegram_registry.json"))
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict[str, Any]) -> None:
    _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def register_user(*, telegram_user_id: int, telegram_username: str | None) -> None:
    uname = normalize_telegram_username(telegram_username or "")
    if not uname:
        return
    with _lock:
        data = _load()
        by_user = data.setdefault("by_username", {})
        if not isinstance(by_user, dict):
            by_user = {}
            data["by_username"] = by_user
        by_user[uname] = int(telegram_user_id)
        by_id = data.setdefault("by_id", {})
        if not isinstance(by_id, dict):
            by_id = {}
            data["by_id"] = by_id
        by_id[str(int(telegram_user_id))] = uname
        _save(data)


def lookup_user_id(telegram_username: str) -> int | None:
    uname = normalize_telegram_username(telegram_username)
    if not uname:
        return None
    with _lock:
        by_user = _load().get("by_username") or {}
    try:
        return int(by_user.get(uname) or 0) or None
    except (TypeError, ValueError):
        return None


def lookup_username(telegram_user_id: int) -> str | None:
    with _lock:
        by_id = _load().get("by_id") or {}
    if not isinstance(by_id, dict):
        return None
    uname = str(by_id.get(str(int(telegram_user_id))) or "").strip()
    return uname or None
