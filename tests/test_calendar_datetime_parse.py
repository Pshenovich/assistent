"""Тесты разбора дат/времени календаря."""

import unittest
from datetime import date, datetime

from assistant.lib.calendar_datetime_parse import (
    calendar_build_llm_user_payload,
    calendar_ensure_future_datetime,
    calendar_extract_move_date,
    calendar_extract_time,
    calendar_normalize_parsed,
    calendar_relative_day,
    calendar_roll_date_forward,
)
from assistant.lib.calendar_event_utils import calendar_date_from_weekday_phrase


class TestCalendarDatetimeParse(unittest.TestCase):
    def test_tomorrow_relative(self) -> None:
        today = date(2026, 5, 22)
        self.assertEqual(
            calendar_relative_day("поставь встречу завтра в 15", today).isoformat(),
            "2026-05-23",
        )

    def test_next_monday_on_monday(self) -> None:
        today = date(2026, 5, 25)  # Monday
        d = calendar_date_from_weekday_phrase("следующий понедельник", today)
        self.assertEqual(d, date(2026, 6, 1))

    def test_move_date_after_na(self) -> None:
        today = date(2026, 5, 22)
        d = calendar_extract_move_date("перенеси встречу на 19 мая в 15:00", today)
        self.assertEqual(d, date(2026, 5, 19))

    def test_search_vs_move_split(self) -> None:
        today = date(2026, 5, 22)  # Friday
        search = calendar_relative_day(
            "в понедельник у меня встреча кугра передвинь на вторник в 15:00",
            today,
            role="search",
        )
        move = calendar_relative_day(
            "в понедельник у меня встреча кугра передвинь на вторник в 15:00",
            today,
            role="move",
        )
        self.assertEqual(search, date(2026, 5, 25))
        self.assertEqual(move, date(2026, 5, 26))

    def test_roll_past_month_day(self) -> None:
        today = date(2026, 5, 22)
        self.assertEqual(
            calendar_roll_date_forward(date(2026, 5, 10), today),
            date(2027, 5, 10),
        )

    def test_ensure_future_same_day_past_time(self) -> None:
        today = date(2026, 5, 22)
        now = datetime(2026, 5, 22, 18, 0, 0)
        past = datetime(2026, 5, 22, 10, 0, 0)
        fixed = calendar_ensure_future_datetime(
            past, now, today, intent="create_event"
        )
        self.assertEqual(fixed, datetime(2026, 5, 23, 10, 0, 0))

    def test_normalize_create_tomorrow(self) -> None:
        parsed: dict = {
            "intent": "create_event",
            "start": "2020-01-01T15:00:00",
            "title": "Созвон",
        }
        calendar_normalize_parsed(
            parsed,
            "поставь встречу завтра в 15:00",
            today_iso="2026-05-22",
            tz_name="Europe/Moscow",
        )
        self.assertEqual(parsed["start"][:10], "2026-05-23")
        self.assertIn("15:00", parsed["start"])

    def test_extract_time_v(self) -> None:
        self.assertEqual(calendar_extract_time("встреча в 15:30"), (15, 30))

    def test_llm_payload_has_reference(self) -> None:
        p = calendar_build_llm_user_payload(
            user_text="завтра",
            today_iso="2026-05-22",
            timezone="Europe/Moscow",
        )
        self.assertIn("reference", p)
        self.assertEqual(p["reference"]["tomorrow"], "2026-05-23")


if __name__ == "__main__":
    unittest.main()
