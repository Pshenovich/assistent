"""Группа: создание встречи после @mention / reply — роутинг и pending-store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from assistant.bot.group_gate import should_process_in_chat, should_process_message
from assistant.lib import calendar_pending_store as cps
from assistant.lib.calendar_intent_heuristics import calendar_detect_route_kind
from assistant.lib.message_context import (
    add_reply_author_as_attendee,
    strip_bot_mention,
)
from assistant.nlu.regex import regex_route


def _msg(
    *,
    text: str | None = None,
    reply_to=None,
):
    return SimpleNamespace(
        text=text,
        caption=None,
        entities=None,
        caption_entities=None,
        reply_to_message=reply_to,
    )


class GroupMeetingCreateTests(unittest.TestCase):
    def test_group_create_requires_mention(self):
        self.assertFalse(
            should_process_in_chat(
                _msg(text="создай встречу завтра в 15:00"),
                chat_type="supergroup",
                bot_id=1,
                bot_username="LeoBot",
            )
        )
        self.assertTrue(
            should_process_in_chat(
                _msg(text="@LeoBot создай встречу завтра в 15:00"),
                chat_type="supergroup",
                bot_id=1,
                bot_username="LeoBot",
            )
        )

    def test_stripped_mention_routes_to_calendar_create(self):
        clean = strip_bot_mention(
            "@LeoBot создай встречу завтра в 15:00 с Иваном", "LeoBot"
        )
        self.assertEqual(clean, "создай встречу завтра в 15:00 с Иваном")
        self.assertEqual(calendar_detect_route_kind(clean), "create")
        route = regex_route(clean)
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "calendar")
        self.assertEqual(route.sub_intent, "create")
        self.assertEqual(route.calendar_kind, "create")

    def test_stripped_mention_zoom_routes_to_zoom_instant(self):
        clean = strip_bot_mention("@LeoBot создай зум", "LeoBot")
        route = regex_route(clean)
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "calendar")
        self.assertEqual(route.calendar_kind, "zoom")

    def test_reply_author_added_as_attendee(self):
        parsed: dict = {"attendees": [], "attendee_names": []}
        add_reply_author_as_attendee(
            parsed,
            {
                "telegram_user_id": 99,
                "first_name": "Иван",
                "last_name": "Петров",
                "username": "ivan",
            },
            owner_user_id=42,
            owner_username="owner",
        )
        self.assertIn("Иван Петров", parsed.get("attendee_names") or [])

    def test_missing_attendee_pending_allows_followup_without_mention(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                cps.set_pending(
                    -1001,
                    {
                        "kind": "missing_attendees",
                        "user_id": 42,
                        "parsed": {"intent": "create_event"},
                        "missing_names": ["Артем"],
                    },
                    user_id=42,
                )
                update = SimpleNamespace(
                    message=_msg(text="Артем artem@example.com"),
                    effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
                    effective_user=SimpleNamespace(id=42),
                )
                context = SimpleNamespace(
                    bot=SimpleNamespace(id=1, username="LeoBot"),
                    user_data={},
                )
                self.assertTrue(should_process_message(update, context))

    def test_zoom_pending_import_resolves(self):
        """Регрессия: Zoom/Telemost pending должен импортировать lib.store, не stores."""
        import assistant.skills.zoom as zoom_skill
        import assistant.skills.telemost as telemost_skill
        import inspect

        src_z = inspect.getsource(zoom_skill.try_continue_pending)
        src_t = inspect.getsource(telemost_skill.try_continue_pending)
        self.assertIn("assistant.lib import calendar_pending_store", src_z)
        self.assertIn("assistant.lib import calendar_pending_store", src_t)
        self.assertNotIn("assistant.stores import calendar_pending_store", src_z)
        self.assertNotIn("assistant.stores import calendar_pending_store", src_t)


if __name__ == "__main__":
    unittest.main()
