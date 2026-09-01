from datetime import date

from assistant.lib.slot_time import (
    parse_datetime_from_user_text,
    parse_time_from_user_text,
    resolve_pick_day_and_time,
    user_text_mentions_calendar_day,
)
from assistant.services.calendar import needs_time_selection, user_text_has_explicit_time


def test_parse_hour_only():
    assert parse_time_from_user_text("10") == (10, 0)
    assert parse_time_from_user_text("  9 ") == (9, 0)


def test_needs_time_when_day_only():
    parsed = {"start": "2026-06-05T09:00:00", "slot_day": "2026-06-05"}
    assert not user_text_has_explicit_time("встреча в пятницу с Иваном")
    assert needs_time_selection(parsed, "встреча в пятницу с Иваном")


def test_has_time_in_text():
    assert user_text_has_explicit_time("встреча завтра в 15:30")
    parsed = {"slot_day": "2026-06-05"}
    assert not needs_time_selection(parsed, "встреча завтра в 15:30")


def test_resolve_pick_today_overrides_fallback():
    today = date(2026, 6, 4)
    day, th = resolve_pick_day_and_time(
        "сегодня в 22:00",
        today=today,
        fallback_day_iso="2026-06-05",
    )
    assert day == "2026-06-04"
    assert th == (22, 0)


def test_user_text_mentions_day():
    today = date(2026, 6, 4)
    assert user_text_mentions_calendar_day("поставь встречу с Геннадием", today=today) is False
    assert user_text_mentions_calendar_day("встреча завтра в 15:00", today=today) is True
