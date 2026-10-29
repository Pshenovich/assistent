"""Приглашения iCalendar (RFC 5545 / iTIP RFC 5546): собрать REQUEST/CANCEL и разобрать REPLY.

Почтовики (Gmail, Outlook, Яндекс) показывают письмо с частью text/calendar; method=REQUEST
как приглашение с кнопками «Да / Нет / Может быть»; ответ гостя приходит организатору письмом METHOD:REPLY.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

PRODID = "-//Orbita//Meetings//RU"
_PARTSTAT_TO_RESPONSE = {"ACCEPTED": "accepted", "DECLINED": "declined", "TENTATIVE": "tentative",
                         "NEEDS-ACTION": "needsAction"}


def _esc(text: str) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def _param(text: str) -> str:
    """Значение параметра (CN=…): кавычки, если есть спецсимволы; сами кавычки убираем."""
    t = str(text or "").replace('"', "'")
    return f'"{t}"' if re.search(r"[;:,]", t) else t


def _fold(line: str) -> str:
    """Строки длиннее 75 байт переносим: CRLF + пробел (не разрывая UTF-8)."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 75:
            out.append(cur)
            cur, size = " " + ch, 1 + n
        else:
            cur += ch
            size += n
    out.append(cur)
    return "\r\n".join(out)


def _dt(value: str | datetime) -> str:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build(event: dict[str, Any], *, method: str = "REQUEST", attendees: Optional[list[dict[str, Any]]] = None,
          organizer_name: str = "", rsvp_url: str = "") -> str:
    """event — запись orbita_events (ical_uid, title, start_utc, end_utc, sequence, organizer_email…).

    attendees — кого перечислить в приглашении (по умолчанию все); для CANCEL — те, кому отменяем.
    """
    method = method.upper()
    atts = attendees if attendees is not None else event.get("attendees") or []
    desc = str(event.get("description") or "").strip()
    if event.get("video_url"):
        desc = "Подключиться: " + str(event["video_url"]) + ("\n\n" + desc if desc else "")
    if rsvp_url:
        desc = (desc + "\n\n" if desc else "") + "Ответить: " + rsvp_url
    lines = [
        "BEGIN:VCALENDAR",
        f"PRODID:{PRODID}",
        "VERSION:2.0",
        "CALSCALE:GREGORIAN",
        f"METHOD:{method}",
        "BEGIN:VEVENT",
        f"UID:{event['ical_uid']}",
        f"SEQUENCE:{int(event.get('sequence') or 0)}",
        f"DTSTAMP:{_dt(datetime.now(timezone.utc))}",
        f"DTSTART:{_dt(event['start_utc'])}",
        f"DTEND:{_dt(event['end_utc'])}",
        f"SUMMARY:{_esc(event.get('title') or 'Встреча')}",
    ]
    if desc:
        lines.append(f"DESCRIPTION:{_esc(desc)}")
    if event.get("location") or event.get("video_url"):
        lines.append(f"LOCATION:{_esc(event.get('location') or event.get('video_url'))}")
    if event.get("video_url"):
        lines.append(f"URL:{event['video_url']}")
    if event.get("organizer_email"):
        cn = f";CN={_param(organizer_name)}" if organizer_name else ""
        lines.append(f"ORGANIZER{cn}:mailto:{event['organizer_email']}")
    for a in atts:
        cn = f";CN={_param(a['name'])}" if a.get("name") else ""
        partstat = {"accepted": "ACCEPTED", "declined": "DECLINED", "tentative": "TENTATIVE"}.get(
            str(a.get("response") or ""), "NEEDS-ACTION")
        lines.append(f"ATTENDEE{cn};ROLE=REQ-PARTICIPANT;PARTSTAT={partstat};RSVP=TRUE:mailto:{a['email']}")
    lines.append("STATUS:CANCELLED" if method == "CANCEL" or event.get("status") == "cancelled" else "STATUS:CONFIRMED")
    lines += ["TRANSP:OPAQUE", "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def _unfold(text: str) -> list[str]:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for ln in raw.split("\n"):
        if ln[:1] in (" ", "\t") and out:
            out[-1] += ln[1:]
        elif ln:
            out.append(ln)
    return out


def parse_reply(text: str) -> Optional[dict[str, Any]]:
    """METHOD:REPLY → {uid, email, response}; иначе None."""
    lines = _unfold(text)
    if not any(ln.upper() == "METHOD:REPLY" for ln in lines):
        return None
    uid = ""
    email = ""
    partstat = ""
    for ln in lines:
        name, _, value = ln.partition(":")
        key = name.split(";", 1)[0].upper()
        if key == "UID":
            uid = value.strip()
        elif key == "ATTENDEE":
            m = re.search(r"PARTSTAT=([A-Z-]+)", name.upper())
            if m:
                partstat = m.group(1)
            em = re.search(r"mailto:([^\s;]+)", ln, flags=re.I)
            if em:
                email = em.group(1).strip().lower()
    response = _PARTSTAT_TO_RESPONSE.get(partstat)
    if not uid or not email or not response:
        return None
    return {"uid": uid, "email": email, "response": response}
