from __future__ import annotations

import unittest

from assistant.lib.calendar_event_utils import (
    event_is_declined_by_self,
    event_is_invitation,
    event_needs_rsvp,
    event_self_response_status,
)


class TestCalendarEventRsvp(unittest.TestCase):
    def test_own_meeting_with_guests_is_not_invitation(self) -> None:
        ev = {
            "organizer": {"email": "me@example.com", "self": True},
            "attendees": [
                {
                    "email": "me@example.com",
                    "self": True,
                    "organizer": True,
                    "responseStatus": "accepted",
                },
                {"email": "guest@example.com", "responseStatus": "needsAction"},
            ],
        }
        self.assertFalse(event_is_invitation(ev))
        self.assertFalse(event_needs_rsvp(ev))
        self.assertEqual(event_self_response_status(ev), "accepted")

    def test_incoming_invite_needs_rsvp(self) -> None:
        ev = {
            "organizer": {
                "email": "artem.danilin.1999@gmail.com",
                "displayName": "Artem",
                "self": False,
            },
            "attendees": [
                {
                    "email": "artem.danilin.1999@gmail.com",
                    "organizer": True,
                    "responseStatus": "accepted",
                },
                {
                    "email": "me@example.com",
                    "self": True,
                    "responseStatus": "needsAction",
                },
            ],
        }
        self.assertTrue(event_is_invitation(ev))
        self.assertTrue(event_needs_rsvp(ev))
        self.assertEqual(event_self_response_status(ev), "needsAction")
        self.assertFalse(event_is_declined_by_self(ev))

    def test_accepted_invite_does_not_need_rsvp(self) -> None:
        ev = {
            "organizer": {"email": "org@example.com", "self": False},
            "attendees": [
                {"email": "me@example.com", "self": True, "responseStatus": "tentative"},
            ],
        }
        self.assertTrue(event_is_invitation(ev))
        self.assertFalse(event_needs_rsvp(ev))
        self.assertEqual(event_self_response_status(ev), "tentative")

    def test_declined_invite(self) -> None:
        ev = {
            "organizer": {"email": "org@example.com", "self": False},
            "attendees": [
                {"email": "me@example.com", "self": True, "responseStatus": "declined"},
            ],
        }
        self.assertTrue(event_is_declined_by_self(ev))
        self.assertFalse(event_needs_rsvp(ev))

    def test_subscribed_calendar_without_self_is_not_invitation(self) -> None:
        ev = {
            "organizer": {"email": "bitrix@example.com", "self": False},
            "attendees": [],
        }
        self.assertFalse(event_is_invitation(ev))
        self.assertFalse(event_needs_rsvp(ev))

    def test_serialize_calendar_event_rsvp_fields(self) -> None:
        from usage_server import _serialize_calendar_event

        item = _serialize_calendar_event(
            {
                "id": "ev1",
                "_calendarId": "primary",
                "summary": "Синк",
                "htmlLink": "https://calendar.google.com/event",
                "start": {"dateTime": "2026-09-09T10:00:00+03:00"},
                "end": {"dateTime": "2026-09-09T11:00:00+03:00"},
                "organizer": {"email": "org@example.com", "self": False},
                "attendees": [
                    {"email": "org@example.com", "organizer": True},
                    {
                        "email": "me@example.com",
                        "self": True,
                        "responseStatus": "needsAction",
                    },
                ],
            }
        )
        self.assertFalse(item["is_organizer"])
        self.assertTrue(item["needs_rsvp"])
        self.assertEqual(item["self_response_status"], "needsAction")


if __name__ == "__main__":
    unittest.main()
