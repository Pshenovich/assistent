"""Создание публичных ссылок на слоты и бронь гостем."""

from __future__ import annotations

import html
from datetime import date, timedelta
from typing import Any

from assistant.integrations import google_calendar_oauth
from assistant.lib.webapp_public import public_booking_url
from assistant.services import calendar as cal_svc
from assistant.stores import booking_links as store

_REASON_HTTP = {
    "not_found": 404,
    "revoked": 404,
    "expired": 410,
    "used": 410,
    "slot_taken": 409,
}


class BookingLinkError(Exception):
    def __init__(self, reason: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.reason = reason
        self.status = int(status or _REASON_HTTP.get(reason, 400))


def _owner_has_calendar(owner_id: int) -> bool:
    try:
        return google_calendar_oauth.user_token_path(int(owner_id)).is_file()
    except Exception:
        return False


def create_booking_link(
    owner_id: int,
    *,
    title: str | None = None,
    attendees: list[Any] | None = None,
    duration_min: int | None = None,
    link_mode: str | None = None,
    expire_days: int | None = None,
) -> dict[str, Any]:
    link = store.create_link(
        int(owner_id),
        title=title,
        attendees=attendees,
        duration_min=duration_min,
        link_mode=link_mode,
        expire_days=expire_days,
    )
    return {
        "token": link["token"],
        "url": public_booking_url(str(link["token"])),
        "title": link.get("title") or "",
        "duration_min": link.get("duration_min"),
        "link_mode": link.get("link_mode"),
        "expires_at": link.get("expires_at"),
        "attendee_count": len(link.get("attendee_emails") or []),
    }


def _require_open_link(token: str) -> dict[str, Any]:
    link = store.resolve_link(token)
    status = store.link_status(link)
    if status == "not_found" or not link:
        raise BookingLinkError("not_found", "Ссылка недействительна")
    if status == "revoked":
        raise BookingLinkError("revoked", "Ссылка отозвана")
    if status == "expired":
        raise BookingLinkError("expired", "Срок действия ссылки истёк")
    if status == "used":
        raise BookingLinkError("used", "Ссылка уже использована")
    return link


def _effective_duration(link: dict[str, Any], requested: Any) -> int:
    fixed = store.normalize_duration_min(link.get("duration_min"))
    if fixed:
        return fixed
    return cal_svc.normalize_booking_duration(requested, default=30)


def public_booking_meta(
    token: str,
    *,
    duration_min: Any = None,
) -> dict[str, Any]:
    link = _require_open_link(token)
    owner_id = int(link["owner_user_id"])
    dur = _effective_duration(link, duration_min)
    tz_name = cal_svc._tz_name_for(owner_id)
    title = str(link.get("title") or "").strip()
    return {
        "ok": True,
        "status": "open",
        "title": title,
        "duration_min": link.get("duration_min"),
        "effective_duration_min": dur,
        "timezone": tz_name,
        "link_mode": link.get("link_mode"),
        "expires_at": link.get("expires_at"),
        "calendar_connected": _owner_has_calendar(owner_id),
        "days": cal_svc.booking_horizon_days(owner_id),
        "slots": [],
    }


_WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
_WEEKDAYS_FULL = [
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
]
_MONTHS_FULL = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]


