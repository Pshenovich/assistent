"""Встречи Орбиты: создание, правка, ответы, видимость приглашённым."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.lib.calendar_event_utils import event_is_invitation, event_needs_rsvp
from assistant.services import meeting_invites, orbita_calendar
from assistant.stores import notes as notes_store
from assistant.stores import orbita_events as store

USERS = {"anna@acme.io": 2, "petr@acme.io": 3}


class OrbitaCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._p = [
            mock.patch.object(meeting_invites, "telegram_id_for_attendee_email",
                              lambda owner, em, **kw: USERS.get(em)),
            mock.patch.object(orbita_calendar, "organizer_email", lambda uid: "vova@acme.io"),
        ]
        for p in self._p:
            p.start()
        self.day = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def _create(self, **extra):
        parsed = {"title": "Планёрка", "start": self.day.isoformat(), "end": (self.day + timedelta(minutes=30)).isoformat(),
                  "attendees": ["Anna@acme.io", "guest@other.com", "vova@acme.io"], **extra}
        return orbita_calendar.create(1, parsed)

    def test_create_and_visibility(self) -> None:
        res = self._create()
        self.assertTrue(res["event_id"].startswith("orb-"))
        self.assertEqual(res["calendar_id"], "orbita")
        self.assertEqual(res["attendee_emails"], ["anna@acme.io", "guest@other.com"])  # себя не зовём
        win = (self.day - timedelta(hours=1), self.day + timedelta(hours=2))
        owner_view = orbita_calendar.events_for(1, *win)
        self.assertEqual(len(owner_view), 1)
        self.assertFalse(event_is_invitation(owner_view[0]))
        anna_view = orbita_calendar.events_for(2, *win)
        self.assertTrue(event_is_invitation(anna_view[0]) and event_needs_rsvp(anna_view[0]))
        self.assertEqual(orbita_calendar.events_for(3, *win), [])  # Петра не звали

    def test_respond_and_busy(self) -> None:
        res = self._create()
        orbita_calendar.respond(2, res["event_id"], "declined")
        win = (self.day - timedelta(hours=1), self.day + timedelta(hours=2))
        self.assertEqual(store.busy_for_user(2, *win), [])
        self.assertEqual(len(store.busy_for_user(1, *win)), 1)
        with self.assertRaises(ValueError):
            orbita_calendar.respond(3, res["event_id"], "accepted")

    def test_update_moves_and_resets_answers(self) -> None:
        res = self._create()
        orbita_calendar.respond(2, res["event_id"], "accepted")
        later = self.day + timedelta(hours=3)
        upd = orbita_calendar.update(1, res["event_id"], {"start": later.isoformat(), "attendees": ["anna@acme.io", "petr@acme.io"]})
        self.assertEqual(upd["end"] - upd["start"], timedelta(minutes=30))  # длительность сохранили
        self.assertEqual(upd["previous_attendee_emails"], ["anna@acme.io", "guest@other.com"])
        self.assertEqual(meeting_invites.added_invitees(upd), ["petr@acme.io"])
        ev = store.get(store.parse_id(res["event_id"]))
        anna = next(a for a in ev["attendees"] if a["email"] == "anna@acme.io")
        self.assertEqual(anna["response"], "needsAction")  # время сдвинули — спросить заново
        self.assertEqual(ev["sequence"], 1)
        with self.assertRaises(ValueError):
            orbita_calendar.update(2, res["event_id"], {"title": "Чужая"})

    def test_cancel_hides(self) -> None:
        res = self._create()
        orbita_calendar.cancel(1, res["event_id"])
        win = (self.day - timedelta(hours=1), self.day + timedelta(hours=2))
        self.assertEqual(orbita_calendar.events_for(2, *win), [])

    def test_recurrence_not_supported_yet(self) -> None:
        with self.assertRaises(ValueError):
            self._create(recurrence=["RRULE:FREQ=WEEKLY"])

    def test_busy_without_google(self) -> None:
        """Занятость для свободных слотов: встречи Орбиты + задачи, без Google."""
        from assistant.services import calendar as cal

        self._create()  # 10:00–10:30 UTC, Анна приглашена
        day = self.day.date().isoformat()
        ws, we = self.day.replace(hour=0), self.day.replace(hour=23)
        with mock.patch.object(cal, "has_google", lambda uid: False):
            owner = cal.busy_intervals_day(1, day, work_start=ws, work_end=we)
            anna = cal.busy_intervals_day(2, day, work_start=ws, work_end=we)
            petr = cal.busy_intervals_day(3, day, work_start=ws, work_end=we)
        self.assertEqual([(s.hour, e.minute) for s, e in owner], [(10, 30)])
        self.assertEqual(len(anna), 1)
        self.assertEqual(petr, [])


class LeoToolsWithoutGoogleTests(unittest.TestCase):
    """Лео без Google: свободные окна, встреча Орбиты, список встреч."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        from assistant.services import calendar as cal
        from assistant.services import gpt_calendar_tools as tools

        self._p = [
            mock.patch.object(tools, "has_google_calendar", lambda uid: False),
            mock.patch.object(cal, "has_google", lambda uid: False),
            mock.patch.object(meeting_invites, "telegram_id_for_attendee_email", lambda o, em, **kw: None),
            mock.patch.object(orbita_calendar, "organizer_email", lambda uid: ""),
        ]
        for p in self._p:
            p.start()

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_leo_creates_orbita_meeting(self) -> None:
        import json

        from assistant.services import gpt_calendar_tools as tools

        day = datetime.now(timezone.utc) + timedelta(days=7)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        ctx = tools.GptCalendarContext(uid=1)
        slots = json.loads(tools.tool_calendar_free_slots(ctx, {"date": day.date().isoformat()}))
        self.assertNotIn("error", slots)
        self.assertTrue(slots["slots"])
        start = slots["slots"][0]["start"][:16]
        res = json.loads(tools.tool_calendar_create_event(ctx, {"title": "Синк по проекту Альфа", "start": start, "duration_min": 30}))
        self.assertTrue(res.get("created"), res)
        self.assertTrue(res["event_id"].startswith("orb-"))
        self.assertEqual(ctx.created_events[0]["calendar_id"], "orbita")
        listed = json.loads(tools.tool_calendar_events(ctx, {"start_date": day.date().isoformat(), "end_date": day.date().isoformat()}))
        self.assertIn("Синк по проекту Альфа", [i["title"] for i in listed["items"]])
        again = json.loads(tools.tool_calendar_free_slots(ctx, {"date": day.date().isoformat()}))
        self.assertNotIn(slots["slots"][0]["start"], [s["start"] for s in again["slots"]])  # слот занят
        clash = json.loads(tools.tool_calendar_create_event(ctx, {"title": "Ещё", "start": start, "duration_min": 30}))
        self.assertTrue(clash.get("conflict"), clash)


if __name__ == "__main__":
    unittest.main()
