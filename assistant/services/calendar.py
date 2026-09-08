"""Google Calendar для встреч (Leo)."""

from __future__ import annotations

import html
import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from assistant.config import (
    DEFAULT_EVENT_TITLE,
    GOOGLE_CALENDAR_ID,
    SLOT_MIN_MINUTES,
    WORK_HOURS_END,
    WORK_HOURS_START,
)
from assistant.integrations import google_calendar_oauth
from assistant.lib import calendar_action_tokens
from assistant.lib.calendar_attendees import infer_event_title_from_text, resolve_attendee_names
from assistant.lib.calendar_event_utils import (
    calendar_busy_from_events,
    calendar_event_is_cancelled,
    calendar_merge_busy_intervals,
)
from assistant.lib.user_timezone import resolve_user_tz_name
from assistant.services import calendar_sources as cal_sources
from assistant.stores import user_prefs


def _tz_for(user_id: int) -> ZoneInfo:
    return user_prefs.get_user_tz(user_id)


def _tz_name_for(user_id: int) -> str:
    return resolve_user_tz_name(user_id)


def _service(user_id: int):
    path = google_calendar_oauth.user_token_path(user_id)
    if not path.is_file():
        raise RuntimeError(
            "Календарь не подключён. Выполните /calendar_auth в боте."
        )
    creds = Credentials.from_authorized_user_file(str(path))
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_dt(s: str, user_id: int) -> datetime:
    raw = (s or "").strip()
    if not raw:
        raise ValueError("empty datetime")
    if "T" not in raw:
        raw = f"{raw}T09:00:00"
    dt = datetime.fromisoformat(raw)
    tz = _tz_for(user_id)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt


_GENERIC_EVENT_TITLES = frozenset({"встреча", "созвон", "митинг", "meeting", "event"})


def _title_is_generic(title: str) -> bool:
    t = (title or "").strip().lower()
    return not t or t in _GENERIC_EVENT_TITLES


def normalize_event_title(parsed: dict[str, Any]) -> str:
    title = str(parsed.get("title") or "").strip()
    if _title_is_generic(title):
        return DEFAULT_EVENT_TITLE
    return title


_MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def unresolved_attendee_names(
    user_id: int,
    parsed: dict[str, Any],
    *,
    telegram_username: str | None = None,
) -> list[str]:
    names = [str(n).strip() for n in (parsed.get("attendee_names") or []) if str(n).strip()]
    _, missing = resolve_attendee_names(
        user_id, names, telegram_username=telegram_username
    )
    return missing


