"""Настройки пользователя (timezone из Google Calendar)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from assistant.config import CALENDAR_TZ, ROOT

_lock = threading.RLock()
_TTL_SEC = float(os.getenv("USER_TZ_CACHE_TTL_SEC", "86400") or "86400")


def _dir() -> Path:
    raw = os.getenv("USER_PREFS_DIR", str(ROOT / "data" / "users")).strip()
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(user_id: int) -> Path:
    return _dir() / f"{int(user_id)}.json"


def load_prefs(user_id: int) -> dict[str, Any]:
    path = _path(user_id)
    with _lock:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def save_prefs(user_id: int, prefs: dict[str, Any]) -> None:
    path = _path(user_id)
    with _lock:
        path.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def set_timezone(user_id: int, tz_name: str) -> None:
    prefs = load_prefs(user_id)
    prefs["timezone"] = (tz_name or "").strip() or CALENDAR_TZ
    prefs["timezone_updated_at"] = time.time()
    save_prefs(user_id, prefs)


def get_timezone_name(user_id: int) -> str | None:
    prefs = load_prefs(user_id)
    tz = str(prefs.get("timezone") or "").strip()
    return tz or None


def get_user_tz(user_id: int) -> ZoneInfo:
    tz = get_timezone_name(user_id)
    if tz:
        try:
            return ZoneInfo(tz)
        except Exception:
            pass
    return ZoneInfo(CALENDAR_TZ)


def get_calendar_excluded_ids(user_id: int) -> list[str]:
    prefs = load_prefs(user_id)
    raw = prefs.get("calendar_excluded_ids")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def set_calendar_excluded_ids(user_id: int, excluded_ids: list[str]) -> None:
    prefs = load_prefs(user_id)
    prefs["calendar_excluded_ids"] = [
        str(x).strip() for x in (excluded_ids or []) if str(x).strip()
    ]
    save_prefs(user_id, prefs)


def meeting_reminders_enabled(user_id: int) -> bool:
    """По умолчанию напоминания о встречах включены."""
    prefs = load_prefs(user_id)
    if "meeting_reminders_enabled" not in prefs:
        return True
    return bool(prefs.get("meeting_reminders_enabled"))


def set_meeting_reminders_enabled(user_id: int, enabled: bool) -> None:
    prefs = load_prefs(user_id)
    prefs["meeting_reminders_enabled"] = bool(enabled)
    save_prefs(user_id, prefs)


def zoom_auto_record_enabled(user_id: int) -> bool:
    """Автозапись Zoom-встреч через meeting bot (выкл. по умолчанию)."""
    prefs = load_prefs(user_id)
    return bool(prefs.get("zoom_auto_record"))


def set_zoom_auto_record_enabled(user_id: int, enabled: bool) -> None:
    prefs = load_prefs(user_id)
    prefs["zoom_auto_record"] = bool(enabled)
    save_prefs(user_id, prefs)


def telemost_auto_record_enabled(user_id: int) -> bool:
    """Автоконспектирование Телемоста при создании комнаты (выкл. по умолчанию)."""
    prefs = load_prefs(user_id)
    return bool(prefs.get("telemost_auto_record"))


def set_telemost_auto_record_enabled(user_id: int, enabled: bool) -> None:
    prefs = load_prefs(user_id)
    prefs["telemost_auto_record"] = bool(enabled)
    save_prefs(user_id, prefs)


def timezone_cache_stale(user_id: int) -> bool:
    prefs = load_prefs(user_id)
    if not str(prefs.get("timezone") or "").strip():
        return True
    try:
        ts = float(prefs.get("timezone_updated_at") or 0)
    except (TypeError, ValueError):
        return True
    return (time.time() - ts) > _TTL_SEC


def onboarding_completed(user_id: int) -> bool:
    prefs = load_prefs(user_id)
    try:
        return float(prefs.get("onboarding_completed_at") or 0) > 0
    except (TypeError, ValueError):
        return False


def mark_onboarding_completed(user_id: int) -> None:
    prefs = load_prefs(user_id)
    prefs["onboarding_completed_at"] = time.time()
    save_prefs(user_id, prefs)
