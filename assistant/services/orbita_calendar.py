"""Встречи Орбиты: создание, правка, отмена и ответы — для пользователей без Google Calendar.

Результат create/update — в той же форме, что у ``calendar.create_event`` / ``update_event``
(event_id, calendar_id, summary, start, end, attendee_emails…), поэтому приглашения в Telegram
(``meeting_invites.notify_invitees``) работают с ним как есть.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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


def _rule(parsed: dict[str, Any]) -> Optional[str]:
    """Правило повтора из формы/Лео: None — не трогали, "" — без повтора, "RRULE:…"."""
    if "recurrence" not in parsed or parsed.get("recurrence") is None:
        return None
    raw = parsed.get("recurrence")
    lines = [raw] if isinstance(raw, str) else list(raw or [])
    rules = [str(x).strip() for x in lines if str(x).strip().upper().startswith("RRULE:")]
    return rules[0] if rules else ""


def _result(ev: dict[str, Any], *, previous: Optional[dict[str, Any]] = None,
            occurrence: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out = {
        "event_id": store.instance_id(ev["id"], occurrence["occ"]) if occurrence and occurrence.get("occ")
        else store.public_id(ev["id"]),
        "calendar_id": store.CALENDAR_ID,
        "html_link": "",
        "summary": ev["title"],
        "start": occurrence["start"] if occurrence else datetime.fromisoformat(ev["start_utc"]),
        "end": occurrence["end"] if occurrence else datetime.fromisoformat(ev["end_utc"]),
        "attendee_emails": [a["email"] for a in ev["attendees"]],
        "recurrence": [ev["recurrence"]] if ev.get("recurrence") else [],
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
        recurrence=_rule(parsed) or "",
    )
    return _result(ev)


def _own(user_id: int, event_id: str) -> dict[str, Any]:
    ev = store.get(store.parse_id(event_id))
    if not ev or ev["status"] == "cancelled":
        raise ValueError("Встреча не найдена")
    if ev["owner_user_id"] != str(int(user_id)):
        raise ValueError("Менять встречу может только организатор")
    return ev


def find_occurrence(ev: dict[str, Any], occ: str) -> Optional[dict[str, Any]]:
    """Вхождение серии по ключу (с учётом сдвига)."""
    if occ in (ev.get("exdates") or []):
        return None
    ov = (ev.get("overrides") or {}).get(occ)
    if ov:
        return {"occ": occ, "start": datetime.fromisoformat(ov["start_utc"]), "end": datetime.fromisoformat(ov["end_utc"])}
    t = store.occ_start(occ)
    hit = [o for o in store.occurrences(ev, t - timedelta(minutes=1), t + timedelta(minutes=1)) if o["occ"] == occ]
    return hit[0] if hit else None


def update(user_id: int, event_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Правка встречи. У вхождения серии сдвиг времени — только этого вхождения; остальное — всей серии."""
    prev = _own(user_id, event_id)
    _, occ = store.parse_instance(event_id)
    rule = _rule(parsed)
    start = end = None
    occurrence = None
    if occ and rule is None:
        occurrence = find_occurrence(prev, occ)
        if occurrence is None:
            raise ValueError("Встреча не найдена")
        if parsed.get("start") or parsed.get("end"):
            s1, e1 = _times(user_id, parsed, prev_start=occurrence["start"], prev_end=occurrence["end"])
            if (s1, e1) != (occurrence["start"], occurrence["end"]):
                store.move_occurrence(prev["id"], occ, start=s1, end=e1)
                occurrence = {"occ": occ, "start": s1, "end": e1}
    elif parsed.get("start") or parsed.get("end"):
        base_s, base_e = datetime.fromisoformat(prev["start_utc"]), datetime.fromisoformat(prev["end_utc"])
        if occ:  # правило поменяли из вхождения: серия начинается с этого вхождения
            cur = find_occurrence(prev, occ)
            base_s, base_e = (cur["start"], cur["end"]) if cur else (base_s, base_e)
        start, end = _times(user_id, parsed, prev_start=base_s, prev_end=base_e)
        if (start, end) == (datetime.fromisoformat(prev["start_utc"]), datetime.fromisoformat(prev["end_utc"])):
            start = end = None
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
        recurrence=rule,
    )
    return _result(ev, previous=prev, occurrence=occurrence if rule is None else None)


def cancel(user_id: int, event_id: str) -> dict[str, Any]:
    """Удалить встречу; у вхождения серии — только это вхождение."""
    ev = _own(user_id, event_id)
    _, occ = store.parse_instance(event_id)
    if occ and ev.get("recurrence"):
        return store.skip_occurrence(ev["id"], occ)
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
    """Встречи Орбиты пользователя в окне — в форме событий Google (серии — по вхождениям)."""
    out = []
    for ev in store.list_for_user(user_id, start, end):
        for o in store.occurrences(ev, start, end):
            out.append(store.as_gcal(ev, user_id, o if o.get("occ") else None))
    return out


def view(user_id: int, event_id: str) -> Optional[dict[str, Any]]:
    """Одна встреча (или вхождение серии) глазами пользователя — в форме события Google."""
    n, occ = store.parse_instance(event_id)
    ev = store.get(n)
    if not ev or not store.can_view(ev, user_id):
        return None
    o = find_occurrence(ev, occ) if occ else None
    if occ and o is None:
        return None
    return store.as_gcal(ev, user_id, o)


def attach_video(user_id: int, event_id: str, provider: str) -> str:
    """Zoom или Телемост для встречи Орбиты: создать комнату и сохранить ссылку во встрече.

    Как и для Google: Zoom — запланированная встреча (с записью ботом, если включена), Телемост — комната.
    """
    from assistant.lib import telemost_gcal, zoom_gcal
    from assistant.lib.user_timezone import resolve_user_tz_name

    ev = _own(user_id, event_id)
    if ev.get("video_url") and ev.get("video_provider") == provider:
        return ev["video_url"]
    start = datetime.fromisoformat(ev["start_utc"])
    end = datetime.fromisoformat(ev["end_utc"])
    if provider == "zoom":
        token = zoom_gcal._zoom_access_token(int(user_id))
        auto_record = zoom_gcal.user_prefs.zoom_auto_record_enabled(int(user_id)) and \
            zoom_gcal.meeting_sched.service_available()
        zm = zoom_gcal.zoom_api.create_scheduled_meeting(
            token, topic=ev["title"], start_utc=start, end_utc=end,
            timezone_str=resolve_user_tz_name(int(user_id)), auto_record=auto_record,
        )
        join, ref = str(zm.get("join_url") or "").strip(), str(zm.get("id") or "")
        if not join:
            raise RuntimeError("Zoom не вернул ссылку на встречу.")
        zoom_gcal.zrm.register_meeting(int(user_id), zm, source="orbita_zoom")
        zoom_gcal.meeting_sched.maybe_schedule_for_zoom_create(
            int(user_id), join_url=join, topic=ev["title"], start_at=start, source="orbita_zoom")
    elif provider == "telemost":
        token = telemost_gcal._telemost_access_token(int(user_id))
        conf = telemost_gcal.telemost_api.create_conference(
            token, auto_summarization=telemost_gcal.user_prefs.telemost_auto_record_enabled(int(user_id)))
        join, ref = str(conf.get("join_url") or "").strip(), str(conf.get("id") or "")
        if not join:
            raise RuntimeError("Телемост не вернул ссылку на встречу.")
    else:
        raise ValueError("Неизвестная видеосвязь")
    store.set_video(ev["id"], url=join, provider=provider, ref=ref)
    return join
