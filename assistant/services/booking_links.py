"""Создание публичных ссылок на слоты и бронь гостем."""

from __future__ import annotations

import html
import os
from datetime import date, datetime, timedelta
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


def attach_source_message(token: str, chat_id: Any, message_id: Any) -> bool:
    try:
        return store.attach_source_message(token, int(chat_id), int(message_id))
    except (TypeError, ValueError):
        return False


def _event_when(result: dict[str, Any], owner_id: int) -> str:
    st = result.get("start")
    en = result.get("end")
    if not isinstance(st, datetime) or not isinstance(en, datetime):
        return ""
    return cal_svc.format_event_when(st, en, int(owner_id))


def format_booking_event_html(
    result: dict[str, Any],
    owner_id: int,
    *,
    heading: str = "Создана встреча",
) -> str:
    title = html.escape(str(result.get("summary") or "Встреча"))
    link = str(result.get("html_link") or "").strip()
    title_html = f'<a href="{html.escape(link, quote=True)}">{title}</a>' if link else f"<b>{title}</b>"
    parts = [f"<b>{html.escape(heading)}:</b> {title_html}"]
    when = _event_when(result, owner_id)
    if when:
        parts.append(html.escape(when))
    guests = [
        str(e).strip()
        for e in (result.get("attendee_emails") or [])
        if str(e).strip()
    ]
    if guests:
        parts.append("Участники: " + html.escape(", ".join(guests)))
    return "\n".join(parts)


def format_booking_owner_notify_html(
    result: dict[str, Any],
    owner_id: int,
    *,
    guests: list[str] | None = None,
) -> str:
    title = html.escape(str(result.get("summary") or "Встреча"))
    link = str(result.get("html_link") or "").strip()
    title_html = f'<a href="{html.escape(link, quote=True)}">{title}</a>' if link else f"<b>{title}</b>"
    who = [str(e).strip() for e in (guests or []) if str(e).strip()]
    who_line = html.escape(", ".join(who)) if who else "внешний контакт"
    parts = [
        "Внешний контакт поставил встречу",
        title_html,
    ]
    when = _event_when(result, owner_id)
    if when:
        parts.append(html.escape(when))
    parts.append(f"Участники: {who_line}")
    return "\n".join(parts)


async def announce_public_booking(
    *,
    owner_id: int,
    created: dict[str, Any],
    guests: list[str] | None = None,
    source_chat_id: int | None = None,
    source_message_id: int | None = None,
    organizer_user: dict[str, Any] | None = None,
) -> None:
    """Заменяет сообщение со ссылкой карточкой встречи и пишет владельцу."""
    from telegram import Bot
    from telegram.constants import ParseMode

    from assistant.lib import calendar_pending_store as cps
    from assistant.lib.telegram_html import sanitize_telegram_html

    uid = int(owner_id)
    payload = dict(created or {})
    guest_list = [str(e).strip() for e in (guests or payload.get("attendee_emails") or []) if str(e).strip()]
    if guest_list and not payload.get("attendee_emails"):
        payload["attendee_emails"] = guest_list
    card = sanitize_telegram_html(format_booking_event_html(payload, uid))
    notify = sanitize_telegram_html(
        format_booking_owner_notify_html(payload, uid, guests=guest_list)
    )
    kb = cal_svc.build_created_event_keyboard(payload, user_id=uid)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return
    edited = False
    try:
        async with Bot(token) as bot:
            if source_chat_id and source_message_id and card:
                try:
                    await bot.edit_message_text(
                        chat_id=int(source_chat_id),
                        message_id=int(source_message_id),
                        text=card,
                        parse_mode=ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_markup=kb,
                    )
                    edited = True
                    st = payload.get("start")
                    start_iso = st.isoformat() if hasattr(st, "isoformat") else str(st or "")
                    cps.put_event_message_ref(
                        int(source_chat_id),
                        int(source_message_id),
                        user_id=uid,
                        event_id=str(payload.get("event_id") or ""),
                        calendar_id=str(payload.get("calendar_id") or ""),
                        summary=str(payload.get("summary") or ""),
                        start_iso=start_iso,
                    )
                except Exception as e:
                    print(
                        f"[public_book] edit_link owner={uid} "
                        f"chat={source_chat_id} mid={source_message_id} err={e!r}"
                    )
            try:
                await bot.send_message(
                    chat_id=uid,
                    text=notify if edited else card or notify,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=None if edited else kb,
                )
            except Exception as e:
                print(f"[public_book] notify_owner owner={uid} err={e!r}")
    except Exception as e:
        print(f"[public_book] announce owner={uid} err={e!r}")
        return
    try:
        from assistant.services import meeting_invites as inv

        await inv.notify_invitees_for_miniapp(
            organizer_uid=uid,
            organizer_user=organizer_user or {},
            result=payload,
        )
    except Exception as e:
        print(f"[public_book] invite_notify err={e!r}")


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
    payload = dict(created or {})
    payload.setdefault("start", start_dt)
    payload.setdefault("end", end_dt)
    payload.setdefault("attendee_emails", emails)
    payload.setdefault("summary", title)
    return {
        "ok": True,
        "event_id": str(payload.get("event_id") or ""),
        "summary": str(payload.get("summary") or title),
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "html_link": str(payload.get("html_link") or ""),
        "_created": payload,
        "_owner_id": owner_id,
        "_guests": guests,
        "_source_chat_id": link.get("source_chat_id"),
        "_source_message_id": link.get("source_message_id"),
    }
