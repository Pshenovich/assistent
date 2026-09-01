"""Запасные слоты участника: короче по длительности и личный календарь."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from assistant.services import calendar as cal_svc


class TestPersonSlotFallback(unittest.TestCase):
    def test_tries_shorter_duration_on_active_calendar(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        day = "2026-06-19"
        calls: list[int] = []

        def _fake_slots(uid, day_iso, *, duration_min=60, max_count=8, calendar_ids=None):
            calls.append(int(duration_min))
            if duration_min == 60:
                return []
            return [datetime(2026, 6, 19, 17, 0, tzinfo=tz)]

        with patch.object(cal_svc.cal_sources, "get_active_calendar_ids", return_value=["bitrix"]):
            with patch.object(cal_svc, "_primary_calendar_id", return_value=None):
                with patch.object(cal_svc, "suggest_slot_starts", side_effect=_fake_slots):
                    starts, dur, src = cal_svc._suggest_person_free_slots(200, day, duration_min=60)
        self.assertEqual([s.strftime("%H:%M") for s in starts], ["17:00"])
        self.assertEqual(dur, 30)
        self.assertEqual(src, "active")
        self.assertEqual(calls, [60, 30])

    def test_falls_back_to_primary_calendar(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        day = "2026-06-19"
        seen: list[list[str] | None] = []

        def _fake_slots(uid, day_iso, *, duration_min=60, max_count=8, calendar_ids=None):
            seen.append(list(calendar_ids or []))
            if calendar_ids == ["bitrix"]:
                return []
            if calendar_ids == ["personal@gmail.com"]:
                return [datetime(2026, 6, 19, 14, 0, tzinfo=tz)]
            return []

        with patch.object(cal_svc.cal_sources, "get_active_calendar_ids", return_value=["bitrix"]):
            with patch.object(cal_svc, "_primary_calendar_id", return_value="personal@gmail.com"):
                with patch.object(cal_svc, "suggest_slot_starts", side_effect=_fake_slots):
                    starts, dur, src = cal_svc._suggest_person_free_slots(200, day, duration_min=60)
        self.assertEqual([s.strftime("%H:%M") for s in starts], ["14:00"])
        self.assertEqual(dur, 60)
        self.assertEqual(src, "primary")
        self.assertEqual(seen[0], ["bitrix"])
        self.assertEqual(seen[-1], ["personal@gmail.com"])


if __name__ == "__main__":
    unittest.main()
