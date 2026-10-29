"""Письма-приглашения на встречи Орбиты внешним гостям (тем, кого нет в Орбите).

Письмо уходит с почты организатора, подключённой в Орбите с включённой отправкой
(«Отвечать из Orbita»). В письме — приглашение text/calendar (почтовики показывают кнопки
«Да / Нет»), файл invite.ics и ссылка на страницу ответа. Пользователям Орбиты пишет бот.
"""

from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime, formataddr, make_msgid
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.lib import ics

REASON_NO_MAIL = "no_mail"
# Поля, правка которых — повод разослать обновлённое приглашение.
_MATERIAL = ("title", "start_utc", "end_utc", "location", "description", "video_url")


def send_account(user_id: int) -> Optional[dict[str, Any]]:
    from assistant.stores import mail_accounts

    try:
        return next((a for a in mail_accounts.list_accounts(int(user_id)) if a.get("send_enabled")), None)
    except Exception:
        return None


def rsvp_url(token: str) -> str:
    from assistant.lib.webapp_public import public_base_url

    return f"{public_base_url()}/rsvp/{token}"


def external(ev: dict[str, Any]) -> list[dict[str, Any]]:
    """Гости без аккаунта в Орбите — им письмо."""
    return [a for a in ev.get("attendees") or [] if not a.get("user_id")]


def _organizer_name(user_id: int) -> str:
    from assistant.stores import note_members

    try:
        prof = note_members.get_profile(user_id)
        name = note_members.profile_display_name(prof, fallback="") if prof else ""
        return "" if name == "Участник" else name
    except Exception:
        return ""


def _when(ev: dict[str, Any], user_id: int) -> str:
    from assistant.services.calendar import format_event_when

    return format_event_when(datetime.fromisoformat(ev["start_utc"]), datetime.fromisoformat(ev["end_utc"]), user_id)


def _message(acc: dict[str, Any], ev: dict[str, Any], guest: dict[str, Any], *, method: str, owner: int) -> EmailMessage:
    name = _organizer_name(owner)
    when = _when(ev, owner)
    cancelled = method == "CANCEL"
    updated = int(ev.get("sequence") or 0) > 0 and not cancelled
    url = "" if cancelled else rsvp_url(guest["token"])
    head = "Отменена встреча" if cancelled else ("Изменена встреча" if updated else "Приглашение на встречу")
    msg = EmailMessage()
    msg["From"] = formataddr((name, acc["email"])) if name else acc["email"]
    msg["To"] = guest["email"]
    msg["Subject"] = f"{head}: {ev['title']} — {when}"
    msg["Date"] = format_datetime(datetime.now(timezone.utc))
    msg["Message-ID"] = make_msgid(domain=str(acc["email"]).rsplit("@", 1)[-1])
    lines = [f"{head} «{ev['title']}»", when]
    if ev.get("location"):
        lines.append(f"Где: {ev['location']}")
    if ev.get("video_url") and not cancelled:
        lines.append(f"Подключиться: {ev['video_url']}")
    if name:
        lines.append(f"Организатор: {name} <{acc['email']}>")
    if ev.get("description") and not cancelled:
        lines += ["", str(ev["description"]).strip()]
    if url:
        lines += ["", "Ответить, придёте ли: " + url]
    msg.set_content("\n".join(lines) + "\n")
    event_for_ics = {**ev, "organizer_email": acc["email"]}
    text = ics.build(event_for_ics, method=method, attendees=[guest], organizer_name=name, rsvp_url=url)
    msg.add_alternative(text, subtype="calendar", params={"method": method})
    msg.add_attachment(text.encode("utf-8"), maintype="application", subtype="ics", filename="invite.ics")
    return msg


