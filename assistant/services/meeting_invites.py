"""Приглашения на встречу в Telegram + RSVP (Приду / Возможно / Не приду)."""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.constants import ParseMode

from assistant.bot.access_gate import is_user_allowed
from assistant.config import GOOGLE_CALENDAR_ID
from assistant.lib import calendar_pending_store as cps
from assistant.lib.calendar_attendees import attendee_calendar_user_id
from assistant.lib.calendar_event_utils import (
    event_is_invitation,
    event_needs_rsvp,
    event_self_attendee,
)
from assistant.services.calendar import format_event_when
from assistant.stores import contacts_store as contacts
from assistant.stores import meeting_invites_notified as notified

RSVP_ACCEPTED = "accepted"
RSVP_TENTATIVE = "tentative"
RSVP_DECLINED = "declined"

_RSVP_LABELS = {
    RSVP_ACCEPTED: "Приду",
    RSVP_TENTATIVE: "Возможно",
    RSVP_DECLINED: "Не приду",
}


def invites_enabled() -> bool:
    raw = os.getenv("MEETING_INVITES_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def organizer_mention_html(user: User | None) -> str:
    if not user:
        return "Организатор"
    un = str(user.username or "").strip()
    if un:
        return f"@{html.escape(un)}"
    name = " ".join(
        p
        for p in (str(user.first_name or "").strip(), str(user.last_name or "").strip())
        if p
    ).strip()
    return html.escape(name or "Организатор")


def telegram_id_for_attendee_email(
    owner_id: int,
    email: str,
    *,
    telegram_username: str | None = None,
) -> int | None:
    from assistant.lib.calendar_user_lookup import lookup_user_id_by_calendar_email

    em = (email or "").strip().lower()
    if not em or "@" not in em:
        return None
    uid = lookup_user_id_by_calendar_email(em)
    if uid:
        return int(uid)
    for c in contacts.load_contacts(
        telegram_user_id=owner_id, telegram_username=telegram_username
    ):
        if str(c.get("email") or "").strip().lower() != em:
            continue
        cid = attendee_calendar_user_id(c)
        if cid:
            return int(cid)
    return None


def invitee_targets(
    organizer_uid: int,
    attendee_emails: list[str],
    *,
    organizer_username: str | None = None,
) -> list[tuple[int, str]]:
    """(telegram_user_id, email) приглашённых с аккаунтом в боте."""
    out: list[tuple[int, str]] = []
    seen_uid: set[int] = set()
    for raw in attendee_emails or []:
        em = str(raw).strip().lower()
        if not em or "@" not in em:
            continue
        uid = telegram_id_for_attendee_email(
            organizer_uid, em, telegram_username=organizer_username
        )
        if not uid or int(uid) == int(organizer_uid) or uid in seen_uid:
            continue
        if not is_user_allowed(uid, None):
            continue
        seen_uid.add(uid)
        out.append((uid, em))
    return out


def format_invite_message_html(
    result: dict[str, Any],
    *,
    organizer: User | None,
    organizer_uid: int,
) -> str:
    who = organizer_mention_html(organizer)
    title = html.escape(str(result.get("summary") or "Встреча"))
    link = str(result.get("html_link") or "").strip()
    if link:
        title_block = f'<a href="{html.escape(link)}">{title}</a>'
    else:
        title_block = f"<b>{title}</b>"
    lines = [
        f"{who} приглашает вас на встречу {title_block}",
    ]
    st = result.get("start")
    en = result.get("end")
    if isinstance(st, datetime) and isinstance(en, datetime):
        when = format_event_when(st, en, organizer_uid)
        if when:
            lines.append(html.escape(when))
    zoom_url = str(result.get("zoom_join_url") or "").strip()
    if zoom_url:
        lines.append(
            "Zoom: "
            + f'<a href="{html.escape(zoom_url)}">{html.escape(zoom_url)}</a>'
        )
    lines.append("")
    lines.append("Вы сможете принять участие?")
    return "\n".join(lines)


def build_invite_keyboard(token: str) -> InlineKeyboardMarkup:
    t = (token or "").strip()
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Не приду", callback_data=f"inv:n:{t}"),
                InlineKeyboardButton("Возможно", callback_data=f"inv:m:{t}"),
                InlineKeyboardButton("Приду", callback_data=f"inv:y:{t}"),
            ]
        ]
    )


