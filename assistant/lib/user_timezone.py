"""Таймзона из Google Calendar Settings."""

from __future__ import annotations

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from assistant.integrations import google_calendar_oauth
from assistant.stores import user_prefs


def fetch_google_timezone(user_id: int) -> str | None:
    path = google_calendar_oauth.user_token_path(user_id)
    if not path.is_file():
        return None
    creds = Credentials.from_authorized_user_file(str(path))
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
    setting = svc.settings().get(setting="timezone").execute()
    value = str(setting.get("value") or "").strip()
    if value:
        user_prefs.set_timezone(user_id, value)
    return value or None


def resolve_user_tz_name(user_id: int, *, refresh: bool = False) -> str:
    if refresh or user_prefs.timezone_cache_stale(user_id):
        fetch_google_timezone(user_id)
    tz = user_prefs.get_timezone_name(user_id)
    if tz:
        return tz
    from assistant.config import CALENDAR_TZ

    return CALENDAR_TZ
