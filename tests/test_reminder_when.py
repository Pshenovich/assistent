"""Ответ «сегодня в 22» после вопроса о напоминании."""

from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.lib.slot_time import parse_datetime_from_user_text


class ReminderWhenTests(unittest.TestCase):
    def test_parse_today_at_22(self):
        tz = ZoneInfo("Europe/Moscow")
        now = datetime(2026, 6, 4, 21, 39, tzinfo=tz)
        got = parse_datetime_from_user_text("сегодня в 22", now=now)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got.date(), now.date())
        self.assertEqual((got.hour, got.minute), (22, 0))


if __name__ == "__main__":
    unittest.main()
