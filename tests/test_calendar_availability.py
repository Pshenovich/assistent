"""Занятость участников для таймлайна миниаппа."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from assistant.services import calendar as cal_svc


class TestCalendarAvailability(unittest.TestCase):
    def test_event_attendee_emails_skips_self(self) -> None:
        ev = {
            "attendees": [
                {"email": "me@x.com", "self": True},
                {"email": "anna@x.com", "displayName": "Анна"},
                {"email": "anna@x.com"},
            ]
        }
        self.assertEqual(cal_svc.event_attendee_emails(ev), ["anna@x.com"])

    def test_availability_includes_organizer_and_flags(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        work = (
            datetime(2026, 9, 8, 9, 0, tzinfo=tz),
            datetime(2026, 9, 8, 18, 0, tzinfo=tz),
        )
        owner_busy = [
            (datetime(2026, 9, 8, 10, 0, tzinfo=tz), datetime(2026, 9, 8, 11, 0, tzinfo=tz))
        ]
        anna_busy = [
            (datetime(2026, 9, 8, 14, 0, tzinfo=tz), datetime(2026, 9, 8, 15, 0, tzinfo=tz))
        ]
        contact = {"name": "Анна", "email": "anna@x.com", "telegram_user_id": 200}

        def fake_busy(uid: int, day: str, **_kw):
            return owner_busy if int(uid) == 100 else anna_busy

        def fake_token(uid: int):
            path = MagicMock()
            path.is_file.return_value = int(uid) in (100, 200)
            return path

        def fake_leo(_contact, email: str = "", **_kw):
            if str(email).lower() == "anna@x.com":
                return 200
            return None

        with patch.object(cal_svc, "_work_window", return_value=work), patch.object(
            cal_svc, "_tz_name_for", return_value="Europe/Moscow"
        ), patch.object(
            cal_svc, "busy_intervals_day", side_effect=fake_busy
        ), patch.object(
            cal_svc, "busy_intervals_via_viewer_email", return_value=None
        ), patch.object(
            cal_svc.google_calendar_oauth, "user_token_path", side_effect=fake_token
        ), patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[contact]
        ), patch(
            "assistant.lib.calendar_attendees.leo_calendar_user_id", side_effect=fake_leo
        ):
            out = cal_svc.availability_for_attendees(
                100, "2026-09-08", ["anna@x.com", "ext@y.com"]
            )

        self.assertEqual(out["date"], "2026-09-08")
        people = {p["id"]: p for p in out["people"]}
        self.assertTrue(people["organizer"]["calendar"])
        self.assertEqual(people["organizer"]["label"], "Вы")
        self.assertTrue(people["anna@x.com"]["calendar"])
        self.assertEqual(people["anna@x.com"]["label"], "Анна")
        self.assertFalse(people["ext@y.com"]["calendar"])
        self.assertEqual(people["ext@y.com"]["busy"], [])
        self.assertEqual(len(people["anna@x.com"]["busy"]), 1)

    def test_viewer_freebusy_when_no_leo_calendar(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        work = (
            datetime(2026, 9, 8, 9, 0, tzinfo=tz),
            datetime(2026, 9, 8, 18, 0, tzinfo=tz),
        )
        via = [
            (datetime(2026, 9, 8, 12, 0, tzinfo=tz), datetime(2026, 9, 8, 13, 0, tzinfo=tz))
        ]

        def fake_token(uid: int):
            path = MagicMock()
            path.is_file.return_value = int(uid) == 100
            return path

        with patch.object(cal_svc, "_work_window", return_value=work), patch.object(
            cal_svc, "_tz_name_for", return_value="Europe/Moscow"
        ), patch.object(cal_svc, "busy_intervals_day", return_value=[]), patch.object(
            cal_svc, "busy_intervals_via_viewer_email", return_value=via
        ), patch.object(
            cal_svc.google_calendar_oauth, "user_token_path", side_effect=fake_token
        ), patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[]
        ), patch(
            "assistant.lib.calendar_attendees.leo_calendar_user_id", return_value=None
        ):
            out = cal_svc.availability_for_attendees(100, "2026-09-08", ["ext@y.com"])

        people = {p["id"]: p for p in out["people"]}
        self.assertTrue(people["ext@y.com"]["calendar"])
        self.assertEqual(len(people["ext@y.com"]["busy"]), 1)
