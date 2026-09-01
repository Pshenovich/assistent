"""Связка Zoom-встреч с событиями Google Calendar."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from assistant.config import (
    ZOOM_AUTO_ATTACH_ON_CALENDAR_CREATE,
    ZOOM_MEETING_PROP_KEY,
)
from assistant.integrations import zoom_api, zoom_oauth
from assistant.lib.meeting_links import (
    extract_online_meeting_url,
    meeting_id_from_zoom_url,
)
from assistant.lib.user_timezone import resolve_user_tz_name
from assistant.services import calendar as cal_svc
from assistant.services import meeting_record_schedule as meeting_sched
from assistant.stores import user_prefs
from assistant.stores import zoom_recent_meetings as zrm

_ZOOM_LINE_RE = re.compile(
    r"(?im)^\s*zoom\s*:\s*https?://\S+\s*$",
)


def auto_attach_enabled() -> bool:
    return ZOOM_AUTO_ATTACH_ON_CALENDAR_CREATE


def user_has_zoom(user_id: int) -> bool:
    return zoom_oauth.user_token_path(int(user_id)).is_file()


def read_zoom_meeting_id(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""
    priv = (event.get("extendedProperties") or {}).get("private") or {}
    if isinstance(priv, dict):
        mid = str(priv.get(ZOOM_MEETING_PROP_KEY) or "").strip()
        if mid:
            return mid
    url = extract_online_meeting_url(event) or ""
    if url and "zoom" in url.lower():
        return meeting_id_from_zoom_url(url)
    return ""


def _zoom_access_token(user_id: int) -> str:
    if not user_has_zoom(user_id):
        raise RuntimeError("Zoom не подключён. Выполните /zoom_auth.")
    return zoom_oauth.get_access_token_for_user(int(user_id))


def _event_times(
    event: dict[str, Any], user_id: int
) -> tuple[datetime, datetime, str]:
    tz_name = resolve_user_tz_name(user_id)
    tz = cal_svc._tz_for(user_id)
    st_raw = (event.get("start") or {}).get("dateTime") or ""
    en_raw = (event.get("end") or {}).get("dateTime") or ""
    if not st_raw:
        raise RuntimeError("У события нет времени начала.")
    start = datetime.fromisoformat(str(st_raw).replace("Z", "+00:00")).astimezone(tz)
    if en_raw:
        end = datetime.fromisoformat(str(en_raw).replace("Z", "+00:00")).astimezone(tz)
    else:
        end = start + timedelta(hours=1)
    return start, end, tz_name


def _upsert_zoom_description(desc: str, join_url: str) -> str:
    url = (join_url or "").strip()
    if not url:
        return desc
    line = f"Zoom: {url}"
    base = _ZOOM_LINE_RE.sub("", desc or "").strip()
    if base:
        return f"{base}\n\n{line}"
    return line


def _patch_zoom_metadata(
    user_id: int,
    event_id: str,
    *,
    meeting_id: str,
    join_url: str,
    calendar_id: str | None = None,
) -> str:
    ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    desc = _upsert_zoom_description(str(ev.get("description") or ""), join_url)
    body: dict[str, Any] = {
        "description": desc,
        "extendedProperties": {
            "private": {
                **((ev.get("extendedProperties") or {}).get("private") or {}),
                ZOOM_MEETING_PROP_KEY: str(meeting_id),
            }
        },
    }
    loc = str(ev.get("location") or "").strip()
    if not loc or "zoom" in loc.lower():
        body["location"] = join_url
    cal_svc.patch_event_fields(
        user_id, event_id, body, calendar_id=calendar_id
    )
    return join_url


def attach_zoom_to_event(
    user_id: int,
    event_id: str,
    start: datetime,
    end: datetime,
    topic: str,
    *,
    calendar_id: str | None = None,
) -> str:
    """Создать запланированный Zoom и записать ссылку в событие GCal."""
    ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    existing = extract_online_meeting_url(ev)
    mid = read_zoom_meeting_id(ev)
    if existing and "zoom" in existing.lower():
        return existing
    if mid:
        meetings = zoom_api.list_upcoming_meetings(_zoom_access_token(user_id))
        for m in meetings:
            if str(m.get("id") or "") == mid:
                join = str(m.get("join_url") or "").strip()
                if join:
                    return _patch_zoom_metadata(
                        user_id,
                        event_id,
                        meeting_id=mid,
                        join_url=join,
                        calendar_id=calendar_id,
                    )
    token = _zoom_access_token(user_id)
    tz_name = resolve_user_tz_name(user_id)
    auto_record = user_prefs.zoom_auto_record_enabled(user_id) and meeting_sched.service_available()
    zm = zoom_api.create_scheduled_meeting(
        token,
        topic=topic or str(ev.get("summary") or "Встреча"),
        start_utc=start,
        end_utc=end,
        timezone_str=tz_name,
        auto_record=auto_record,
    )
    join = str(zm.get("join_url") or "").strip()
    meeting_id = str(zm.get("id") or "")
    if not join or not meeting_id:
        raise RuntimeError("Zoom не вернул ссылку на встречу.")
    zrm.register_meeting(user_id, zm, source="gcal_zoom")
    meeting_sched.maybe_schedule_for_zoom_create(
        user_id,
        join_url=join,
        topic=topic or str(ev.get("summary") or "Встреча"),
        start_at=start,
        source="gcal_zoom",
    )
    return _patch_zoom_metadata(
        user_id,
        event_id,
        meeting_id=meeting_id,
        join_url=join,
        calendar_id=calendar_id,
    )


def sync_zoom_after_calendar_update(
    user_id: int,
    event_id: str,
    parsed: dict[str, Any],
    *,
    calendar_id: str | None = None,
) -> None:
    if not parsed.get("start"):
        return
    ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    mid = read_zoom_meeting_id(ev)
    if not mid:
        return
    start = cal_svc._parse_dt(str(parsed["start"]), user_id)
    dur = int(parsed.get("duration_min") or 60)
    end_s = str(parsed.get("end") or "").strip()
    end = (
        cal_svc._parse_dt(end_s, user_id)
        if end_s
        else start + timedelta(minutes=max(15, dur))
    )
    token = _zoom_access_token(user_id)
    tz_name = resolve_user_tz_name(user_id)
    zoom_api.update_scheduled_meeting(
        token,
        mid,
        start_utc=start,
        end_utc=end,
        timezone_str=tz_name,
    )
    print(f"[zoom_gcal] PATCH meeting_id={mid} user={user_id}")
    join = extract_online_meeting_url(ev) or ""
    if join:
        _patch_zoom_metadata(
            user_id,
            event_id,
            meeting_id=mid,
            join_url=join,
            calendar_id=calendar_id,
        )


def delete_zoom_for_event(
    user_id: int,
    event_id: str,
    *,
    calendar_id: str | None = None,
) -> bool:
    """Удалить Zoom-встречу по событию GCal. True — DELETE к Zoom API выполнен."""
    if not user_has_zoom(user_id):
        return False
    try:
        ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    except Exception:
        return False
    mid = read_zoom_meeting_id(ev)
    if not mid:
        return False
    try:
        zoom_api.delete_meeting(_zoom_access_token(user_id), mid)
        print(f"[zoom_gcal] DELETE meeting_id={mid} user={user_id}")
    except Exception as e:
        print(f"[zoom_gcal] delete_zoom_for_event user={user_id} err={e!r}")
        return False
    zrm.remove_meeting(user_id, mid)
    desc = _ZOOM_LINE_RE.sub("", str(ev.get("description") or "")).strip()
    priv = dict((ev.get("extendedProperties") or {}).get("private") or {})
    priv.pop(ZOOM_MEETING_PROP_KEY, None)
    body: dict[str, Any] = {
        "description": desc,
        "extendedProperties": {"private": priv},
    }
    loc = str(ev.get("location") or "").strip()
    if loc and "zoom" in loc.lower():
        body["location"] = ""
    try:
        cal_svc.patch_event_fields(
            user_id, event_id, body, calendar_id=calendar_id
        )
    except Exception as e:
        print(f"[zoom_gcal] clear_gcal_zoom_meta user={user_id} err={e!r}")
    return True


def find_gcal_events_with_zoom(
    user_id: int,
    match_query: str,
    match_date: str | None,
) -> list[dict[str, Any]]:
    try:
        found = cal_svc.find_events_by_query(user_id, match_query, match_date)
        if not found and (match_date or "").strip():
            found = cal_svc.find_events_by_query(user_id, match_query, None)
    except Exception as e:
        print(f"[zoom_gcal] find_gcal_events_with_zoom user={user_id} gcal_skip={e!r}")
        return []
    out: list[dict[str, Any]] = []
    for row in found:
        eid = str(row.get("id") or "")
        if not eid:
            continue
        cal_id = str(row.get("calendar_id") or "").strip() or None
        try:
            ev = cal_svc.get_event(user_id, eid, calendar_id=cal_id)
        except Exception:
            continue
        if read_zoom_meeting_id(ev) or (
            extract_online_meeting_url(ev) and "zoom" in (extract_online_meeting_url(ev) or "").lower()
        ):
            out.append({**row, "_raw": ev})
    return out


def _match_zoom_meeting_topic(meeting: dict[str, Any], query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    topic = str(meeting.get("topic") or "").lower()
    return q in topic or topic in q


def find_bot_tracked_meetings(
    user_id: int,
    match_query: str,
    match_date: str | None,
) -> list[dict[str, Any]]:
    """Только встречи, созданные этим ботом (локальный трекинг)."""
    tz = cal_svc._tz_for(user_id)
    hits = zrm.list_recent(user_id, match_query, match_date, tz=tz)
    if not hits and (match_date or "").strip():
        hits = zrm.list_recent(user_id, match_query, None, tz=tz)
    return hits


def find_zoom_meetings(
    user_id: int,
    match_query: str,
    match_date: str | None,
) -> list[dict[str, Any]]:
    tz = cal_svc._tz_for(user_id)
    api_meetings: list[dict[str, Any]] = []
    # GET /users/me/meetings требует meeting:read:list_meetings. Без scope — 400 (4711);
    # тогда используем локальный трекинг встреч, созданных ботом.
    if zoom_oauth.user_has_granted_scope(user_id, "meeting:read:list_meetings"):
        try:
            token = _zoom_access_token(user_id)
            api_meetings = zoom_api.list_upcoming_meetings(token)
        except Exception as e:
            print(f"[zoom_gcal] list_upcoming_meetings user={user_id} err={e!r}")
    local_meetings = zrm.list_recent(user_id, match_query, match_date, tz=tz)
    # Если фильтр по дате ничего не дал (часто при reschedule: match_date = новый день),
    # повторяем поиск без даты — иначе только что созданная встреча «пропадает».
    if not local_meetings and (match_date or "").strip():
        local_meetings = zrm.list_recent(user_id, match_query, None, tz=tz)
    q = (match_query or "").strip().lower()
    day = (match_date or "").strip()[:10]
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def _accept_api(m: dict[str, Any]) -> bool:
        if q and not _match_zoom_meeting_topic(m, q):
            return False
        st_s = str(m.get("start_time") or "").strip()
        if day and st_s:
            try:
                st = datetime.fromisoformat(st_s.replace("Z", "+00:00")).astimezone(tz)
                if st.date().isoformat() != day:
                    return False
            except ValueError:
                pass
        return True

    for m in api_meetings:
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen or not _accept_api(m):
            continue
        seen.add(mid)
        out.append(m)
    for m in local_meetings:
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    return out


def try_auto_attach_after_create(
    user_id: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not auto_attach_enabled() or not user_has_zoom(user_id):
        return result
    eid = str(result.get("event_id") or "")
    st = result.get("start")
    en = result.get("end")
    if not eid or not isinstance(st, datetime) or not isinstance(en, datetime):
        return result
    try:
        join = attach_zoom_to_event(
            user_id,
            eid,
            st,
            en,
            str(result.get("summary") or ""),
            calendar_id=str(result.get("calendar_id") or "").strip() or None,
        )
        if join:
            result = {**result, "zoom_join_url": join}
    except Exception as e:
        print(f"[zoom_gcal] auto_attach user={user_id} err={e!r}")
    return result
