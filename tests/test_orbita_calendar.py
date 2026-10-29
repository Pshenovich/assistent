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


if __name__ == "__main__":
    unittest.main()
