"""Список календарей Google и агрегация событий из нескольких источников."""

from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any

from assistant.config import GOOGLE_CALENDAR_ID
from assistant.integrations import google_calendar_oauth
from assistant.lib.calendar_event_utils import calendar_event_is_cancelled
from assistant.lib.meeting_links import extract_online_meeting_url
from assistant.stores import user_prefs

READABLE_ROLES = frozenset({"owner", "writer", "reader"})

_list_cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}


def _cache_ttl_sec() -> float:
    try:
        return float(os.getenv("CALENDAR_LIST_CACHE_TTL_SEC", "21600") or "21600")
    except ValueError:
        return 21600.0


def _service(user_id: int):
    from assistant.services.calendar import _service as cal_service

    return cal_service(user_id)


def _invalidate_list_cache(user_id: int) -> None:
    _list_cache.pop(int(user_id), None)


def list_readable_calendars(user_id: int, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Календари с правом чтения (owner/writer/reader)."""
    uid = int(user_id)
    now = time.time()
    if not force_refresh:
        cached = _list_cache.get(uid)
        if cached and (now - cached[0]) < _cache_ttl_sec():
            return list(cached[1])

    svc = _service(uid)
    out: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        req: dict[str, Any] = {"minAccessRole": "reader"}
        if page_token:
            req["pageToken"] = page_token
        res = svc.calendarList().list(**req).execute()
        for item in res.get("items") or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("accessRole") or "").strip().lower()
            if role not in READABLE_ROLES:
                continue
            cal_id = str(item.get("id") or "").strip()
            if not cal_id:
                continue
            out.append(
                {
                    "id": cal_id,
                    "summary": str(item.get("summary") or cal_id).strip(),
                    "backgroundColor": str(item.get("backgroundColor") or "").strip(),
                    "accessRole": role,
                    "primary": bool(item.get("primary")),
                    "selected": item.get("selected") is not False,
                    "hidden": bool(item.get("hidden")),
                }
            )
        page_token = str(res.get("nextPageToken") or "").strip() or None
        if not page_token:
            break

    _list_cache[uid] = (now, out)
    return out


def get_calendar_excluded_ids(user_id: int) -> set[str]:
    return set(user_prefs.get_calendar_excluded_ids(user_id))


def get_active_calendar_ids(user_id: int) -> list[str]:
    excluded = get_calendar_excluded_ids(user_id)
    ids = [
        c["id"]
        for c in list_readable_calendars(user_id)
        if c.get("id") and c["id"] not in excluded
    ]
    if not ids:
        return [GOOGLE_CALENDAR_ID]
    return ids


def resolve_calendar_id(user_id: int, calendar_id: str | None) -> str:
    """Google API принимает primary, но в calendarList id часто email — нормализуем."""
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    if cal_id != "primary":
        return cal_id
    for c in list_readable_calendars(user_id):
        if c.get("primary") and c.get("id"):
            return str(c["id"])
    return "primary"


def get_calendar_access_role(user_id: int, calendar_id: str) -> str | None:
    cal_id = (calendar_id or "").strip()
    for c in list_readable_calendars(user_id):
        cid = str(c.get("id") or "")
        if cid == cal_id:
            return str(c.get("accessRole") or "")
        if cal_id in ("", "primary") and c.get("primary"):
            return str(c.get("accessRole") or "")
    return None


def assert_calendar_writable(user_id: int, calendar_id: str) -> None:
    role = get_calendar_access_role(user_id, calendar_id) or ""
    if role == "reader":
        raise RuntimeError(
            "Этот календарь доступен только для просмотра (например, подписка Bitrix). "
            "Изменить или удалить встречу можно в Google Calendar."
        )
    if role not in READABLE_ROLES:
        raise RuntimeError("Календарь недоступен.")


def _event_start_end_raw(
    ev: dict[str, Any], user_id: int
) -> tuple[datetime | None, datetime | None]:
    from assistant.services.calendar import _tz_for

    tz = _tz_for(user_id)
    st = ev.get("start") or {}
    en = ev.get("end") or {}
    ds = st.get("dateTime")
    de = en.get("dateTime")
    if not ds or not de:
        return None, None
    try:
        start = datetime.fromisoformat(str(ds).replace("Z", "+00:00")).astimezone(tz)
        end = datetime.fromisoformat(str(de).replace("Z", "+00:00")).astimezone(tz)
        return start, end
    except ValueError:
        return None, None


def _normalize_event(
    ev: dict[str, Any], *, calendar_id: str, user_id: int
) -> dict[str, Any] | None:
    if calendar_event_is_cancelled(ev):
        return None
    start, end = _event_start_end_raw(ev, user_id)
    if not start or not end:
        return None
    return {
        "calendar_id": calendar_id,
        "event_id": str(ev.get("id") or ""),
        "summary": str(ev.get("summary") or "Встреча"),
        "html_link": str(ev.get("htmlLink") or "").strip(),
        "start": start,
        "end": end,
        "start_iso": start.replace(tzinfo=None).isoformat(timespec="seconds"),
        "ical_uid": str(ev.get("iCalUID") or "").strip(),
        "raw": ev,
    }


def _event_preference_key(norm: dict[str, Any]) -> tuple[int, int]:
    cal_id = str(norm.get("calendar_id") or "")
    raw = norm.get("raw") if isinstance(norm.get("raw"), dict) else {}
    is_primary = 1 if cal_id == "primary" or cal_id == GOOGLE_CALENDAR_ID else 0
    has_meet = 1 if extract_online_meeting_url(raw) else 0
    return (is_primary, has_meet)


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_uid: dict[str, dict[str, Any]] = {}
    without_uid: list[dict[str, Any]] = []
    for ev in events:
        uid = str(ev.get("ical_uid") or "").strip()
        if not uid:
            without_uid.append(ev)
            continue
        prev = by_uid.get(uid)
        if not prev or _event_preference_key(ev) > _event_preference_key(prev):
            by_uid[uid] = ev
    merged = list(by_uid.values()) + without_uid
    merged.sort(
        key=lambda e: (
            e.get("start").isoformat()
            if isinstance(e.get("start"), datetime)
            else ""
        )
    )
    return merged


def list_events_in_window(
    user_id: int,
    time_min: datetime,
    time_max: datetime,
    *,
    calendar_ids: list[str] | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    svc = _service(user_id)
    ids = calendar_ids or get_active_calendar_ids(user_id)
    q = (query or "").strip() or None
    collected: list[dict[str, Any]] = []
    for cal_id in ids:
        try:
            kwargs: dict[str, Any] = {
                "calendarId": cal_id,
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
            }
            if q:
                kwargs["q"] = q
            res = svc.events().list(**kwargs).execute()
        except Exception as e:
            print(f"[calendar_sources] list_failed uid={user_id} cal={cal_id} err={e!r}")
            continue
        for ev in res.get("items") or []:
            if not isinstance(ev, dict):
                continue
            norm = _normalize_event(ev, calendar_id=cal_id, user_id=user_id)
            if norm:
                collected.append(norm)
    return dedupe_events(collected)


def list_raw_events_day(user_id: int, day_iso: str) -> list[dict[str, Any]]:
    """Сырые события Google API за день (после дедупа), с полем _calendarId."""
    from datetime import date, time, timedelta

    from assistant.services.calendar import _tz_for

    day = date.fromisoformat(day_iso)
    tz = _tz_for(user_id)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    out: list[dict[str, Any]] = []
    for norm in list_events_in_window(user_id, start, end):
        raw = norm.get("raw")
        if not isinstance(raw, dict):
            continue
        tagged = dict(raw)
        tagged["_calendarId"] = str(norm.get("calendar_id") or GOOGLE_CALENDAR_ID)
        out.append(tagged)
    return out


def calendar_sources_for_miniapp(user_id: int) -> list[dict[str, Any]]:
    excluded = get_calendar_excluded_ids(user_id)
    return [
        {
            "id": c["id"],
            "summary": c.get("summary") or c["id"],
            "backgroundColor": c.get("backgroundColor") or "",
            "accessRole": c.get("accessRole") or "",
            "primary": bool(c.get("primary")),
            "excluded": c["id"] in excluded,
        }
        for c in list_readable_calendars(user_id)
    ]


def set_calendar_excluded(user_id: int, excluded_ids: list[str]) -> None:
    """Сохранить список отключённых календарей (id из calendarList)."""
    known = {str(c.get("id") or "").strip() for c in list_readable_calendars(user_id)}
    known.discard("")
    cleaned = [str(x).strip() for x in (excluded_ids or []) if str(x).strip()]
    if known:
        cleaned = [x for x in cleaned if x in known]
    user_prefs.set_calendar_excluded_ids(user_id, cleaned)
    _invalidate_list_cache(user_id)
