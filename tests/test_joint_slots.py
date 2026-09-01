"""Совместные и персональные слоты при занятости."""

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from assistant.services import calendar as cal_svc


class TestJointSlots(unittest.TestCase):
    def test_suggest_joint_uses_workday_not_only_gaps(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        day = "2026-06-05"
        starts = [
            datetime(2026, 6, 5, 14, 0, tzinfo=tz),
            datetime(2026, 6, 5, 15, 0, tzinfo=tz),
        ]
        with patch.object(cal_svc, "iter_workday_slot_starts", return_value=starts):
            with patch.object(cal_svc, "owner_conflict_at", return_value=None):
                with patch.object(cal_svc, "_freebusy_has_overlap", return_value=False):
                    with patch(
                        "assistant.lib.calendar_attendees.iter_attendees_for_calendar_busy_check",
                        return_value=[(200, "Диана")],
                    ):
                        out = cal_svc.suggest_joint_slot_starts(
                            100, {"attendee_names": ["Диана"]}, day
                        )
        self.assertEqual(len(out), 2)

    def test_attendee_fallback_when_joint_empty(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        busy_start = datetime(2026, 6, 5, 9, 30, tzinfo=tz)
        parsed = {"attendee_names": ["Диана"], "start": busy_start.isoformat()}
        with patch.object(cal_svc, "owner_conflict_at", return_value="Занято"):
            with patch(
                "assistant.lib.calendar_attendees.iter_attendees_for_calendar_busy_check",
                return_value=[(200, "Диана")],
            ):
                with patch.object(cal_svc, "suggest_joint_slot_starts", return_value=[]):
                    with patch.object(
                        cal_svc, "_freebusy_has_overlap", side_effect=lambda uid, *_: uid == 200
                    ):
                        with patch.object(
                            cal_svc,
                            "_suggest_person_free_slots",
                            return_value=(
                                [datetime(2026, 6, 5, 14, 0, tzinfo=tz)],
                                60,
                                "active",
                            ),
                        ):
                            with patch(
                                "assistant.lib.calendar_attendees.attendee_names_missing_calendar_link",
                                return_value=[],
                            ):
                                check = cal_svc.validate_meeting_slot(
                                    100, parsed, busy_start
                                )
        self.assertTrue(check.get("attendee_alt_starts"))
        self.assertEqual(check.get("attendee_alt_duration_min"), 60)

    def test_attendee_fallback_shorter_duration(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        busy_start = datetime(2026, 6, 5, 9, 30, tzinfo=tz)
        parsed = {"attendee_names": ["Диана"], "start": busy_start.isoformat(), "duration_min": 60}
        with patch.object(cal_svc, "owner_conflict_at", return_value="Занято"):
            with patch(
                "assistant.lib.calendar_attendees.iter_attendees_for_calendar_busy_check",
                return_value=[(200, "Диана")],
            ):
                with patch.object(cal_svc, "suggest_joint_slot_starts", return_value=[]):
                    with patch.object(cal_svc, "_freebusy_has_overlap", return_value=True):
                        with patch.object(
                            cal_svc,
                            "_suggest_person_free_slots",
                            return_value=(
                                [datetime(2026, 6, 5, 17, 0, tzinfo=tz)],
                                30,
                                "active",
                            ),
                        ):
                            with patch(
                                "assistant.lib.calendar_attendees.attendee_names_missing_calendar_link",
                                return_value=[],
                            ):
                                check = cal_svc.validate_meeting_slot(
                                    100, parsed, busy_start
                                )
        self.assertEqual(
            [s.strftime("%H:%M") for s in check.get("attendee_alt_starts") or []],
            ["17:00"],
        )
        self.assertEqual(check.get("attendee_alt_duration_min"), 30)

    def test_attendee_fallback_when_only_owner_busy(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        busy_start = datetime(2026, 6, 5, 9, 30, tzinfo=tz)
        parsed = {"attendee_names": ["Диана"], "start": busy_start.isoformat()}
        with patch.object(cal_svc, "owner_conflict_at", return_value="Занято"):
            with patch(
                "assistant.lib.calendar_attendees.iter_attendees_for_calendar_busy_check",
                return_value=[(200, "Диана")],
            ):
                with patch.object(cal_svc, "suggest_joint_slot_starts", return_value=[]):
                    with patch.object(cal_svc, "_freebusy_has_overlap", return_value=False):
                        with patch.object(
                            cal_svc, "suggest_slot_starts", return_value=[]
                        ):
                            with patch.object(
                                cal_svc,
                                "_suggest_person_free_slots",
                                side_effect=lambda uid, day, **kw: (
                                    ([], 60, "active")
                                    if uid == 100
                                    else (
                                        [datetime(2026, 6, 5, 14, 0, tzinfo=tz)],
                                        60,
                                        "active",
                                    )
                                ),
                            ):
                                with patch(
                                    "assistant.lib.calendar_attendees.attendee_names_missing_calendar_link",
                                    return_value=[],
                                ):
                                    check = cal_svc.validate_meeting_slot(
                                        100, parsed, busy_start
                                    )
        self.assertEqual(
            check.get("attendee_alt_starts"),
            [datetime(2026, 6, 5, 14, 0, tzinfo=tz)],
        )


if __name__ == "__main__":
    unittest.main()
