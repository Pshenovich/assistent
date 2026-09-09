"""Совместимость miniapp API с новым кодом (вместо удалённого bot.py)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from assistant.config import GOOGLE_CALENDAR_ID
from assistant.integrations import todoist_api
from assistant.integrations.todoist_api import DEFAULT_LABEL
from assistant.nlu import llm as nlu_llm
from assistant.services import calendar as cal_svc
from assistant.services import calendar_sources as cal_sources

TODOIST_DEFAULT_LABEL = DEFAULT_LABEL


def add_todoist_note(content: str, description: str = "", *, telegram_user_id: int) -> str:
    return todoist_api.add_note(telegram_user_id, content, description)


def fetch_todoist_notes(
    limit: int = 200,
    *,
    telegram_user_id: int,
    label_filter: str | None = None,
) -> list:
    return todoist_api.list_notes(
        telegram_user_id, limit=limit, label_filter=label_filter
    )


def set_todoist_task_content(
    task_id: str,
    *,
    content: str | None = None,
    description: str | None = None,
    telegram_user_id: int,
) -> None:
    raise NotImplementedError("Редактирование Todoist в miniapp — в разработке.")


def delete_todoist_task(task_id: str, *, telegram_user_id: int) -> None:
    raise NotImplementedError("Удаление Todoist в miniapp — в разработке.")


def gpt_openrouter_answer_with_context(
    question: str,
    context: str = "",
    *,
    history: list[dict[str, str]] | None = None,
    model: str | None = None,
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    knowledge_brief: str = "",
) -> dict:
    answer = nlu_llm.answer_with_context(
        question,
        context,
        model=model,
        history=history,
        note_title=note_title,
        note_text=note_text,
        quote=quote,
        knowledge_brief=knowledge_brief,
    )
    return {"answer": answer, "bullets": []}


def _calendar_tz():
    from zoneinfo import ZoneInfo
    from assistant.config import CALENDAR_TZ

    return ZoneInfo(CALENDAR_TZ)


def _day_bounds(user_id: int, day_iso: str) -> tuple[datetime, datetime]:
    tz = cal_svc._tz_for(user_id)
    day = date.fromisoformat(day_iso)
    start = datetime.combine(day, time.min, tzinfo=tz)
    return start, start + timedelta(days=1)


def calendar_free_slots(
    parsed: dict[str, Any],
    *,
    telegram_user_id: int,
    include_event_details: bool = False,
    telegram_username: str | None = None,
) -> dict[str, Any]:
    del telegram_username
    uid = int(telegram_user_id)
    day_iso = str(parsed.get("free_slots_date") or "").strip()[:10]
    if not day_iso:
        day_iso = datetime.now(cal_svc._tz_for(uid)).date().isoformat()
    slots = cal_svc.free_slots_day(uid, day_iso)
    out: dict[str, Any] = {
        "date": day_iso,
        "free_slot_starts": [s.isoformat() for s, _e in slots],
        "events": [],
    }
    if include_event_details:
        start, end = _day_bounds(uid, day_iso)
        for norm in cal_sources.list_events_in_window(uid, start, end):
            raw = dict(norm.get("raw") or {})
            raw["_calendarId"] = str(norm.get("calendar_id") or GOOGLE_CALENDAR_ID)
            out["events"].append(raw)
    return out


def calendar_update_event(
    event_id: str,
    parsed: dict[str, Any],
    *,
    telegram_user_id: int,
    telegram_username: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    del telegram_username
    return cal_svc.update_event(
        int(telegram_user_id),
        event_id,
        parsed,
        calendar_id=calendar_id,
    )


def calendar_create_event(
    parsed: dict[str, Any],
    *,
    telegram_user_id: int,
    telegram_username: str | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    return cal_svc.create_event(
        int(telegram_user_id),
        parsed,
        telegram_username=telegram_username,
        calendar_id=calendar_id,
    )


def calendar_availability(
    *,
    telegram_user_id: int,
    day_iso: str,
    attendees: list[str],
    telegram_username: str | None = None,
    attendee_refs: list[dict] | None = None,
) -> dict[str, Any]:
    return cal_svc.availability_for_attendees(
        int(telegram_user_id),
        day_iso,
        attendees,
        telegram_username=telegram_username,
        attendee_refs=attendee_refs,
    )


def calendar_delete_event(
    event_id: str,
    *,
    telegram_user_id: int,
    calendar_id: str | None = None,
) -> None:
    # delete_event также удаляет связанную Zoom-встречу (см. cal_svc.delete_event).
    cal_svc.delete_event(
        int(telegram_user_id), event_id, calendar_id=calendar_id
    )


def calendar_attach_zoom_link(
    event_id: str,
    *,
    telegram_user_id: int,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    from assistant.lib import zoom_gcal
    from assistant.lib.meeting_links import extract_online_meeting_url

    uid = int(telegram_user_id)
    if not zoom_gcal.user_has_zoom(uid):
        raise RuntimeError("Zoom не подключён.")
    ev = cal_svc.get_event(uid, event_id, calendar_id=calendar_id)
    existing = extract_online_meeting_url(ev)
    if existing and "zoom" in existing.lower():
        return {"join_url": existing, "already": True}
    start, end, _tz = zoom_gcal._event_times(ev, uid)
    join = zoom_gcal.attach_zoom_to_event(
        uid,
        event_id,
        start,
        end,
        str(ev.get("summary") or "Встреча"),
        calendar_id=calendar_id,
    )
    return {"join_url": join, "already": False}


def calendar_attach_telemost_link(
    event_id: str,
    *,
    telegram_user_id: int,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    from assistant.lib import telemost_gcal
    from assistant.lib.meeting_links import extract_online_meeting_url

    uid = int(telegram_user_id)
    if not telemost_gcal.user_has_telemost(uid):
        raise RuntimeError("Телемост не подключён.")
    ev = cal_svc.get_event(uid, event_id, calendar_id=calendar_id)
    existing = extract_online_meeting_url(ev)
    if existing and "telemost" in existing.lower():
        return {"join_url": existing, "already": True}
    join = telemost_gcal.attach_telemost_to_event(
        uid,
        event_id,
        str(ev.get("summary") or "Встреча"),
        calendar_id=calendar_id,
    )
    return {"join_url": join, "already": False}
