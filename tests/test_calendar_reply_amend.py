"""Реплай на сообщение бота о встрече — перенос / изменение."""

import unittest

from assistant.lib.calendar_intent_heuristics import calendar_detect_amend_kind
from assistant.lib.calendar_pending_store import (
    get_event_message_ref,
    put_event_message_ref,
)
from assistant.lib.message_context import parse_bot_calendar_event_message
from assistant.skills.calendar import _event_candidate_from_ref, _resolve_replied_calendar_event


class TestCalendarReplyAmend(unittest.TestCase):
    def test_amend_kind_short_reply(self):
        self.assertEqual(calendar_detect_amend_kind("передвинь на 15:00"), "update")
        self.assertEqual(calendar_detect_amend_kind("измени встречу на завтра"), "update")
        self.assertEqual(calendar_detect_amend_kind("отмени"), "delete")
        self.assertIsNone(calendar_detect_amend_kind("привет"))

    def test_parse_bot_created_message_with_year_and_zoom(self):
        text = (
            "Создана встреча: Улучшение скрипта с точки зрения продуктовой ценности\n"
            "21 июля 2026, 14:00–15:00 Участники: a@b.ru 🔗 Zoom-встреча\n"
            "[^zoom]: Zoom-встреча"
        )
        parsed = parse_bot_calendar_event_message(text)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(
            parsed["summary"],
            "Улучшение скрипта с точки зрения продуктовой ценности",
        )
        self.assertIn("21 июля 2026", parsed.get("when_line", ""))

    def test_match_date_from_when_line(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from assistant.skills.calendar import _match_date_from_when_line

        now = datetime(2026, 7, 20, 12, 0, tzinfo=ZoneInfo("Europe/Moscow"))
        self.assertEqual(
            _match_date_from_when_line("21 июля 2026, 14:00–15:00", now=now),
            "2026-07-21",
        )

    def test_event_message_ref_roundtrip(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from assistant.lib import calendar_pending_store as cps

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                put_event_message_ref(
                    100,
                    42,
                    user_id=7,
                    event_id="ev-1",
                    calendar_id="primary",
                    summary="Demo",
                    start_iso="2026-06-10T15:00:00",
                )
                ref = get_event_message_ref(100, 42)
                self.assertIsNotNone(ref)
                assert ref is not None
                self.assertEqual(ref["event_id"], "ev-1")
                self.assertEqual(ref["user_id"], 7)

    def test_event_candidate_from_ref(self):
        cand = _event_candidate_from_ref(
            {
                "event_id": "abc",
                "calendar_id": "primary",
                "summary": "Тест",
                "start_iso": "2026-06-10T15:00:00+03:00",
            }
        )
        self.assertEqual(cand["id"], "abc")
        self.assertEqual(cand["summary"], "Тест")

    def test_resolve_replied_event_via_store(self):
        class _User:
            id = 99
            is_bot = True

        class _Chat:
            id = 500

        class _Rep:
            message_id = 12
            from_user = _User()
            text = "Создана встреча: Demo"
            caption = None

        class _Msg:
            chat = _Chat()
            reply_to_message = _Rep()

        import tempfile
        from pathlib import Path
        from unittest import mock

        from assistant.lib import calendar_pending_store as cps

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                put_event_message_ref(
                    500,
                    12,
                    user_id=7,
                    event_id="ev-99",
                    summary="Demo",
                )
                hit = _resolve_replied_calendar_event(_Msg(), user_id=7, bot_id=99)
                self.assertIsNotNone(hit)
                assert hit is not None
                self.assertEqual(hit["id"], "ev-99")


if __name__ == "__main__":
    unittest.main()