def register_invite_token(
    *,
    organizer_user_id: int,
    invitee_user_id: int,
    invitee_email: str,
    event_id: str,
    calendar_id: str,
) -> str:
    return cps.put_invite_rsvp_token(
        organizer_user_id=organizer_user_id,
        invitee_user_id=invitee_user_id,
        invitee_email=invitee_email,
        event_id=event_id,
        calendar_id=calendar_id,
    )


def _apply_attendee_status(
    event: dict[str, Any],
    *,
    invitee_email: str,
    response_status: str,
    match_self: bool = False,
) -> list[dict[str, Any]]:
    em = (invitee_email or "").strip().lower()
    status = response_status if response_status in _RSVP_LABELS else RSVP_TENTATIVE
    attendees = [dict(row) if isinstance(row, dict) else row for row in (event.get("attendees") or [])]
    found = False
    for row in attendees:
        if not isinstance(row, dict):
            continue
        if match_self and row.get("self"):
            row["responseStatus"] = status
            found = True
            continue
        if em and str(row.get("email") or "").strip().lower() == em:
            row["responseStatus"] = status
            found = True
    if not found:
        row: dict[str, Any] = {"responseStatus": status}
        if em:
            row["email"] = em
        attendees.append(row)
    return attendees


def patch_attendee_rsvp(
    organizer_user_id: int,
    *,
    event_id: str,
    calendar_id: str,
    invitee_email: str,
    response_status: str,
) -> dict[str, Any]:
    from assistant.services.calendar import _service

    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    svc = _service(int(organizer_user_id))
    event = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
    attendees = _apply_attendee_status(
        event, invitee_email=invitee_email, response_status=response_status
    )
    return svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body={"attendees": attendees},
        sendUpdates="all",
    ).execute()


def patch_self_rsvp(
    invitee_user_id: int,
    *,
    event_id: str,
    calendar_id: str,
    response_status: str,
    invitee_email: str = "",
) -> dict[str, Any]:
    """RSVP в копии события на календаре приглашённого."""
    from assistant.services import calendar_sources as cal_sources
    from assistant.services.calendar import _service

    cal_id = cal_sources.resolve_calendar_id(
        int(invitee_user_id), (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    )
    status = response_status if response_status in _RSVP_LABELS else RSVP_TENTATIVE
    svc = _service(int(invitee_user_id))
    event = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
    self_row = event_self_attendee(event)
    em = (invitee_email or "").strip().lower()
    if not em and self_row:
        em = str(self_row.get("email") or "").strip().lower()
    attendees = _apply_attendee_status(
        event,
        invitee_email=em,
        response_status=status,
        match_self=True,
    )
    patched = svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body={"attendees": attendees},
        sendUpdates="all",
    ).execute()
    patched["_calendarId"] = cal_id
    return patched


def apply_invitee_rsvp(
    invitee_user_id: int,
    *,
    event_id: str,
    calendar_id: str,
    response_status: str,
    invitee_email: str = "",
    organizer_user_id: int = 0,
) -> dict[str, Any]:
    last_err: Exception | None = None
    try:
        return patch_self_rsvp(
            invitee_user_id,
            event_id=event_id,
            calendar_id=calendar_id,
            response_status=response_status,
            invitee_email=invitee_email,
        )
    except Exception as e:
        last_err = e
        print(f"[meeting_invite] self_rsvp uid={invitee_user_id} ev={event_id} err={e!r}")
    if organizer_user_id:
        try:
            return patch_attendee_rsvp(
                int(organizer_user_id),
                event_id=event_id,
                calendar_id=calendar_id,
                invitee_email=invitee_email,
                response_status=response_status,
            )
        except Exception as e:
            last_err = e
            print(
                f"[meeting_invite] org_rsvp organizer={organizer_user_id} "
                f"ev={event_id} err={e!r}"
            )
    if last_err:
        raise last_err
    raise RuntimeError("Не удалось сохранить ответ на приглашение")


def format_invite_after_answer_html(
    base_text: str, response_status: str
) -> str:
    label = _RSVP_LABELS.get(response_status, response_status)
    clean = (base_text or "").split("\n\nВы сможете")[0].strip()
    return f"{clean}\n\n<b>Ваш ответ:</b> {html.escape(label)}"


