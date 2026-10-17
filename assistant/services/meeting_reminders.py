"""Напоминания за N минут до встречи."""

from __future__ import annotations

import html
import os
from datetime import datetime, timedelta
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from assistant.integrations import google_calendar_oauth
from assistant.lib.meeting_links import (
    extract_online_meeting_urls,
    online_link_button_text,
    online_link_label,
)
from assistant.services.calendar import format_event_when
from assistant.services import calendar_sources as cal_sources
from assistant.stores import meeting_reminders_sent as sent_store


def reminder_minutes_before() -> int:
    try:
        return max(1, int(os.getenv("MEETING_REMINDER_MINUTES_BEFORE", "15") or "15"))
    except ValueError:
        return 15


def reminder_window_sec() -> float:
    try:
        return float(os.getenv("MEETING_REMINDER_WINDOW_SEC", "90") or "90")
    except ValueError:
        return 90.0


def iter_calendar_user_ids() -> list[int]:
    out: list[int] = []
    for path in google_calendar_oauth._tokens_dir().glob("*.json"):
        try:
            out.append(int(path.stem))
        except ValueError:
            continue
    return out


def fetch_upcoming_timed_events(user_id: int, *, horizon_min: int = 25) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for

    tz = _tz_for(user_id)
    now = datetime.now(tz)
    end = now + timedelta(minutes=horizon_min)
    return cal_sources.list_events_in_window(user_id, now, end)


def web_push_payload_for_event(
    event: dict[str, Any], *, user_id: int, minutes_before: int
) -> dict[str, Any]:
    """Payload для PWA Web Push (клик открывает установленное приложение)."""
    from assistant.lib.webapp_public import webapp_event_url

    title = str(event.get("summary") or "Встреча").strip() or "Встреча"
    ev_id = str(event.get("event_id") or event.get("id") or "").strip()
    cal_id = str(event.get("calendar_id") or "primary").strip() or "primary"
    start = event.get("start")
    end = event.get("end")
    day = ""
    if isinstance(start, datetime):
        day = start.date().isoformat()
    when = ""
    if isinstance(start, datetime) and isinstance(end, datetime):
        when = format_event_when(start, end, user_id)
    body = f"Через {minutes_before} мин"
    if when:
        body = f"{body} · {when}"
    return {
        "title": title,
        "body": body,
        "url": webapp_event_url(ev_id, calendar_id=cal_id, day=day or None)
        if ev_id
        else "",
    }


def format_reminder_message(
    event: dict[str, Any], *, user_id: int, minutes_before: int
) -> tuple[str, InlineKeyboardMarkup | None]:
    from assistant.lib.webapp_public import webapp_event_url, webapp_launch_url

    title = html.escape(str(event.get("summary") or "Встреча"))
    ev_id = str(event.get("event_id") or event.get("id") or "").strip()
    cal_id = str(event.get("calendar_id") or "primary").strip() or "primary"
    start = event.get("start")
    end = event.get("end")
    day = ""
    if isinstance(start, datetime):
        day = start.date().isoformat()
    # Из TG https почти всегда остаётся во встроенном браузере — ведём через launch.html.
    link = (
        webapp_launch_url(event_id=ev_id, calendar_id=cal_id, day=day or None)
        if ev_id
        else ""
    )
    direct = (
        webapp_event_url(ev_id, calendar_id=cal_id, day=day or None) if ev_id else ""
    )
    when = ""
    if isinstance(start, datetime) and isinstance(end, datetime):
        when = format_event_when(start, end, user_id)
    lines = [
        f"Через {minutes_before} минут у вас встреча:",
    ]
    if link:
        lines.append(f'<a href="{html.escape(link)}">{title}</a>')
    else:
        lines.append(f"<b>{title}</b>")
    if when:
        lines.append(f"Время: {html.escape(when)}")
    raw = event.get("raw") or {}
    meet_urls = (
        extract_online_meeting_urls(raw)
        if isinstance(raw, dict)
        else []
    )
    rows: list[list[InlineKeyboardButton]] = []
    if direct:
        rows.append(
            [InlineKeyboardButton("Открыть в Leo", url=link or direct)]
        )
    if meet_urls:
        if len(meet_urls) == 1:
            url = meet_urls[0]
            label = online_link_label(url)
            lines.append(
                f'Ссылка на встречу: <a href="{html.escape(url)}">{html.escape(label)}</a>'
            )
        else:
            lines.append("Ссылки на встречу:")
            for url in meet_urls:
                label = online_link_label(url)
                lines.append(
                    f'• <a href="{html.escape(url)}">{html.escape(label)}</a>'
                )
        total = len(meet_urls)
        for i, url in enumerate(meet_urls, start=1):
            rows.append(
                [
                    InlineKeyboardButton(
                        online_link_button_text(url, index=i, total=total),
                        url=url,
                    )
                ]
            )
    kb = InlineKeyboardMarkup(rows) if rows else None
    return "\n".join(lines), kb


def events_due_for_reminder(
    user_id: int, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    from assistant.services.calendar import _tz_for

    tz = _tz_for(user_id)
    now = now or datetime.now(tz)
    mins = reminder_minutes_before()
    window = reminder_window_sec() / 2.0
    target = timedelta(minutes=mins)
    due: list[dict[str, Any]] = []
    for ev in fetch_upcoming_timed_events(user_id, horizon_min=mins + 12):
        cal_id = str(ev.get("calendar_id") or "")
        ev_id = str(ev.get("event_id") or "")
        start_iso = str(ev.get("start_iso") or "")
        if not ev_id or not cal_id:
            continue
        if sent_store.was_sent(user_id, cal_id, ev_id, start_iso):
            continue
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        from assistant.lib.calendar_event_utils import calendar_event_is_task

        if calendar_event_is_task(raw):
            continue
        start = ev.get("start")
        if not isinstance(start, datetime):
            continue
        delta_sec = (start - now).total_seconds()
        target_sec = target.total_seconds()
        if abs(delta_sec - target_sec) <= window:
            due.append(ev)
    return due


def process_reminders_for_user(user_id: int) -> int:
    """Возвращает число отправленных напоминаний (синхронно, без Telegram)."""
    return len(collect_due_reminders(user_id))


def collect_due_reminders(user_id: int) -> list[dict[str, Any]]:
    return events_due_for_reminder(user_id)