def _date_chip_rel(day: date, today: date) -> str:
    delta = (day - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return _MONTHS_FULL[day.month - 1][:3]


def format_booking_day_heading(date_iso: str, *, today: date | None = None) -> str:
    try:
        day = date.fromisoformat(str(date_iso)[:10])
    except ValueError:
        return str(date_iso or "")
    core = f"{_WEEKDAYS_FULL[day.weekday()]}, {day.day} {_MONTHS_FULL[day.month - 1]}"
    if today is None:
        return core
    rel = _date_chip_rel(day, today)
    if rel in ("сегодня", "завтра"):
        return f"{rel} · {core}"
    return core


def first_bookable_day(days: list[dict[str, Any]] | None) -> str:
    for row in days or []:
        if not row.get("weekend"):
            return str(row.get("date") or "")
    return str((days or [{}])[0].get("date") or "") if days else ""


def public_booking_dates_html(days: list[dict[str, Any]] | None, *, selected: str = "") -> str:
    rows = list(days or [])
    if not rows:
        return "".join('<div class="date-chip sk"></div>' for _ in range(6))
    today = None
    try:
        today = date.fromisoformat(str(rows[0].get("date") or "")[:10])
    except ValueError:
        today = None
    chosen = selected or first_bookable_day(rows)
    parts: list[str] = []
    for row in rows:
        day_iso = str(row.get("date") or "")
        weekend = bool(row.get("weekend"))
        try:
            day = date.fromisoformat(day_iso[:10])
        except ValueError:
            continue
        classes = ["date-chip"]
        if day_iso == chosen:
            classes.append("is-active")
        if weekend:
            classes.append("is-weekend")
        rel = html.escape(_date_chip_rel(day, today or day))
        wd = html.escape(_WEEKDAYS_SHORT[day.weekday()])
        num = html.escape(str(day.day))
        selected_attr = "true" if day_iso == chosen else "false"
        parts.append(
            "<button type=\"button\" class=\""
            + " ".join(classes)
            + f"\" role=\"tab\" aria-selected=\"{selected_attr}\">"
            + f"<span class=\"date-chip-wd\">{wd}</span>"
            + f"<span class=\"date-chip-num\">{num}</span>"
            + f"<span class=\"date-chip-rel\">{rel}</span>"
            + "</button>"
        )
    return "".join(parts)


def public_booking_payload(
    token: str,
    *,
    duration_min: Any = None,
) -> dict[str, Any]:
    return public_booking_meta(token, duration_min=duration_min)


def public_booking_day_slots(
    token: str,
    day_iso: str,
    *,
    duration_min: Any = None,
) -> dict[str, Any]:
    link = _require_open_link(token)
    owner_id = int(link["owner_user_id"])
    dur = _effective_duration(link, duration_min)
    day = str(day_iso or "").strip()[:10]
    if not day:
        raise BookingLinkError("bad_date", "Укажите дату", status=400)
    slots = cal_svc.joint_free_slots_day(
        owner_id,
        day,
        list(link.get("attendee_emails") or []),
        duration_min=dur,
    )
    return {"ok": True, "date": day, "slots": slots}


def _guest_emails(raw: Any, extra: Any = None) -> list[str]:
    parts: list[Any] = []
    if isinstance(raw, list):
        parts.extend(raw)
    elif raw is not None and str(raw).strip():
        parts.extend(str(raw).replace(";", ",").split(","))
    if isinstance(extra, list):
        parts.extend(extra)
    elif extra is not None and str(extra).strip():
        parts.extend(str(extra).replace(";", ",").split(","))
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        em = str(item or "").strip().lower()
        if not em or em in seen:
            continue
        if "@" not in em or " " in em:
            raise BookingLinkError(
                "bad_email",
                "Укажите корректные email через запятую",
                status=400,
            )
        seen.add(em)
        out.append(em)
    if not out:
        raise BookingLinkError(
            "bad_email",
            "Укажите хотя бы один email",
            status=400,
        )
    return out


def book_public_slot(
    token: str,
    *,
    start: str,
    duration_min: Any = None,
    guest_email: Any = None,
    guest_emails: Any = None,
) -> dict[str, Any]:
    link = _require_open_link(token)
    owner_id = int(link["owner_user_id"])
    dur = _effective_duration(link, duration_min)
    start_raw = str(start or "").strip()
    if not start_raw:
        raise BookingLinkError("bad_start", "Укажите время встречи", status=400)
    try:
        start_dt = cal_svc._parse_dt(start_raw, owner_id)
    except Exception:
        raise BookingLinkError("bad_start", "Некорректное время встречи", status=400) from None
    end_dt = start_dt + timedelta(minutes=dur)
    team_emails = list(link.get("attendee_emails") or [])
    guests = _guest_emails(guest_email, extra=guest_emails)
    emails = list(team_emails)
    for guest in guests:
        if guest not in emails:
            emails.append(guest)
    title = str(link.get("title") or "").strip()
    marked = False
    if str(link.get("link_mode") or "") == "one_shot":
        if not store.try_mark_used(str(link["token"])):
            raise BookingLinkError("used", "Ссылка уже использована")
        marked = True
    try:
        if not cal_svc.joint_slot_is_free(owner_id, start_dt, end_dt, team_emails):
            raise BookingLinkError("slot_taken", "Этот слот уже занят")
        created = cal_svc.create_event(
            owner_id,
            {
                "title": title,
                "start": start_dt.replace(tzinfo=None).isoformat(timespec="seconds"),
                "end": end_dt.replace(tzinfo=None).isoformat(timespec="seconds"),
                "duration_min": dur,
                "attendees": emails,
            },
        )
    except BookingLinkError:
        if marked:
            store.clear_used(str(link["token"]))
        raise
    except Exception:
        if marked:
            store.clear_used(str(link["token"]))
        raise
    return {
        "ok": True,
        "event_id": str(created.get("event_id") or ""),
        "summary": str(created.get("summary") or title),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "html_link": str(created.get("html_link") or ""),
        "_created": created,
        "_owner_id": owner_id,
    }