async def notify_invitees(
    bot: Bot,
    *,
    organizer: User | None,
    organizer_uid: int,
    result: dict[str, Any],
    organizer_username: str | None = None,
) -> int:
    """Отправляет приглашения; возвращает число доставленных сообщений."""
    if not invites_enabled():
        return 0
    ev_id = str(result.get("event_id") or "").strip()
    cal_id = str(result.get("calendar_id") or "").strip()
    if not ev_id:
        return 0
    emails = list(result.get("attendee_emails") or [])
    targets = invitee_targets(
        organizer_uid, emails, organizer_username=organizer_username
    )
    if not targets:
        return 0
    text = format_invite_message_html(
        result, organizer=organizer, organizer_uid=organizer_uid
    )
    sent = 0
    for invitee_uid, invitee_email in targets:
        if notified.was_notified(int(invitee_uid), ev_id):
            continue
        try:
            token = register_invite_token(
                organizer_user_id=organizer_uid,
                invitee_user_id=invitee_uid,
                invitee_email=invitee_email,
                event_id=ev_id,
                calendar_id=cal_id,
            )
            await bot.send_message(
                chat_id=int(invitee_uid),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=build_invite_keyboard(token),
            )
            notified.mark_notified(int(invitee_uid), ev_id)
            sent += 1
            print(
                f"[meeting_invite] sent organizer={organizer_uid} "
                f"invitee={invitee_uid} ev={ev_id}"
            )
        except Exception as e:
            print(
                f"[meeting_invite] send organizer={organizer_uid} "
                f"invitee={invitee_uid} err={e!r}"
            )
    return sent


def parse_invite_callback(data: str) -> tuple[str, str] | None:
    """inv:y:TOKEN → (status, token)."""
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "inv":
        return None
    code, token = parts[1], parts[2].strip()
    status_map = {"y": RSVP_ACCEPTED, "m": RSVP_TENTATIVE, "n": RSVP_DECLINED}
    status = status_map.get(code)
    if not status or not token:
        return None
    return status, token


async def handle_invite_rsvp_callback(
    query,
    *,
    invitee_uid: int,
) -> bool:
    """Обработка inv:y|m|n:TOKEN. True если callback обработан."""
    from telegram import CallbackQuery

    q: CallbackQuery = query
    parsed_cb = parse_invite_callback(str(q.data or ""))
    if not parsed_cb:
        return False
    status, token = parsed_cb
    rec = cps.get_invite_rsvp_token(token, invitee_user_id=invitee_uid)
    if not rec:
        await q.answer("Приглашение устарело.", show_alert=True)
        return True
    organizer_uid = int(rec.get("organizer_user_id") or 0)
    ev_id = str(rec.get("event_id") or "")
    cal_id = str(rec.get("calendar_id") or "")
    invitee_email = str(rec.get("invitee_email") or "")
    try:
        await q.answer(_RSVP_LABELS.get(status, ""))
        apply_invitee_rsvp(
            invitee_uid,
            event_id=ev_id,
            calendar_id=cal_id,
            response_status=status,
            invitee_email=invitee_email,
            organizer_user_id=organizer_uid,
        )
        base = ""
        if q.message:
            base = str(
                getattr(q.message, "text_html", None)
                or q.message.text
                or q.message.caption
                or ""
            )
        new_text = format_invite_after_answer_html(base, status)
        await q.edit_message_text(
            new_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=None,
        )
    except Exception as e:
        await q.answer("Не удалось сохранить ответ.", show_alert=True)
        print(f"[meeting_invite] rsvp uid={invitee_uid} err={e!r}")
    return True


class _MiniappOrganizer:
    def __init__(self, user: dict[str, Any] | None) -> None:
        u = user or {}
        self.username = str(u.get("username") or "").strip() or None
        self.first_name = str(u.get("first_name") or "").strip() or "Организатор"
        self.last_name = str(u.get("last_name") or "").strip()


async def notify_invitees_for_miniapp(
    *,
    organizer_uid: int,
    organizer_user: dict[str, Any] | None,
    result: dict[str, Any],
    only_emails: list[str] | None = None,
) -> int:
    """RSVP в Telegram после создания/правки встречи из миниаппа."""
    if not invites_enabled():
        return 0
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return 0
    payload = dict(result or {})
    if only_emails is not None:
        allow = {str(e).strip().lower() for e in only_emails if str(e).strip()}
        payload["attendee_emails"] = [
            str(e).strip().lower()
            for e in (payload.get("attendee_emails") or [])
            if str(e).strip().lower() in allow
        ]
        if not payload["attendee_emails"]:
            return 0
    uname = str((organizer_user or {}).get("username") or "").strip() or None
    try:
        async with Bot(token) as bot:
            return await notify_invitees(
                bot,
                organizer=_MiniappOrganizer(organizer_user),
                organizer_uid=int(organizer_uid),
                result=payload,
                organizer_username=uname,
            )
    except Exception as e:
        print(f"[meeting_invite] miniapp notify organizer={organizer_uid} err={e!r}")
        return 0


