"""Ссылки на свободные слоты: store, пересечение, бронь."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from assistant.services import booking_links as booking_svc
from assistant.services import calendar as cal_svc
from assistant.stores import booking_links as store
from assistant.stores import notes as notes_store


class BookingLinkStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_and_resolve(self) -> None:
        link = store.create_link(
            42,
            title="Синк",
            attendees=["anna@x.com", "anna@x.com", "bad"],
            duration_min=30,
            link_mode="reusable",
        )
        self.assertEqual(link["title"], "Синк")
        self.assertEqual(link["attendee_emails"], ["anna@x.com"])
        self.assertEqual(link["duration_min"], 30)
        self.assertEqual(link["link_mode"], "reusable")
        resolved = store.resolve_link(link["token"])
        assert resolved is not None
        self.assertEqual(resolved["owner_user_id"], "42")
        self.assertEqual(store.link_status(resolved), "open")

    def test_one_shot_consume_and_rollback(self) -> None:
        link = store.create_link(7, link_mode="one_shot")
        self.assertTrue(store.try_mark_used(link["token"]))
        again = store.resolve_link(link["token"])
        self.assertEqual(store.link_status(again), "used")
        self.assertFalse(store.try_mark_used(link["token"]))
        store.clear_used(link["token"])
        opened = store.resolve_link(link["token"])
        self.assertEqual(store.link_status(opened), "open")

    def test_expiring_and_revoke(self) -> None:
        link = store.create_link(3, link_mode="expiring", expire_days=7)
        self.assertIsNotNone(link["expires_at"])
        self.assertEqual(store.link_status(link), "open")
        past = datetime.now(timezone.utc) + timedelta(days=8)
        self.assertEqual(store.link_status(link, now=past), "expired")
        self.assertTrue(store.revoke_link(3, link["token"]))
        self.assertIsNone(store.resolve_link(link["token"]))

    def test_rejects_bad_token(self) -> None:
        self.assertIsNone(store.resolve_link(""))
        self.assertIsNone(store.resolve_link("short"))
        self.assertIsNone(store.resolve_link("../etc/passwd"))


class JointFreeSlotsRangeTests(unittest.TestCase):
    def test_intersection_skips_owner_and_attendee_busy(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        day = datetime(2026, 6, 5, 8, 0, tzinfo=tz)
        work = (
            datetime(2026, 6, 5, 9, 0, tzinfo=tz),
            datetime(2026, 6, 5, 12, 0, tzinfo=tz),
        )
        owner_busy = [
            (datetime(2026, 6, 5, 9, 0, tzinfo=tz), datetime(2026, 6, 5, 10, 0, tzinfo=tz))
        ]
        anna_busy = [
            (datetime(2026, 6, 5, 11, 0, tzinfo=tz), datetime(2026, 6, 5, 12, 0, tzinfo=tz))
        ]

        def fake_busy(uid: int, day_iso: str, **_kw):
            return owner_busy if int(uid) == 100 else anna_busy

        def fake_attendee(_owner, email, day_iso, **_kw):
            if email == "anna@x.com":
                return anna_busy
            return []

        with patch.object(cal_svc, "_tz_for", return_value=tz), patch.object(
            cal_svc, "_work_window", return_value=work
        ), patch.object(
            cal_svc, "busy_intervals_day", side_effect=fake_busy
        ), patch.object(
            cal_svc, "_attendee_busy_day", side_effect=fake_attendee
        ), patch.object(
            cal_svc, "earliest_bookable_start", return_value=work[0]
        ):
            slots = cal_svc.joint_free_slots_range(
                100,
                ["anna@x.com"],
                days=1,
                duration_min=60,
                now=day,
            )

        starts = [s["start"][11:16] for s in slots]
        self.assertEqual(starts, ["10:00"])

    def test_weekend_day_returns_no_slots(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        saturday = datetime(2026, 6, 6, 8, 0, tzinfo=tz)
        work = (
            datetime(2026, 6, 6, 9, 0, tzinfo=tz),
            datetime(2026, 6, 6, 18, 0, tzinfo=tz),
        )
        with patch.object(cal_svc, "_tz_for", return_value=tz), patch.object(
            cal_svc, "_work_window", return_value=work
        ), patch.object(cal_svc, "busy_intervals_day") as busy, patch.object(
            cal_svc, "earliest_bookable_start", return_value=work[0]
        ):
            slots = cal_svc.joint_free_slots_day(100, "2026-06-06", [], duration_min=30)
            ranged = cal_svc.joint_free_slots_range(
                100, [], days=1, duration_min=30, now=saturday
            )
        self.assertEqual(slots, [])
        self.assertEqual(ranged, [])
        busy.assert_not_called()
        horizon = cal_svc.booking_horizon_days(100, days=3, now=saturday)
        self.assertTrue(horizon[0]["weekend"])
        self.assertEqual(horizon[0]["date"], "2026-06-06")
        self.assertTrue(horizon[1]["weekend"])
        self.assertFalse(horizon[2]["weekend"])

    def test_slot_is_free_false_when_busy(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        work = (
            datetime(2026, 6, 5, 9, 0, tzinfo=tz),
            datetime(2026, 6, 5, 18, 0, tzinfo=tz),
        )
        busy = [
            (datetime(2026, 6, 5, 10, 0, tzinfo=tz), datetime(2026, 6, 5, 11, 0, tzinfo=tz))
        ]
        start = datetime(2026, 6, 5, 10, 0, tzinfo=tz)
        end = datetime(2026, 6, 5, 10, 30, tzinfo=tz)
        with patch.object(cal_svc, "_tz_for", return_value=tz), patch.object(
            cal_svc, "joint_busy_day", return_value=(busy, work[0], work[1])
        ), patch.object(cal_svc, "earliest_bookable_start", return_value=work[0]):
            self.assertFalse(cal_svc.joint_slot_is_free(100, start, end, []))
            later = datetime(2026, 6, 5, 11, 0, tzinfo=tz)
            self.assertTrue(
                cal_svc.joint_slot_is_free(
                    100, later, later + timedelta(minutes=30), []
                )
            )


class BookPublicSlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_create_without_calendar_still_returns_url(self) -> None:
        path = MagicMock()
        path.is_file.return_value = False
        with patch.object(
            booking_svc.google_calendar_oauth, "user_token_path", return_value=path
        ):
            created = booking_svc.create_booking_link(1, title="Синк")
        self.assertTrue(created["url"].endswith(created["token"]))
        self.assertIn("/book/", created["url"])

    def test_book_happy_path(self) -> None:
        path = MagicMock()
        path.is_file.return_value = True
        tz = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 6, 5, 10, 0, tzinfo=tz)
        with patch.object(
            booking_svc.google_calendar_oauth, "user_token_path", return_value=path
        ):
            created = booking_svc.create_booking_link(
                42,
                title="Синк",
                attendees=["anna@x.com"],
                duration_min=30,
                link_mode="reusable",
            )
        token = created["token"]
        with patch.object(cal_svc, "_parse_dt", return_value=start), patch.object(
            cal_svc, "joint_slot_is_free", return_value=True
        ) as free, patch.object(
            cal_svc,
            "create_event",
            return_value={
                "event_id": "ev-1",
                "summary": "Синк",
                "html_link": "https://cal",
                "attendee_emails": ["anna@x.com", "guest@x.com"],
            },
        ) as create:
            out = booking_svc.book_public_slot(
                token,
                start=start.isoformat(),
                duration_min=30,
                guest_email="guest@x.com",
            )
        self.assertTrue(out["ok"])
        self.assertEqual(out["event_id"], "ev-1")
        free.assert_called_once()
        parsed = create.call_args.args[1]
        self.assertEqual(parsed["title"], "Синк")
        self.assertIn("guest@x.com", parsed["attendees"])
        self.assertIn("anna@x.com", parsed["attendees"])

    def test_book_slot_taken(self) -> None:
        path = MagicMock()
        path.is_file.return_value = True
        tz = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 6, 5, 10, 0, tzinfo=tz)
        with patch.object(
            booking_svc.google_calendar_oauth, "user_token_path", return_value=path
        ):
            created = booking_svc.create_booking_link(9, link_mode="one_shot")
        with patch.object(cal_svc, "_parse_dt", return_value=start), patch.object(
            cal_svc, "joint_slot_is_free", return_value=False
        ):
            with self.assertRaises(booking_svc.BookingLinkError) as ctx:
                booking_svc.book_public_slot(
                    created["token"],
                    start=start.isoformat(),
                    duration_min=30,
                    guest_email="guest@x.com",
                )
        self.assertEqual(ctx.exception.reason, "slot_taken")
        link = store.resolve_link(created["token"])
        self.assertEqual(store.link_status(link), "open")

    def test_book_rejects_used_one_shot(self) -> None:
        path = MagicMock()
        path.is_file.return_value = True
        with patch.object(
            booking_svc.google_calendar_oauth, "user_token_path", return_value=path
        ):
            created = booking_svc.create_booking_link(4, link_mode="one_shot")
        store.try_mark_used(created["token"])
        with self.assertRaises(booking_svc.BookingLinkError) as ctx:
            booking_svc.book_public_slot(
                created["token"], start="2026-06-05T10:00:00", duration_min=30
            )
        self.assertEqual(ctx.exception.reason, "used")

    def test_meta_skips_calendar_and_lists_days(self) -> None:
        path = MagicMock()
        path.is_file.return_value = False
        with patch.object(
            booking_svc.google_calendar_oauth, "user_token_path", return_value=path
        ):
            created = booking_svc.create_booking_link(11, title="Созвон")
        with patch.object(cal_svc, "_tz_name_for", return_value="Europe/Moscow"), patch.object(
            cal_svc, "joint_free_slots_day"
        ) as slots_day, patch.object(cal_svc, "joint_free_slots_range") as slots_range:
            meta = booking_svc.public_booking_meta(created["token"])
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["title"], "Созвон")
        self.assertEqual(meta["timezone"], "Europe/Moscow")
        self.assertEqual(meta["slots"], [])
        self.assertGreaterEqual(len(meta["days"]), 5)
        self.assertIn("date", meta["days"][0])
        self.assertIn("weekend", meta["days"][0])
        slots_day.assert_not_called()
        slots_range.assert_not_called()
        html = booking_svc.public_booking_dates_html(meta["days"])
        self.assertIn("date-chip", html)
        self.assertIn("is-weekend", html)

    def test_book_requires_guest_email(self) -> None:
        created = booking_svc.create_booking_link(8, title="Синк")
        with self.assertRaises(booking_svc.BookingLinkError) as ctx:
            booking_svc.book_public_slot(
                created["token"], start="2026-06-05T10:00:00", duration_min=30
            )
        self.assertEqual(ctx.exception.reason, "bad_email")

    def test_book_accepts_comma_separated_guest_emails(self) -> None:
        tz = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 6, 5, 10, 0, tzinfo=tz)
        created = booking_svc.create_booking_link(8, title="Синк", attendees=["anna@x.com"])
        with patch.object(cal_svc, "_parse_dt", return_value=start), patch.object(
            cal_svc, "joint_slot_is_free", return_value=True
        ), patch.object(
            cal_svc,
            "create_event",
            return_value={"event_id": "ev-2", "summary": "Синк", "html_link": "https://cal"},
        ) as create:
            out = booking_svc.book_public_slot(
                created["token"],
                start=start.isoformat(),
                duration_min=30,
                guest_email="one@x.com, two@x.com",
                guest_emails=["two@x.com", "three@x.com"],
            )
        self.assertTrue(out["ok"])
        attendees = create.call_args.args[1]["attendees"]
        self.assertEqual(attendees, ["anna@x.com", "one@x.com", "two@x.com", "three@x.com"])