def _resolve_attendees(
    parsed: dict[str, Any],
    user_id: int,
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[dict[str, str]]:
    skip = {str(x).strip().lower() for x in (skip_names or set())}
    emails: list[str] = []
    for em in parsed.get("attendees") or []:
        e = str(em).strip().lower()
        if e and "@" in e:
            emails.append(e)
    names = [
        str(n).strip()
        for n in (parsed.get("attendee_names") or [])
        if str(n).strip() and str(n).strip().lower() not in skip
    ]
    resolved, _missing = resolve_attendee_names(
        user_id, names, telegram_username=telegram_username
    )
    for row in resolved:
        emails.append(row["email"])
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for e in emails:
        if e not in seen:
            seen.add(e)
            out.append({"email": e})
    return out


def event_attendee_emails(ev: dict[str, Any] | None) -> list[str]:
    """Email гостей Google-события без организатора."""
    out: list[str] = []
    seen: set[str] = set()
    for row in (ev or {}).get("attendees") or []:
        if not isinstance(row, dict):
            continue
        if row.get("self") or row.get("organizer"):
            continue
        em = str(row.get("email") or "").strip().lower()
        if not em or em in seen:
            continue
        seen.add(em)
        out.append(em)
    return out


def format_event_when(start: datetime, end: datetime, user_id: int) -> str:
    tz = _tz_for(user_id)
    s = start.astimezone(tz) if start.tzinfo else start.replace(tzinfo=tz)
    e = end.astimezone(tz) if end.tzinfo else end.replace(tzinfo=tz)
    date_part = f"{s.day} {_MONTHS_RU[s.month - 1]} {s.year}"
    return f"{date_part}, {s.strftime('%H:%M')}–{e.strftime('%H:%M')}"


def create_event(
    user_id: int,
    parsed: dict[str, Any],
    *,
    telegram_username: str | None = None,
    skip_attendee_names: set[str] | None = None,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    svc = _service(user_id)
    tz_name = _tz_name_for(user_id)
    start = _parse_dt(str(parsed.get("start") or ""), user_id)
    end_s = str(parsed.get("end") or "").strip()
    if end_s:
        end = _parse_dt(end_s, user_id)
    else:
        dur = int(parsed.get("duration_min") or 60)
        end = start + timedelta(minutes=max(15, dur))
    title = normalize_event_title(parsed)
    parsed["title"] = title
    body: dict[str, Any] = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
    }
    desc = str(parsed.get("description") or "").strip()
    if desc:
        body["description"] = desc
    loc = str(parsed.get("location") or "").strip()
    if loc:
        body["location"] = loc
    attendees = _resolve_attendees(
        parsed,
        user_id,
        telegram_username=telegram_username,
        skip_names=skip_attendee_names,
    )
    if attendees:
        body["attendees"] = attendees
    insert_cal = cal_sources.resolve_calendar_id(
        user_id, (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    )
    cal_sources.assert_calendar_writable(user_id, insert_cal)
    event = svc.events().insert(
        calendarId=insert_cal,
        body=body,
        sendUpdates="all" if attendees else "none",
    ).execute()
    link = str(event.get("htmlLink") or "").strip()
    return {
        "event_id": str(event.get("id") or ""),
        "calendar_id": insert_cal,
        "html_link": link,
        "summary": event.get("summary") or title,
        "start": start,
        "end": end,
        "attendee_emails": [a["email"] for a in attendees],
    }


def _list_raw_events_day(_svc, user_id: int, day_iso: str) -> list[dict[str, Any]]:
    return cal_sources.list_raw_events_day(user_id, day_iso)


def _raw_events_for_day(
    user_id: int,
    day_iso: str,
    *,
    calendar_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    if not calendar_ids:
        return cal_sources.list_raw_events_day(user_id, day_iso)
    day = date.fromisoformat(day_iso[:10])
    tz = _tz_for(user_id)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    out: list[dict[str, Any]] = []
    for norm in cal_sources.list_events_in_window(
        user_id, start, end, calendar_ids=calendar_ids
    ):
        raw = norm.get("raw")
        if not isinstance(raw, dict):
            continue
        tagged = dict(raw)
        tagged["_calendarId"] = str(norm.get("calendar_id") or GOOGLE_CALENDAR_ID)
        out.append(tagged)
    return out


def _primary_calendar_id(user_id: int) -> str | None:
    for cal in cal_sources.list_readable_calendars(user_id):
        if cal.get("primary"):
            cid = str(cal.get("id") or "").strip()
            if cid:
                return cid
    return None


def list_events_day(user_id: int, day_iso: str) -> list[dict[str, Any]]:
    svc = _service(user_id)
    out: list[dict[str, Any]] = []
    for ev in _list_raw_events_day(svc, user_id, day_iso):
        if calendar_event_is_cancelled(ev):
            continue
        st = ev.get("start") or {}
        t = st.get("dateTime") or st.get("date") or ""
        out.append(
            {
                "id": ev.get("id"),
                "calendar_id": str(ev.get("_calendarId") or GOOGLE_CALENDAR_ID),
                "summary": ev.get("summary") or "(без названия)",
                "start": t,
                "end": (ev.get("end") or {}).get("dateTime") or "",
                "link": ev.get("htmlLink") or "",
            }
        )
    return out


def _work_window(user_id: int, day_iso: str) -> tuple[datetime, datetime]:
    tz = _tz_for(user_id)
    day = date.fromisoformat(day_iso)
    sh, sm = [int(x) for x in WORK_HOURS_START.split(":", 1)]
    eh, em = [int(x) for x in WORK_HOURS_END.split(":", 1)]
    return (
        datetime.combine(day, time(sh, sm), tzinfo=tz),
        datetime.combine(day, time(eh, em), tzinfo=tz),
    )


def _busy_intervals_to_iso(
    intervals: list[tuple[datetime, datetime]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for start, end in intervals:
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            continue
        if end <= start:
            continue
        out.append({"start": start.isoformat(), "end": end.isoformat()})
    return out


def _clip_busy_window(
    start: datetime,
    end: datetime,
    *,
    work_start: datetime,
    work_end: datetime,
) -> tuple[datetime, datetime] | None:
    tz = work_start.tzinfo
    if tz is not None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        else:
            start = start.astimezone(tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        else:
            end = end.astimezone(tz)
    s = max(start, work_start)
    e = min(end, work_end)
    if s < e:
        return (s, e)
    return None


def _busy_from_freebusy_payload(
    res: dict[str, Any],
    calendar_ids: list[str],
    *,
    work_start: datetime,
    work_end: datetime,
) -> tuple[list[tuple[datetime, datetime]], bool]:
    calendars = res.get("calendars") or {}
    extra: list[tuple[datetime, datetime]] = []
    any_ok = False
    for cid in calendar_ids:
        cal = calendars.get(cid) or calendars.get(str(cid).lower()) or {}
        if not isinstance(cal, dict):
            continue
        if cal.get("errors"):
            continue
        any_ok = True
        for block in cal.get("busy") or []:
            if not isinstance(block, dict):
                continue
            bs = block.get("start")
            be = block.get("end")
            if not bs or not be:
                continue
            try:
                b_start = datetime.fromisoformat(str(bs).replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(str(be).replace("Z", "+00:00"))
            except ValueError:
                continue
            clipped = _clip_busy_window(
                b_start, b_end, work_start=work_start, work_end=work_end
            )
            if clipped:
                extra.append(clipped)
    return calendar_merge_busy_intervals(extra), any_ok


def busy_intervals_via_freebusy(
    user_id: int,
    work_start: datetime,
    work_end: datetime,
    *,
    calendar_ids: list[str] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Занятость по FreeBusy токена пользователя (без названий встреч)."""
    try:
        svc = _service(int(user_id))
        ids = [str(x).strip() for x in (calendar_ids or []) if str(x).strip()]
        if not ids:
            ids = list(cal_sources.get_active_calendar_ids(int(user_id)) or [])
        if not ids:
            ids = ["primary"]
        body = {
            "timeMin": work_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeMax": work_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "items": [{"id": cid} for cid in ids],
        }
        res = svc.freebusy().query(body=body).execute()
    except Exception as e:
        print(f"[calendar] freebusy uid={user_id} err={e!r}")
        return []
    busy, _ok = _busy_from_freebusy_payload(
        res if isinstance(res, dict) else {},
        ids,
        work_start=work_start,
        work_end=work_end,
    )
    return busy


def busy_intervals_via_viewer_email(
    viewer_id: int,
    email: str,
    work_start: datetime,
    work_end: datetime,
) -> list[tuple[datetime, datetime]] | None:
    """Занятость чужого календаря через FreeBusy зрителя. None — календарь не виден."""
    em = str(email or "").strip()
    if not em or "@" not in em:
        return None
    try:
        svc = _service(int(viewer_id))
        body = {
            "timeMin": work_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeMax": work_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "items": [{"id": em}],
        }
        res = svc.freebusy().query(body=body).execute()
    except Exception as e:
        print(f"[calendar] freebusy viewer={viewer_id} email={em} err={e!r}")
        return None
    busy, any_ok = _busy_from_freebusy_payload(
        res if isinstance(res, dict) else {},
        [em],
        work_start=work_start,
        work_end=work_end,
    )
    if not any_ok:
        return None
    return busy


def busy_intervals_day(
    user_id: int,
    day_iso: str,
    *,
    tz: Any | None = None,
    work_start: datetime | None = None,
    work_end: datetime | None = None,
) -> list[tuple[datetime, datetime]]:
    """Занятые интервалы дня без названий событий."""
    try:
        if work_start is None or work_end is None:
            work_start, work_end = _work_window(user_id, day_iso)
        if tz is None:
            tz = work_start.tzinfo or _tz_for(user_id)
        busy = busy_intervals_via_freebusy(
            int(user_id), work_start, work_end
        )
        if busy:
            return busy
        events = _raw_events_for_day(user_id, day_iso)
        return calendar_busy_from_events(
            events, tz=tz, work_start=work_start, work_end=work_end
        )
    except Exception as e:
        print(f"[calendar] busy_intervals uid={user_id} day={day_iso} err={e!r}")
        return []


def availability_for_attendees(
    owner_id: int,
    day_iso: str,
    attendee_emails: list[str],
    *,
    telegram_username: str | None = None,
    attendee_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Занятость постановщика и участников с календарём Leo. Без названий встреч."""
    from assistant.lib.calendar_attendees import (
        contact_display_name,
        leo_calendar_user_id,
    )
    from assistant.stores import contacts_store as contacts
    from assistant.stores.contacts_store import normalize_telegram_username

    day = str(day_iso or "").strip()[:10]
    datetime.strptime(day, "%Y-%m-%d")
    work_start, work_end = _work_window(int(owner_id), day)
    tz = work_start.tzinfo or _tz_for(int(owner_id))
    owner_has_cal = google_calendar_oauth.user_token_path(int(owner_id)).is_file()
    owner_busy = (
        busy_intervals_day(
            int(owner_id),
            day,
            tz=tz,
            work_start=work_start,
            work_end=work_end,
        )
        if owner_has_cal
        else []
    )
    people: list[dict[str, Any]] = [
        {
            "id": "organizer",
            "email": "",
            "label": "Вы",
            "kind": "organizer",
            "calendar": bool(owner_has_cal),
            "busy": _busy_intervals_to_iso(owner_busy),
        }
    ]
    book = contacts.load_contacts(
        telegram_user_id=int(owner_id), telegram_username=telegram_username
    )
    by_email: dict[str, dict[str, Any]] = {}
    for row in book:
        em = str((row or {}).get("email") or "").strip().lower()
        if em:
            by_email[em] = row
    refs: list[dict[str, Any]] = []
    if attendee_refs:
        refs = [r for r in attendee_refs if isinstance(r, dict)]
    else:
        refs = [{"email": e, "telegram_username": ""} for e in (attendee_emails or [])]
    seen: set[str] = set()
    for raw in refs:
        em = str((raw or {}).get("email") or "").strip().lower()
        if not em or "@" not in em or em in seen:
            continue
        seen.add(em)
        hit = by_email.get(em)
        uname = normalize_telegram_username(
            str((raw or {}).get("telegram_username") or "")
        )
        uid = leo_calendar_user_id(hit, em, telegram_username=uname or None)
        if uid and int(uid) == int(owner_id):
            continue
        label = ""
        if hit:
            label = str(contact_display_name(hit) or hit.get("name") or "").strip()
        busy: list[tuple[datetime, datetime]] = []
        has_cal = False
        if uid:
            has_cal = True
            busy = busy_intervals_day(
                int(uid),
                day,
                tz=tz,
                work_start=work_start,
                work_end=work_end,
            )
        else:
            via = busy_intervals_via_viewer_email(
                int(owner_id), em, work_start, work_end
            )
            if via is not None:
                has_cal = True
                busy = via
        people.append(
            {
                "id": em,
                "email": em,
                "label": label or em,
                "kind": "contact" if hit else "email",
                "calendar": has_cal,
                "busy": _busy_intervals_to_iso(busy),
            }
        )
    return {
        "date": day,
        "timezone": _tz_name_for(int(owner_id)),
        "work_start": work_start.isoformat(),
        "work_end": work_end.isoformat(),
        "people": people,
    }


def earliest_bookable_start(
    user_id: int,
    day_iso: str,
    *,
    grace_min: int = 15,
    step_min: int = 30,
) -> datetime:
    from assistant.lib.calendar_slots import compute_earliest_bookable_start

    tz = _tz_for(user_id)
    now = datetime.now(tz)
    work_start, work_end = _work_window(user_id, day_iso)
    try:
        day = date.fromisoformat(str(day_iso)[:10])
    except ValueError:
        return work_start
    return compute_earliest_bookable_start(
        now=now,
        day=day,
        work_start=work_start,
        work_end=work_end,
        grace_min=grace_min,
        step_min=step_min,
    )


def _gaps_from_busy(
    busy: list[tuple[datetime, datetime]],
    *,
    work_start: datetime,
    work_end: datetime,
    cursor: datetime,
) -> list[tuple[datetime, datetime]]:
    min_slot = timedelta(minutes=max(15, SLOT_MIN_MINUTES))
    slots: list[tuple[datetime, datetime]] = []
    cur = cursor
    for bs, be in busy:
        if cur < bs and (bs - cur) >= min_slot:
            slots.append((cur, bs))
        cur = max(cur, be)
    if cur < work_end and (work_end - cur) >= min_slot:
        slots.append((cur, work_end))
    return slots


def free_slots_day(
    user_id: int,
    day_iso: str,
    *,
    calendar_ids: list[str] | None = None,
) -> list[tuple[datetime, datetime]]:
    work_start, work_end = _work_window(user_id, day_iso)
    tz = _tz_for(user_id)
    events = _raw_events_for_day(user_id, day_iso, calendar_ids=calendar_ids)
    busy = calendar_busy_from_events(
        events, tz=tz, work_start=work_start, work_end=work_end
    )
    cursor = max(work_start, earliest_bookable_start(user_id, day_iso))
    return _gaps_from_busy(
        busy, work_start=work_start, work_end=work_end, cursor=cursor
    )


def free_slots_day_via_freebusy(
    viewer_id: int,
    calendar_ids: list[str],
    day_iso: str,
) -> list[tuple[datetime, datetime]] | None:
    """Слоты чужого календаря через FreeBusy (email как id). None — календарь не виден."""
    ids = [str(x).strip() for x in calendar_ids if str(x).strip()]
    if not ids:
        return None
    try:
        svc = _service(viewer_id)
    except Exception:
        return None
    work_start, work_end = _work_window(viewer_id, day_iso)
    body = {
        "timeMin": work_start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timeMax": work_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [{"id": cid} for cid in ids],
    }
    try:
        res = svc.freebusy().query(body=body).execute()
    except Exception:
        return None
    calendars = res.get("calendars") or {}
    extra: list[tuple[datetime, datetime]] = []
    any_ok = False
    for cid in ids:
        cal = calendars.get(cid) or {}
        if not isinstance(cal, dict):
            continue
        if cal.get("errors"):
            continue
        any_ok = True
        for block in cal.get("busy") or []:
            if not isinstance(block, dict):
                continue
            bs = block.get("start")
            be = block.get("end")
            if not bs or not be:
                continue
            try:
                b_start = datetime.fromisoformat(str(bs).replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(str(be).replace("Z", "+00:00"))
            except ValueError:
                continue
            extra.append(
                (
                    b_start.astimezone(work_start.tzinfo),
                    b_end.astimezone(work_start.tzinfo),
                )
            )
    if not any_ok:
        return None
    busy = calendar_merge_busy_intervals(extra)
    cursor = max(work_start, earliest_bookable_start(viewer_id, day_iso))
    return _gaps_from_busy(
        busy, work_start=work_start, work_end=work_end, cursor=cursor
    )


def format_free_slots(
    slots: list[tuple[datetime, datetime]],
    day_iso: str,
    *,
    person: str | None = None,
) -> str:
    who = f" у {person}" if (person or "").strip() else ""
    if not slots:
        return f"На {day_iso} свободных окон{who} в рабочее время нет."
    parts = []
    for s, e in slots:
        parts.append(f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}")
    return f"Свободно{who} {day_iso}: " + ", ".join(parts)


def free_slots_message(
    owner_id: int,
    parsed: dict[str, Any],
    day_iso: str,
    *,
    telegram_username: str | None = None,
) -> str:
    from assistant.lib.calendar_attendees import resolve_free_slots_person

    target = resolve_free_slots_person(
        owner_id, parsed, telegram_username=telegram_username
    )
    kind = str(target.get("kind") or "owner")
    label = str(target.get("label") or "").strip()
    if kind == "missing_contact":
        return (
            f"В контактах нет «{label}». Добавьте человека в адресную книгу "
            "с email — тогда можно смотреть его слоты."
        )
    if kind == "missing_calendar":
        return (
            f"Календарь {label} недоступен: нет привязки к боту и нет email "
            "для проверки занятости."
        )
    via_email = bool(target.get("via_email"))
    email = str(target.get("email") or "").strip()
    uid = int(target.get("user_id") or owner_id)
    person = label if kind == "person" else None
    try:
        if via_email and email:
            slots = free_slots_day_via_freebusy(int(owner_id), [email], day_iso)
            if slots is None:
                return (
                    f"Не удалось посмотреть календарь {label}: человек не подключал "
                    "Google Календарь в боте, и ваш аккаунт не видит его занятость."
                )
        else:
            slots = free_slots_day(uid, day_iso)
    except Exception as e:
        return f"Ошибка календаря: {e}"
    return format_free_slots(slots, day_iso, person=person)


def search_events(
    user_id: int,
    query: str,
    *,
    days_ahead: int = 90,
) -> list[dict[str, Any]]:
    tz = _tz_for(user_id)
    now = datetime.now(tz)
    end = now + timedelta(days=days_ahead)
    q = (query or "").strip()
    needle = q.lower()
    out: list[dict[str, Any]] = []
    for norm in cal_sources.list_events_in_window(
        user_id, now, end, query=q or None
    ):
        summary = str(norm.get("summary") or "")
        if needle and needle not in summary.lower():
            continue
        start = norm.get("start")
        t = start.isoformat() if isinstance(start, datetime) else ""
        raw = norm.get("raw") if isinstance(norm.get("raw"), dict) else {}
        out.append(
            {
                "id": norm.get("event_id"),
                "calendar_id": str(norm.get("calendar_id") or GOOGLE_CALENDAR_ID),
                "summary": summary or "(без названия)",
                "start": t,
                "end": (
                    norm.get("end").isoformat()
                    if isinstance(norm.get("end"), datetime)
                    else ""
                ),
                "link": str(norm.get("html_link") or raw.get("htmlLink") or ""),
            }
        )
        if len(out) >= 10:
            break
    return out


def _event_start_local(ev: dict[str, Any], user_id: int) -> datetime | None:
    st = ev.get("start") or {}
    raw = st.get("dateTime") or st.get("date")
    if not raw:
        return None
    tz = _tz_for(user_id)
    try:
        if "T" in str(raw):
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return dt.astimezone(tz)
        d = date.fromisoformat(str(raw)[:10])
        return datetime.combine(d, time(9, 0), tzinfo=tz)
    except ValueError:
        return None


def _match_summary(summary: str, query: str) -> bool:
    s = (summary or "").lower()
    q = (query or "").lower().strip()
    if not q:
        return True
    if q in s:
        return True
    parts = [p for p in q.split() if len(p) >= 3]
    return bool(parts) and all(p in s for p in parts)


def find_events_by_query(
    user_id: int,
    match_query: str,
    match_date: str | None = None,
) -> list[dict[str, Any]]:
    q = (match_query or "").strip()
    if match_date:
        events = list_events_day(user_id, match_date)
        if q:
            events = [
                e for e in events if _match_summary(str(e.get("summary") or ""), q)
            ]
        return events
    return search_events(user_id, q, days_ahead=60)


find_events_for_update = find_events_by_query


def update_event(
    user_id: int,
    event_id: str,
    parsed: dict[str, Any],
    *,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    cal_sources.assert_calendar_writable(user_id, cal_id)
    svc = _service(user_id)
    tz_name = _tz_name_for(user_id)
    ev = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
    body: dict[str, Any] = {}
    title = str(parsed.get("title") or "").strip()
    if title:
        body["summary"] = title
    if parsed.get("start"):
        start = _parse_dt(str(parsed["start"]), user_id)
        dur = int(parsed.get("duration_min") or 60)
        end_s = str(parsed.get("end") or "").strip()
        end = _parse_dt(end_s, user_id) if end_s else start + timedelta(minutes=max(15, dur))
        body["start"] = {"dateTime": start.isoformat(), "timeZone": tz_name}
        body["end"] = {"dateTime": end.isoformat(), "timeZone": tz_name}
    if "description" in parsed:
        body["description"] = str(parsed.get("description") or "")
    if "location" in parsed:
        body["location"] = str(parsed.get("location") or "").strip()
    prev_emails = event_attendee_emails(ev)
    attendees = _resolve_attendees(parsed, user_id)
    attendees_touched = (
        parsed.get("attendee_names") is not None or parsed.get("attendees") is not None
    )
    if attendees_touched:
        body["attendees"] = attendees
    patched = svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body=body,
        sendUpdates="all" if (attendees if attendees_touched else prev_emails) else "none",
    ).execute()
    st = _event_start_local(patched, user_id)
    en_raw = (patched.get("end") or {}).get("dateTime")
    en = None
    if en_raw:
        try:
            en = datetime.fromisoformat(str(en_raw).replace("Z", "+00:00")).astimezone(
                _tz_for(user_id)
            )
        except ValueError:
            pass
    attendee_emails = (
        [a["email"] for a in attendees] if attendees_touched else list(prev_emails)
    )
    return {
        "event_id": event_id,
        "calendar_id": cal_id,
        "html_link": str(patched.get("htmlLink") or ""),
        "summary": patched.get("summary") or title,
        "start": st,
        "end": en,
        "attendee_emails": attendee_emails,
        "previous_attendee_emails": prev_emails,
    }


def consume_delete_token(
    token: str, *, user_id: int
) -> tuple[str, str] | None:
    return calendar_action_tokens.consume(token, user_id=user_id)


def delete_event(
    user_id: int, event_id: str, *, calendar_id: str | None = None
) -> None:
    """Удалить событие GCal; если к нему привязаны Zoom/Telemost — удалить и их."""
    from assistant.lib import telemost_gcal, zoom_gcal

    try:
        zoom_gcal.delete_zoom_for_event(
            user_id, event_id, calendar_id=calendar_id
        )
    except Exception as e:
        print(f"[calendar] delete_event zoom cleanup user={user_id} err={e!r}")
    try:
        telemost_gcal.delete_telemost_for_event(
            user_id, event_id, calendar_id=calendar_id
        )
    except Exception as e:
        print(f"[calendar] delete_event telemost cleanup user={user_id} err={e!r}")
    cal_id = cal_sources.resolve_calendar_id(
        user_id, (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    )
    cal_sources.assert_calendar_writable(user_id, cal_id)
    _service(user_id).events().delete(calendarId=cal_id, eventId=event_id).execute()


def get_event(
    user_id: int,
    event_id: str,
    *,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cal_id = cal_sources.resolve_calendar_id(
        user_id, (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    )
    ev = _service(user_id).events().get(
        calendarId=cal_id, eventId=event_id
    ).execute()
    if not isinstance(ev, dict):
        raise RuntimeError("Неожиданный ответ Google Calendar.")
    return ev


def patch_event_fields(
    user_id: int,
    event_id: str,
    body: dict[str, Any],
    *,
    calendar_id: str | None = None,
) -> dict[str, Any]:
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    cal_sources.assert_calendar_writable(user_id, cal_id)
    patched = _service(user_id).events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body=body,
        sendUpdates="none",
    ).execute()
    if not isinstance(patched, dict):
        raise RuntimeError("Неожиданный ответ Google Calendar.")
    return patched


def format_create_reply_html(result: dict[str, Any], user_id: int) -> str:
    from assistant.lib.telegram_rich import append_footnotes, zoom_meeting_footnote

    title = html.escape(str(result.get("summary") or DEFAULT_EVENT_TITLE))
    link = str(result.get("html_link") or "").strip()
    when = ""
    st = result.get("start")
    en = result.get("end")
    if isinstance(st, datetime) and isinstance(en, datetime):
        when = format_event_when(st, en, user_id)
    if link:
        head = f"<h2>Создана встреча: <a href=\"{html.escape(link, quote=True)}\">{title}</a></h2>"
    else:
        head = f"<h2>Создана встреча: {title}</h2>"
    parts = [head]
    if when:
        parts.append(f"<p>{html.escape(when)}</p>")
    guests = result.get("attendee_emails") or []
    if guests:
        parts.append(f"<p>Участники: {html.escape(', '.join(guests))}</p>")
    zoom_url = str(result.get("zoom_join_url") or "").strip()
    footers: list[str] = []
    if zoom_url:
        ref, foot = zoom_meeting_footnote(zoom_url)
        if ref:
            parts.append(f"<p>{ref}</p>")
        if foot:
            footers.append(foot)
    return append_footnotes("\n".join(parts), footers)


def format_events_list(events: list[dict[str, Any]], header: str) -> str:
    from assistant.lib.telegram_rich import format_events_day_markdown

    return format_events_day_markdown(events, header)


def build_created_event_keyboard(
    result: dict[str, Any], *, user_id: int
) -> InlineKeyboardMarkup | None:
    link = str(result.get("html_link") or "").strip()
    ev_id = str(result.get("event_id") or "").strip()
    if not link and not ev_id:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    if link:
        rows.append([InlineKeyboardButton("Редактировать", url=link)])
    if ev_id:
        cal_id = str(result.get("calendar_id") or "").strip() or GOOGLE_CALENDAR_ID
        token = calendar_action_tokens.register(
            user_id, ev_id, calendar_id=cal_id
        )
        rows.append([InlineKeyboardButton("Удалить", callback_data=f"ced:{token}")])
    return InlineKeyboardMarkup(rows) if rows else None


def parsed_event_day(parsed: dict[str, Any]) -> str | None:
    for field in ("slot_day", "free_slots_date"):
        v = str(parsed.get(field) or "").strip()
        if v:
            return v[:10]
    start = str(parsed.get("start") or "").strip()
    if "T" in start:
        return start.split("T", 1)[0]
    if len(start) >= 10:
        return start[:10]
    return None


def user_text_has_explicit_time(user_text: str) -> bool:
    from assistant.lib.calendar_datetime_parse import calendar_extract_time
    from assistant.lib.slot_time import parse_time_from_user_text

    return (
        parse_time_from_user_text(user_text) is not None
        or calendar_extract_time(user_text) is not None
    )


def _combined_user_and_reply(user_text: str, reply_context: str = "") -> str:
    u = (user_text or "").strip()
    r = (reply_context or "").strip()
    if r and r not in u:
        return f"{u}\n\n{r}".strip() if u else r
    return u


def ensure_event_title_from_request(
    parsed: dict[str, Any],
    user_text: str = "",
    *,
    reply_context: str = "",
) -> None:
    """Подставляет название из исходного текста, если LLM оставил пустое/общее."""
    if not _title_is_generic(str(parsed.get("title") or "")):
        return
    combined = _combined_user_and_reply(user_text, reply_context)
    inferred = infer_event_title_from_text(combined)
    if inferred:
        parsed["title"] = inferred


def needs_time_selection(
    parsed: dict[str, Any],
    user_text: str,
    *,
    reply_context: str = "",
) -> bool:
    if not parsed_event_day(parsed):
        return False
    combined = _combined_user_and_reply(user_text, reply_context)
    rc = (reply_context or "").strip()
    from assistant.lib.calendar_datetime_parse import calendar_extract_time

    for blob in (combined, rc, (user_text or "").strip()):
        if not blob:
            continue
        if user_text_has_explicit_time(blob) or calendar_extract_time(blob):
            return False
    return True


def _intervals_overlap(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> bool:
    return a_start < b_end and b_start < a_end


def iter_workday_slot_starts(
    user_id: int,
    day_iso: str,
    *,
    duration_min: int = 60,
    step_min: int = 30,
    max_steps: int = 32,
) -> list[datetime]:
    """Кандидаты времени в рабочем окне дня (не только «дыры» из free_slots)."""
    dur = timedelta(minutes=max(15, duration_min))
    step = timedelta(minutes=max(15, step_min))
    work_start, work_end = _work_window(user_id, day_iso)
    starts: list[datetime] = []
    t = max(work_start, earliest_bookable_start(user_id, day_iso, step_min=step_min))
    steps = 0
    while t + dur <= work_end and steps < max_steps:
        starts.append(t)
        t += step
        steps += 1
    return starts


def suggest_slot_starts(
    user_id: int,
    day_iso: str,
    *,
    duration_min: int = 60,
    max_count: int = 8,
    step_min: int = 30,
    calendar_ids: list[str] | None = None,
) -> list[datetime]:
    from assistant.lib.calendar_slots import filter_future_slot_starts

    dur_min = max(15, int(duration_min or 60))
    dur = timedelta(minutes=dur_min)
    step = timedelta(minutes=max(15, step_min))
    work_start, work_end = _work_window(user_id, day_iso)
    not_before = earliest_bookable_start(user_id, day_iso, step_min=step_min)
    starts: list[datetime] = []
    for ws, we in free_slots_day(user_id, day_iso, calendar_ids=calendar_ids):
        t = max(ws, not_before)
        while t + dur <= we and len(starts) < max_count:
            starts.append(t)
            t += step
    if not starts:
        for st in iter_workday_slot_starts(
            user_id,
            day_iso,
            duration_min=dur_min,
            step_min=step_min,
            max_steps=max_count * 4,
        ):
            end = st + dur
            if not owner_conflict_at(
                user_id, st, end, day_iso=day_iso, calendar_ids=calendar_ids
            ):
                starts.append(st)
            if len(starts) >= max_count:
                break
    return filter_future_slot_starts(
        starts,
        not_before=not_before,
        work_end=work_end,
        duration=dur,
    )[:max_count]


def _suggest_person_free_slots(
    user_id: int,
    day_iso: str,
    *,
    duration_min: int = 60,
    max_count: int = 6,
) -> tuple[list[datetime], int, str]:
    """Слоты участника: активные календари, затем короче по длительности, затем личный."""
    target = max(15, int(duration_min or 60))
    durations: list[int] = [target]
    if target > 30:
        durations.append(30)

    active_ids = cal_sources.get_active_calendar_ids(user_id)
    for dur in durations:
        slots = suggest_slot_starts(
            user_id,
            day_iso,
            duration_min=dur,
            max_count=max_count,
            calendar_ids=active_ids,
        )
        if slots:
            return slots, dur, "active"

    primary_id = _primary_calendar_id(user_id)
    if primary_id and primary_id not in active_ids:
        for dur in durations:
            slots = suggest_slot_starts(
                user_id,
                day_iso,
                duration_min=dur,
                max_count=max_count,
                calendar_ids=[primary_id],
            )
            if slots:
                return slots, dur, "primary"

    return [], target, "active"


def suggest_person_free_slot_starts(
    user_id: int,
    day_iso: str,
    *,
    duration_min: int = 60,
    max_count: int = 6,
) -> list[datetime]:
    starts, _, _ = _suggest_person_free_slots(
        user_id, day_iso, duration_min=duration_min, max_count=max_count
    )
    return starts


def owner_conflict_at(
    user_id: int,
    start: datetime,
    end: datetime,
    *,
    day_iso: str,
    calendar_ids: list[str] | None = None,
) -> str | None:
    tz = _tz_for(user_id)
    work_start, work_end = _work_window(user_id, day_iso)
    try:
        _service(user_id)
    except Exception:
        return None
    for ev in _raw_events_for_day(user_id, day_iso, calendar_ids=calendar_ids):
        if calendar_event_is_cancelled(ev):
            continue
        from assistant.lib.calendar_event_utils import calendar_event_busy_window_local

        win = calendar_event_busy_window_local(
            ev, tz=tz, work_start=work_start, work_end=work_end
        )
        if win and _intervals_overlap(start, end, win[0], win[1]):
            return str(ev.get("summary") or "Встреча")
    return None


def _freebusy_has_overlap(
    user_id: int, start: datetime, end: datetime
) -> bool:
    try:
        svc = _service(user_id)
    except Exception:
        return False
    cal_ids = cal_sources.get_active_calendar_ids(user_id)
    body = {
        "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [{"id": cid} for cid in cal_ids],
    }
    res = svc.freebusy().query(body=body).execute()
    calendars = res.get("calendars") or {}
    for cal_id in cal_ids:
        cal = calendars.get(cal_id) or {}
        for block in cal.get("busy") or []:
            if not isinstance(block, dict):
                continue
            bs = block.get("start")
            be = block.get("end")
            if not bs or not be:
                continue
            try:
                b_start = datetime.fromisoformat(str(bs).replace("Z", "+00:00"))
                b_end = datetime.fromisoformat(str(be).replace("Z", "+00:00"))
                if _intervals_overlap(
                    start.astimezone(timezone.utc),
                    end.astimezone(timezone.utc),
                    b_start,
                    b_end,
                ):
                    return True
            except ValueError:
                continue
    return False


def busy_attendees_with_calendar(
    owner_id: int,
    parsed: dict[str, Any],
    start: datetime,
    end: datetime,
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[str]:
    from assistant.lib.calendar_attendees import iter_attendees_for_calendar_busy_check

    busy: list[str] = []
    for uid, label in iter_attendees_for_calendar_busy_check(
        owner_id,
        parsed,
        telegram_username=telegram_username,
        skip_names=skip_names,
    ):
        if _freebusy_has_overlap(uid, start, end):
            busy.append(label)
    return busy


def suggest_joint_slot_starts(
    owner_id: int,
    parsed: dict[str, Any],
    day_iso: str,
    *,
    duration_min: int = 60,
    max_count: int = 6,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> list[datetime]:
    """Время в рабочем дне, когда свободны организатор и участники с календарём в боте."""
    from assistant.lib.calendar_attendees import iter_attendees_for_calendar_busy_check

    dur = int(duration_min or parsed.get("duration_min") or 60)
    attendees = iter_attendees_for_calendar_busy_check(
        owner_id,
        parsed,
        telegram_username=telegram_username,
        skip_names=skip_names,
    )
    out: list[datetime] = []
    for st in iter_workday_slot_starts(
        owner_id, day_iso, duration_min=dur, max_steps=max(32, max_count * 5)
    ):
        end = st + timedelta(minutes=max(15, dur))
        if owner_conflict_at(owner_id, st, end, day_iso=day_iso):
            continue
        if any(_freebusy_has_overlap(uid, st, end) for uid, _ in attendees):
            continue
        out.append(st)
        if len(out) >= max_count:
            break
    return out


def apply_start_to_parsed(
    parsed: dict[str, Any], start: datetime, *, duration_min: int | None = None
) -> None:
    dur = int(duration_min or parsed.get("duration_min") or 60)
    end = start + timedelta(minutes=max(15, dur))
    parsed["start"] = start.replace(tzinfo=None).isoformat(timespec="seconds")
    parsed["end"] = end.replace(tzinfo=None).isoformat(timespec="seconds")
    parsed["duration_min"] = dur


def validate_meeting_slot(
    user_id: int,
    parsed: dict[str, Any],
    start: datetime,
    *,
    telegram_username: str | None = None,
    skip_names: set[str] | None = None,
) -> dict[str, Any]:
    day_iso = parsed_event_day(parsed) or start.date().isoformat()
    dur = int(parsed.get("duration_min") or 60)
    end = start + timedelta(minutes=max(15, dur))
    from assistant.lib.calendar_attendees import (
        attendee_names_missing_calendar_link,
        iter_attendees_for_calendar_busy_check,
    )

    conflict = owner_conflict_at(user_id, start, end, day_iso=day_iso)
    checked = iter_attendees_for_calendar_busy_check(
        user_id,
        parsed,
        telegram_username=telegram_username,
        skip_names=skip_names,
    )
    att_busy: list[str] = []
    for uid, label in checked:
        if _freebusy_has_overlap(uid, start, end):
            att_busy.append(label)
    not_checked = attendee_names_missing_calendar_link(
        user_id,
        parsed,
        checked,
        telegram_username=telegram_username,
        skip_names=skip_names,
    )
    if att_busy:
        from assistant.lib.calendar_attendees import names_match

        not_checked = [
            n
            for n in not_checked
            if not any(names_match(n, busy) for busy in att_busy)
        ]
    alts: list[datetime] = []
    attendee_alts: list[datetime] = []
    attendee_alt_label = ""
    attendee_alt_duration_min = 0
    attendee_alt_source = ""
    if conflict or att_busy:
        alts = suggest_joint_slot_starts(
            user_id,
            parsed,
            day_iso,
            duration_min=dur,
            max_count=6,
            telegram_username=telegram_username,
            skip_names=skip_names,
        )
        if not alts and conflict and not att_busy:
            alts = suggest_slot_starts(
                user_id, day_iso, duration_min=dur, max_count=6
            )
        if not alts and checked:
            from assistant.lib.calendar_attendees import names_match

            ordered = sorted(
                checked,
                key=lambda item: (
                    0
                    if any(names_match(item[1], busy) for busy in att_busy)
                    else 1
                ),
            )
            for uid, label in ordered:
                person_alts, person_dur, person_src = _suggest_person_free_slots(
                    uid, day_iso, duration_min=dur, max_count=6
                )
                if person_alts:
                    attendee_alts = person_alts
                    attendee_alt_label = str(label or "участника")
                    attendee_alt_duration_min = int(person_dur)
                    attendee_alt_source = str(person_src or "active")
                    break
    return {
        "ok": not conflict and not att_busy,
        "owner_conflict": conflict,
        "attendee_busy": att_busy,
        "attendees_not_checked": not_checked,
        "alt_starts": alts,
        "attendee_alt_starts": attendee_alts,
        "attendee_alt_label": attendee_alt_label,
        "attendee_alt_duration_min": attendee_alt_duration_min,
        "attendee_alt_source": attendee_alt_source,
        "start": start,
        "end": end,
    }
