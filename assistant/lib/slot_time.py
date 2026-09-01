"""Разбор времени из текста при выборе слота."""

from __future__ import annotations

import re
from datetime import date, datetime

from assistant.lib.calendar_datetime_parse import (
    calendar_extract_move_date,
    calendar_extract_time,
    calendar_parse_explicit_date,
    calendar_relative_day,
)

_RE_HOUR_ONLY = re.compile(r"^\s*(\d{1,2})\s*$")


def parse_time_from_user_text(text: str) -> tuple[int, int] | None:
    """«10» → 10:00, «10:30» / «в 10» — через calendar_extract_time."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    m = _RE_HOUR_ONLY.match(raw)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0
    return calendar_extract_time(text)


def resolve_pick_day_and_time(
    text: str,
    *,
    today: date,
    fallback_day_iso: str,
) -> tuple[str, tuple[int, int] | None]:
    """День и время из ответа при выборе слота («сегодня в 22:00», «10»)."""
    raw = (text or "").strip()
    th = parse_time_from_user_text(raw)
    if th is None:
        th = calendar_extract_time(raw)
    day_iso = (fallback_day_iso or today.isoformat())[:10]
    explicit = (
        calendar_extract_move_date(raw, today)
        or calendar_relative_day(raw, today, role="any")
        or calendar_parse_explicit_date(raw, today)
    )
    if explicit is not None:
        day_iso = explicit.isoformat()
    return day_iso, th


def parse_datetime_from_user_text(text: str, *, now: datetime) -> datetime | None:
    """«сегодня в 22:00», «завтра в 10», «в 18:30» — дата и время в TZ now."""
    from datetime import time as time_cls

    raw = (text or "").strip()
    if not raw:
        return None
    today = now.date()
    tz = now.tzinfo
    day = (
        calendar_extract_move_date(raw, today)
        or calendar_relative_day(raw, today, role="any")
        or calendar_parse_explicit_date(raw, today)
    )
    th = parse_time_from_user_text(raw)
    if th is None:
        th = calendar_extract_time(raw)
    if th is None:
        return None
    if day is None:
        day = today
    return datetime.combine(day, time_cls(th[0], th[1]), tzinfo=tz)


def user_text_mentions_calendar_day(text: str, *, today: date) -> bool:
    """Есть ли в тексте явная дата (сегодня, завтра, 5 июня, …)."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(
        calendar_extract_move_date(raw, today)
        or calendar_relative_day(raw, today, role="any")
        or calendar_parse_explicit_date(raw, today)
    )