def send(owner: int, ev: dict[str, Any], guests: list[dict[str, Any]], *, method: str = "REQUEST") -> dict[str, Any]:
    """Отправить приглашение (REQUEST) или отмену (CANCEL) гостям. {sent, failed, skipped, reason}."""
    from assistant.integrations import mail_smtp
    from assistant.services import mail_source

    emails = [g["email"] for g in guests]
    if not guests:
        return {"sent": [], "failed": [], "skipped": []}
    acc = send_account(owner)
    if not acc:
        return {"sent": [], "failed": [], "skipped": emails, "reason": REASON_NO_MAIL}
    sent, failed = [], []
    creds = mail_source.credentials(owner, acc)
    for g in guests:
        try:
            mail_smtp.send(acc, _message(acc, ev, g, method=method, owner=owner), **creds)
            sent.append(g["email"])
        except Exception as e:
            print(f"[orbita_invite_mail] owner={owner} to={g['email']} err={e!r}")
            failed.append(g["email"])
    return {"sent": sent, "failed": failed, "skipped": []}


def after_change(owner: int, prev: Optional[dict[str, Any]], ev: dict[str, Any]) -> dict[str, Any]:
    """Разослать внешним гостям то, что нужно после создания, правки или отмены встречи."""
    if ev.get("status") == "cancelled":
        return send(owner, ev, external(prev or ev), method="CANCEL")
    now_ext = {a["email"]: a for a in external(ev)}
    if prev is None:
        return send(owner, ev, list(now_ext.values()))
    before = {a["email"]: a for a in external(prev)}
    changed = any(str(prev.get(k) or "") != str(ev.get(k) or "") for k in _MATERIAL)
    targets = list(now_ext.values()) if changed else [a for e, a in now_ext.items() if e not in before]
    res = send(owner, ev, targets)
    removed = [a for e, a in before.items() if e not in now_ext]
    if removed:
        gone = send(owner, {**ev, "status": "cancelled"}, removed, method="CANCEL")
        res["cancelled"] = gone.get("sent", [])
    return res


def notify_members(owner: int, prev: Optional[dict[str, Any]], ev: dict[str, Any]) -> None:
    """Пользователям Орбиты — сообщение бота об отмене или переносе (о новой встрече зовёт meeting_invites)."""
    from assistant.bot import access_gate

    if prev is None:
        return
    members = [a for a in (prev if ev.get("status") == "cancelled" else ev).get("attendees") or [] if a.get("user_id")]
    if not members:
        return
    if ev.get("status") == "cancelled":
        text = f"Встреча «{ev['title']}» ({_when(prev, owner)}) отменена."
    elif any(str(prev.get(k)) != str(ev.get(k)) for k in ("start_utc", "end_utc", "location")):
        where = f", {ev['location']}" if ev.get("location") else ""
        text = f"Встреча «{ev['title']}» перенесена: {_when(ev, owner)}{where}. Ответьте в Орбите, придёте ли."
    else:
        return
    for a in members:
        try:
            access_gate.notify_user_sync(int(a["user_id"]), text)
        except Exception as e:
            print(f"[orbita_invite_mail] notify member={a.get('user_id')} err={e!r}")


def apply_replies(owner: int, messages: list[Any]) -> int:
    """Ответы гостей из почтовика (письма METHOD:REPLY во «Входящих» организатора) → статус гостя."""
    from assistant.services import orbita_rsvp_page
    from assistant.stores import orbita_events

    applied = 0
    for msg in messages:
        try:
            parts = list(msg.walk()) if hasattr(msg, "walk") else []
        except Exception:
            continue
        for part in parts:
            ctype = part.get_content_type()
            if ctype not in ("text/calendar", "application/ics"):
                continue
            try:
                payload = part.get_payload(decode=True) or b""
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
            reply = ics.parse_reply(text)
            if not reply:
                continue
            ev = orbita_events.get_by_ical_uid(reply["uid"])
            if not ev or ev["owner_user_id"] != str(int(owner)) or ev["status"] == "cancelled":
                continue
            guest = next((a for a in ev["attendees"] if a["email"] == reply["email"]), None)
            if not guest:
                continue
            if guest.get("response") != reply["response"]:
                orbita_events.set_response(ev["id"], email=reply["email"], response=reply["response"])
                orbita_rsvp_page._tell_organizer(ev, reply["email"], reply["response"])
                applied += 1
            break
    return applied
