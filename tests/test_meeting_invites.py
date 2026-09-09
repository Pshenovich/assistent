"""Приглашения на встречу и RSVP."""

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from assistant.services import meeting_invites as inv


class TestMeetingInvites(unittest.TestCase):
    def test_parse_invite_callback(self) -> None:
        self.assertEqual(
            inv.parse_invite_callback("inv:y:abc123"),
            (inv.RSVP_ACCEPTED, "abc123"),
        )
        self.assertEqual(
            inv.parse_invite_callback("inv:m:tok"),
            (inv.RSVP_TENTATIVE, "tok"),
        )
        self.assertIsNone(inv.parse_invite_callback("cal:stp:x"))

    def test_format_invite_html(self) -> None:
        class U:
            username = "artyawn"
            first_name = "Art"

        result = {
            "summary": "Синк",
            "html_link": "https://calendar.google.com/event",
            "start": datetime(2026, 6, 5, 9, 30),
            "end": datetime(2026, 6, 5, 10, 0),
        }
        text = inv.format_invite_message_html(
            result, organizer=U(), organizer_uid=871463833
        )
        self.assertIn("@artyawn", text)
        self.assertIn("Синк", text)
        self.assertIn("Вы сможете принять участие?", text)

    def test_invitee_targets_skips_organizer(self) -> None:
        with patch.object(
            inv, "telegram_id_for_attendee_email", side_effect=[871463833, 106278723]
        ):
            with patch.object(inv, "is_user_allowed", return_value=True):
                out = inv.invitee_targets(
                    871463833,
                    ["artem@test.com", "knazorlov@gmail.com"],
                )
        self.assertEqual(out, [(106278723, "knazorlov@gmail.com")])

    def test_collect_pending_incoming_invites(self) -> None:
        from datetime import datetime, timedelta, timezone

        start = datetime.now(timezone.utc) + timedelta(hours=2)
        end = start + timedelta(hours=1)
        raw = {
            "id": "ev-in",
            "summary": "Синк",
            "htmlLink": "https://calendar.google.com/event",
            "organizer": {
                "email": "org@example.com",
                "displayName": "Org",
                "self": False,
            },
            "attendees": [
                {
                    "email": "me@example.com",
                    "self": True,
                    "responseStatus": "needsAction",
                }
            ],
        }
        norm = {
            "event_id": "ev-in",
            "calendar_id": "primary",
            "summary": "Синк",
            "html_link": "https://calendar.google.com/event",
            "start": start,
            "end": end,
            "raw": raw,
        }
        with patch(
            "assistant.services.calendar_sources.list_events_in_window",
            return_value=[norm],
        ), patch("assistant.services.calendar._tz_for", return_value=timezone.utc):
            out = inv.collect_pending_incoming_invites(1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["event_id"], "ev-in")
        self.assertEqual(out[0]["invitee_email"], "me@example.com")

    def test_incoming_invite_html_uses_organizer_name(self) -> None:
        text = inv.format_incoming_invite_html(
            {
                "summary": "Синк",
                "html_link": "https://calendar.google.com/event",
                "organizer": {"displayName": "Артём", "email": "a@x.com"},
            },
            user_id=1,
        )
        self.assertIn("Артём", text)
        self.assertIn("Синк", text)
        self.assertIn("Вы сможете принять участие?", text)

    def test_apply_attendee_status_updates_self(self) -> None:
        event = {
            "attendees": [
                {"email": "org@x.com", "organizer": True, "responseStatus": "accepted"},
                {
                    "email": "me@x.com",
                    "self": True,
                    "responseStatus": "needsAction",
                },
            ]
        }
        rows = inv._apply_attendee_status(
            event,
            invitee_email="me@x.com",
            response_status=inv.RSVP_ACCEPTED,
            match_self=True,
        )
        self.assertEqual(rows[1]["responseStatus"], "accepted")
        self.assertEqual(rows[0]["responseStatus"], "accepted")

    def test_format_after_answer(self) -> None:
        base = "@artyawn приглашает вас\n\nВы сможете принять участие?"
        out = inv.format_invite_after_answer_html(base, inv.RSVP_ACCEPTED)
        self.assertIn("Приду", out)
        self.assertNotIn("Вы сможете", out)

    def test_miniapp_notify_filters_new_emails(self) -> None:
        import asyncio
        import os
        from unittest.mock import AsyncMock

        async def run() -> None:
            bot = MagicMock()
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=bot)
            cm.__aexit__ = AsyncMock(return_value=None)
            with patch.object(inv, "invites_enabled", return_value=True), patch.dict(
                os.environ, {"TELEGRAM_BOT_TOKEN": "token"}
            ), patch.object(inv, "Bot", return_value=cm), patch.object(
                inv, "notify_invitees", new_callable=AsyncMock, return_value=1
            ) as notify:
                await inv.notify_invitees_for_miniapp(
                    organizer_uid=1,
                    organizer_user={"username": "me", "first_name": "Art"},
                    result={
                        "event_id": "e1",
                        "calendar_id": "primary",
                        "attendee_emails": ["a@x.com", "b@x.com"],
                    },
                    only_emails=["b@x.com"],
                )
            payload = notify.call_args.kwargs["result"]
            self.assertEqual(payload["attendee_emails"], ["b@x.com"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
