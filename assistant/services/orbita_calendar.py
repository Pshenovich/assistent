"""Встречи Орбиты: создание, правка, отмена и ответы — для пользователей без Google Calendar.

Результат create/update — в той же форме, что у ``calendar.create_event`` / ``update_event``
(event_id, calendar_id, summary, start, end, attendee_emails…), поэтому приглашения в Telegram
(``meeting_invites.notify_invitees``) работают с ним как есть.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from assistant.stores import orbita_events as store


def uses_orbita(user_id: int) -> bool:
    """Встречи пользователя живут в Орбите, если Google Calendar не подключён."""
    try:
        from assistant.integrations.google_calendar_oauth import user_token_path

        return not user_token_path(int(user_id)).is_file()
    except Exception:
        return True


def organizer_email(user_id: int) -> str:
    """С какой почты организатор зовёт гостей: подключённая для отправки, иначе любая подтверждённая."""
    try:
        from assistant.stores import mail_accounts

        accs = mail_accounts.list_accounts(int(user_id))
        sending = next((a for a in accs if a.get("send_enabled")), None)
        if sending:
            return str(sending["email"]).lower()
    except Exception:
        pass
    try:
        from assistant.stores import orgs_store

        emails = orgs_store.verified_emails(int(user_id))
        return emails[0] if emails else ""
    except Exception:
        return ""


def _resolve_attendees(owner_id: int, emails: list[str], *, owner_email: str = "") -> list[dict[str, Any]]:
    from assistant.services import meeting_invites as inv

    out = []
    seen: set[str] = set()
    for raw in emails or []:
        em = str(raw or "").strip().lower()
        if not em or "@" not in em or em in seen or em == owner_email:
            continue
        seen.add(em)
        uid: Optional[int] = None
        try:
            uid = inv.telegram_id_for_attendee_email(int(owner_id), em)
        except Exception:
            uid = None
        if uid and int(uid) == int(owner_id):
            continue
        out.append({"email": em, "user_id": int(uid) if uid else None})
    return out


def _result(ev: dict[str, Any], *, previous: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out = {
        "event_id": store.public_id(ev["id"]),
        "calendar_id": store.CALENDAR_ID,
        "html_link": "",
        "summary": ev["title"],
        "start": datetime.fromisoformat(ev["start_utc"]),
        "end": datetime.fromisoformat(ev["end_utc"]),
        "attendee_emails": [a["email"] for a in ev["attendees"]],
        "recurrence": [],
        "orbita": True,
    }
    if previous is not None:
        out["previous_attendee_emails"] = [a["email"] for a in previous["attendees"]]
        out["previous_all_emails"] = out["previous_attendee_emails"] + (
            [previous["organizer_email"]] if previous.get("organizer_email") else [])
    return out


def _times(user_id: int, parsed: dict[str, Any], *, prev_start: datetime | None = None,
           prev_end: datetime | None = None) -> tuple[datetime, datetime]:
    """Начало и конец из формы. При правке: сдвинули только начало — длительность та же."""
    from assistant.services import calendar as cal

    start_s = str(parsed.get("start") or "").strip()
    end_s = str(parsed.get("end") or "").strip()
    start = cal._parse_dt(start_s, user_id) if start_s else prev_start
    if start is None:
        raise ValueError("Укажите начало встречи")
    if end_s:
        end = cal._parse_dt(end_s, user_id)
    elif prev_start is not None and prev_end is not None:
        end = start + (prev_end - prev_start)
    else:
        end = None
    return cal._ensure_event_range(start, end, parsed.get("duration_min") or 60)


def create(user_id: int, parsed: dict[str, Any]) -> dict[str, Any]:
    from assistant.services import calendar as cal

    if parsed.get("recurrence"):
        raise ValueError("Повторяющиеся встречи пока можно ставить только с подключённым Google Calendar")
    start, end = _times(user_id, parsed)
    owner_email = organizer_email(user_id)
    ev = store.create(
        int(user_id),
        title=cal.normalize_event_title(parsed),
        start=start,
        end=end,
        tz=cal._tz_name_for(int(user_id)),
        description=str(parsed.get("description") or ""),
        location=str(parsed.get("location") or ""),
        organizer_email=owner_email,
        attendees=_resolve_attendees(user_id, list(parsed.get("attendees") or []), owner_email=owner_email),
    )
    return _result(ev)


def _own(user_id: int, event_id: str) -> dict[str, Any]:
    ev = store.get(store.parse_id(event_id))
    if not ev or ev["status"] == "cancelled":
        raise ValueError("Встреча не найдена")
    if ev["owner_user_id"] != str(int(user_id)):
        raise ValueError("Менять встречу может только организатор")
    return ev


def update(user_id: int, event_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    if parsed.get("recurrence"):
        raise ValueError("Повторяющиеся встречи пока можно ставить только с подключённым Google Calendar")
    prev = _own(user_id, event_id)
    start = end = None
    if parsed.get("start") or parsed.get("end"):
        start, end = _times(user_id, parsed, prev_start=datetime.fromisoformat(prev["start_utc"]),
                            prev_end=datetime.fromisoformat(prev["end_utc"]))
    attendees = None
    if "attendees" in parsed:
        attendees = _resolve_attendees(user_id, list(parsed.get("attendees") or []),
                                       owner_email=prev.get("organizer_email") or "")
    ev = store.update(
        prev["id"],
        title=str(parsed["title"]) if parsed.get("title") else None,
        start=start,
        end=end,
        description=parsed.get("description"),
        location=parsed.get("location"),
        attendees=attendees,
    )
    return _result(ev, previous=prev)


def cancel(user_id: int, event_id: str) -> dict[str, Any]:
    ev = _own(user_id, event_id)
    return store.cancel(ev["id"])


def respond(user_id: int, event_id: str, response: str) -> dict[str, Any]:
    """Ответ приглашённого пользователя Орбиты («Приду / Возможно / Не приду»)."""
    ev = store.get(store.parse_id(event_id))
    if not ev or ev["status"] == "cancelled" or not store.can_view(ev, user_id):
        raise ValueError("Встреча не найдена")
    if not store.set_response(ev["id"], user_id=user_id, response=response):
        raise ValueError("Вы не приглашены на эту встречу")
    return store.get(ev["id"]) or ev


def events_for(user_id: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Встречи Орбиты пользователя в окне — в форме событий Google."""
    return [store.as_gcal(ev, user_id) for ev in store.list_for_user(user_id, start, end)]
