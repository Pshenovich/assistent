"""Per-user выбор бэкенда напоминаний: Telegram или Apple через CalDAV."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_lock = threading.Lock()

BACKEND_TELEGRAM = "telegram"
BACKEND_APPLE_CALDAV = "apple_caldav"
VALID_BACKENDS = frozenset({BACKEND_TELEGRAM, BACKEND_APPLE_CALDAV})


def _path() -> Path:
    raw = os.getenv("REMINDER_PREFERENCES_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (HERE / p).resolve()
        return p
    return HERE / "reminder_preferences.json"


def _load_all() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_reminder_backend(telegram_user_id: int) -> str:
    """По умолчанию — напоминания в Telegram."""
    key = str(int(telegram_user_id))
    with _lock:
        root = _load_all()
        entry = root.get(key)
        if not isinstance(entry, dict):
            return BACKEND_TELEGRAM
        b = str(entry.get("backend") or "").strip()
        if b in VALID_BACKENDS:
            return b
        return BACKEND_TELEGRAM


def set_reminder_backend(telegram_user_id: int, backend: str) -> None:
    b = str(backend or "").strip()
    if b not in VALID_BACKENDS:
        raise ValueError(f"Неизвестный backend: {backend!r}")
    key = str(int(telegram_user_id))
    with _lock:
        root = _load_all()
        cur = root.get(key)
        if not isinstance(cur, dict):
            cur = {}
        cur["backend"] = b
        root[key] = cur
        _save_all(root)
