"""Приглашения на встречу в Telegram + RSVP (Приду / Возможно / Не приду)."""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Any

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, User
from telegram.constants import ParseMode

from assistant.bot.access_gate import is_user_allowed
from assistant.config import GOOGLE_CALENDAR_ID
from assistant.lib import calendar_pending_store as cps
from assistant.lib.calendar_attendees import attendee_calendar_user_id
from assistant.services.calendar import format_event_when
from assistant.stores import contacts_store as contacts

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
                InlineKeyboardButton("Приду", callback_data=f"inv:y:{t}"),
                InlineKeyboardButton("Возможно", callback_data=f"inv:m:{t}"),
                InlineKeyboardButton("Не приду", callback_data=f"inv:n:{t}"),
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


def patch_attendee_rsvp(
    organizer_user_id: int,
    *,
    event_id: str,
    calendar_id: str,
    invitee_email: str,
    response_status: str,
) -> None:
    from assistant.services.calendar import _service

    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    em = (invitee_email or "").strip().lower()
    status = response_status if response_status in _RSVP_LABELS else RSVP_TENTATIVE
    svc = _service(int(organizer_user_id))
    event = svc.events().get(calendarId=cal_id, eventId=event_id).execute()
    attendees = list(event.get("attendees") or [])
    found = False
    for row in attendees:
        if not isinstance(row, dict):
            continue
        if str(row.get("email") or "").strip().lower() == em:
            row["responseStatus"] = status
            found = True
            break
    if not found:
        attendees.append({"email": em, "responseStatus": status})
    svc.events().patch(
        calendarId=cal_id,
        eventId=event_id,
        body={"attendees": attendees},
        sendUpdates="all",
    ).execute()


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
        patch_attendee_rsvp(
            organizer_uid,
            event_id=ev_id,
            calendar_id=cal_id,
            invitee_email=invitee_email,
            response_status=status,
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