def incoming_invite_horizon_days() -> int:
    try:
        return max(1, min(60, int(os.getenv("MEETING_INVITE_HORIZON_DAYS", "14") or "14")))
    except ValueError:
        return 14


def organizer_label_from_event(event: dict[str, Any] | None) -> str:
    src = event or {}
    org = src.get("organizer") if isinstance(src.get("organizer"), dict) else {}
    name = str(org.get("displayName") or "").strip()
    if name:
        return html.escape(name)
    email = str(org.get("email") or "").strip()
    if email:
        return html.escape(email)
    for row in src.get("attendees") or []:
        if not isinstance(row, dict) or not row.get("organizer"):
            continue
        name = str(row.get("displayName") or row.get("name") or "").strip()
        if name:
            return html.escape(name)
        email = str(row.get("email") or "").strip()
        if email:
            return html.escape(email)
    return "Организатор"


def format_incoming_invite_html(event: dict[str, Any], *, user_id: int) -> str:
    who = organizer_label_from_event(event)
    title = html.escape(str(event.get("summary") or "Встреча"))
    link = str(event.get("html_link") or event.get("htmlLink") or "").strip()
    if link:
        title_block = f'<a href="{html.escape(link)}">{title}</a>'
    else:
        title_block = f"<b>{title}</b>"
    lines = [f"{who} приглашает вас на встречу {title_block}"]
    start = event.get("start")
    end = event.get("end")
    if isinstance(start, datetime) and isinstance(end, datetime):
        when = format_event_when(start, end, user_id)
        if when:
            lines.append(html.escape(when))
    lines.append("")
    lines.append("Вы сможете принять участие?")
    return "\n".join(lines)


def collect_pending_incoming_invites(user_id: int) -> list[dict[str, Any]]:
    """Входящие Google-приглашения без ответа, начиная с текущего момента."""
    from assistant.services import calendar_sources as cal_sources
    from assistant.services.calendar import _tz_for

    tz = _tz_for(int(user_id))
    now = datetime.now(tz)
    end = now + timedelta(days=incoming_invite_horizon_days())
    pending: list[dict[str, Any]] = []
    for norm in cal_sources.list_events_in_window(int(user_id), now, end):
        raw = norm.get("raw") if isinstance(norm.get("raw"), dict) else {}
        if not event_is_invitation(raw) or not event_needs_rsvp(raw):
            continue
        ev_id = str(norm.get("event_id") or raw.get("id") or "").strip()
        if not ev_id:
            continue
        self_row = event_self_attendee(raw)
        pending.append(
            {
                "event_id": ev_id,
                "calendar_id": str(norm.get("calendar_id") or "primary"),
                "summary": str(norm.get("summary") or raw.get("summary") or "Встреча"),
                "html_link": str(norm.get("html_link") or raw.get("htmlLink") or ""),
                "start": norm.get("start"),
                "end": norm.get("end"),
                "invitee_email": str((self_row or {}).get("email") or "").strip().lower(),
                "raw": raw,
            }
        )
    return pending


def prepare_incoming_invite(user_id: int, event: dict[str, Any]) -> tuple[str, str]:
    ev_id = str(event.get("event_id") or "").strip()
    cal_id = str(event.get("calendar_id") or "primary")
    email = str(event.get("invitee_email") or "").strip().lower()
    token = register_invite_token(
        organizer_user_id=0,
        invitee_user_id=int(user_id),
        invitee_email=email,
        event_id=ev_id,
        calendar_id=cal_id,
    )
    payload = {
        "summary": event.get("summary"),
        "html_link": event.get("html_link"),
        "start": event.get("start"),
        "end": event.get("end"),
        "organizer": (event.get("raw") or {}).get("organizer")
        if isinstance(event.get("raw"), dict)
        else {},
        "attendees": (event.get("raw") or {}).get("attendees")
        if isinstance(event.get("raw"), dict)
        else [],
    }
    return format_incoming_invite_html(payload, user_id=int(user_id)), token
