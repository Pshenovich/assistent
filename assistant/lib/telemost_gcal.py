"""Связка Yandex Telemost с событиями Google Calendar."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from assistant.config import (
    TELEMOST_AUTO_ATTACH_ON_CALENDAR_CREATE,
    TELEMOST_MEETING_PROP_KEY,
)
from assistant.integrations import telemost_api, telemost_oauth
from assistant.lib.meeting_links import extract_online_meeting_url
from assistant.lib.user_timezone import resolve_user_tz_name
from assistant.services import calendar as cal_svc
from assistant.stores import user_prefs

_TELEMOST_LINE_RE = re.compile(
    r"(?im)^\s*телемост\s*:\s*https?://\S+\s*$",
)


def auto_attach_enabled() -> bool:
    return TELEMOST_AUTO_ATTACH_ON_CALENDAR_CREATE


def user_has_telemost(user_id: int) -> bool:
    return telemost_oauth.user_token_path(int(user_id)).is_file()


def read_telemost_conference_id(event: dict[str, Any]) -> str:
    if not isinstance(event, dict):
        return ""
    priv = (event.get("extendedProperties") or {}).get("private") or {}
    if isinstance(priv, dict):
        return str(priv.get(TELEMOST_MEETING_PROP_KEY) or "").strip()
    return ""


def _telemost_access_token(user_id: int) -> str:
    if not user_has_telemost(user_id):
        raise RuntimeError("Телемост не подключён. Выполните /telemost_auth.")
    return telemost_oauth.get_access_token_for_user(int(user_id))


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


def _upsert_telemost_description(desc: str, join_url: str) -> str:
    url = (join_url or "").strip()
    if not url:
        return desc
    line = f"Телемост: {url}"
    base = _TELEMOST_LINE_RE.sub("", desc or "").strip()
    if base:
        return f"{base}\n\n{line}"
    return line


def _patch_telemost_metadata(
    user_id: int,
    event_id: str,
    *,
    conference_id: str,
    join_url: str,
    calendar_id: str | None = None,
) -> str:
    ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    desc = _upsert_telemost_description(str(ev.get("description") or ""), join_url)
    body: dict[str, Any] = {
        "description": desc,
        "extendedProperties": {
            "private": {
                **((ev.get("extendedProperties") or {}).get("private") or {}),
                TELEMOST_MEETING_PROP_KEY: str(conference_id),
            }
        },
    }
    loc = str(ev.get("location") or "").strip()
    if not loc or "telemost" in loc.lower() or "телемост" in loc.lower():
        body["location"] = join_url
    cal_svc.patch_event_fields(
        user_id, event_id, body, calendar_id=calendar_id
    )
    return join_url


def attach_telemost_to_event(
    user_id: int,
    event_id: str,
    topic: str,
    *,
    calendar_id: str | None = None,
) -> str:
    """Создать комнату Телемоста и записать ссылку в событие GCal."""
    del topic
    ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    existing = extract_online_meeting_url(ev)
    conf_id = read_telemost_conference_id(ev)
    if existing and "telemost" in existing.lower():
        return existing
    if conf_id:
        try:
            conf = telemost_api.get_conference(_telemost_access_token(user_id), conf_id)
            join = str(conf.get("join_url") or "").strip()
            if join:
                return _patch_telemost_metadata(
                    user_id,
                    event_id,
                    conference_id=conf_id,
                    join_url=join,
                    calendar_id=calendar_id,
                )
        except Exception as e:
            print(f"[telemost_gcal] get existing conf user={user_id} err={e!r}")
    token = _telemost_access_token(user_id)
    auto_summ = user_prefs.telemost_auto_record_enabled(user_id)
    conf = telemost_api.create_conference(token, auto_summarization=auto_summ)
    join = str(conf.get("join_url") or "").strip()
    conference_id = str(conf.get("id") or "")
    if not join or not conference_id:
        raise RuntimeError("Телемост не вернул ссылку на встречу.")
    return _patch_telemost_metadata(
        user_id,
        event_id,
        conference_id=conference_id,
        join_url=join,
        calendar_id=calendar_id,
    )


def delete_telemost_for_event(
    user_id: int,
    event_id: str,
    *,
    calendar_id: str | None = None,
) -> None:
    if not user_has_telemost(user_id):
        return
    try:
        ev = cal_svc.get_event(user_id, event_id, calendar_id=calendar_id)
    except Exception:
        return
    conf_id = read_telemost_conference_id(ev)
    if not conf_id:
        return
    try:
        telemost_api.delete_conference(_telemost_access_token(user_id), conf_id)
    except Exception as e:
        print(f"[telemost_gcal] delete_telemost_for_event user={user_id} err={e!r}")
        return
    desc = _TELEMOST_LINE_RE.sub("", str(ev.get("description") or "")).strip()
    priv = dict((ev.get("extendedProperties") or {}).get("private") or {})
    priv.pop(TELEMOST_MEETING_PROP_KEY, None)
    body: dict[str, Any] = {
        "description": desc,
        "extendedProperties": {"private": priv},
    }
    try:
        cal_svc.patch_event_fields(
            user_id, event_id, body, calendar_id=calendar_id
        )
    except Exception as e:
        print(f"[telemost_gcal] clear_gcal_telemost_meta user={user_id} err={e!r}")


def find_gcal_events_with_telemost(
    user_id: int,
    match_query: str,
    match_date: str | None,
) -> list[dict[str, Any]]:
    found = cal_svc.find_events_by_query(user_id, match_query, match_date)
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
        url = extract_online_meeting_url(ev) or ""
        if read_telemost_conference_id(ev) or (
            url and "telemost" in url.lower()
        ):
            out.append({**row, "_raw": ev})
    return out


def try_auto_attach_after_create(
    user_id: int,
    result: dict[str, Any],
) -> dict[str, Any]:
    if not auto_attach_enabled() or not user_has_telemost(user_id):
        return result
    eid = str(result.get("event_id") or "")
    if not eid:
        return result
    try:
        join = attach_telemost_to_event(
            user_id,
            eid,
            str(result.get("summary") or ""),
            calendar_id=str(result.get("calendar_id") or "").strip() or None,
        )
        if join:
            result = {**result, "telemost_join_url": join}
    except Exception as e:
        print(f"[telemost_gcal] auto_attach user={user_id} err={e!r}")
    return result
