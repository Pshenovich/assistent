"""Слоты: не предлагать прошедшее время."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from assistant.lib.calendar_slots import (
    compute_earliest_bookable_start,
    filter_future_slot_starts,
)


class CalendarSlotsTests(unittest.TestCase):
    def test_earliest_today_after_2139(self):
        tz = ZoneInfo("Europe/Moscow")
        day = date(2026, 6, 4)
        work_start = datetime.combine(day, datetime.strptime("09:00", "%H:%M").time(), tzinfo=tz)
        work_end = datetime.combine(day, datetime.strptime("22:00", "%H:%M").time(), tzinfo=tz)
        now = datetime.combine(day, datetime.strptime("21:39", "%H:%M").time(), tzinfo=tz)
        earliest = compute_earliest_bookable_start(
            now=now,
            day=day,
            work_start=work_start,
            work_end=work_end,
            grace_min=15,
            step_min=30,
        )
        self.assertEqual(earliest, datetime.combine(day, datetime.strptime("22:00", "%H:%M").time(), tzinfo=tz))

    def test_filter_drops_morning_slots(self):
        tz = ZoneInfo("Europe/Moscow")
        day = date(2026, 6, 4)
        work_end = datetime.combine(day, datetime.strptime("22:00", "%H:%M").time(), tzinfo=tz)
        not_before = datetime.combine(day, datetime.strptime("20:00", "%H:%M").time(), tzinfo=tz)
        past = datetime.combine(day, datetime.strptime("10:00", "%H:%M").time(), tzinfo=tz)
        future = datetime.combine(day, datetime.strptime("21:00", "%H:%M").time(), tzinfo=tz)
        out = filter_future_slot_starts(
            [past, future],
            not_before=not_before,
            work_end=work_end,
            duration=timedelta(minutes=30),
        )
        self.assertEqual(out, [future])


if __name__ == "__main__":
    unittest.main()
