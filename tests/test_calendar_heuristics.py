"""Тесты эвристик календаря (без bot.py / Telegram)."""

import unittest

from assistant.lib.calendar_event_utils import (
    calendar_busy_from_events,
    calendar_clarify_expects_title,
    calendar_entry_kind_label,
    calendar_event_counts_as_busy,
    calendar_is_meeting_overview_query,
    calendar_title_is_placeholder,
    calendar_try_resolve_clarify,
)
from assistant.lib.calendar_intent_heuristics import (
    calendar_apply_relative_day,
    calendar_detect_route_kind,
    calendar_free_slots_hide_meetings,
)


class TestCalendarHeuristics(unittest.TestCase):
    def test_create_with_typo_vstrecha_case(self):
        self.assertEqual(
            calendar_detect_route_kind("поставь встрече на 12 сегодня"),
            "create",
        )

    def test_create_standard_phrase(self):
        self.assertEqual(
            calendar_detect_route_kind("поставь встречу на 12:00 сегодня"),
            "create",
        )

    def test_create_organize_with_attendees_tomorrow(self):
        phrase = (
            "организуй встречу с Вовой и Димой на завтра в 14:00, "
            "тема «Улучшение скрипта с точки зрения продуктовой ценности»"
        )
        self.assertEqual(calendar_detect_route_kind(phrase), "create")
        self.assertFalse(calendar_is_meeting_overview_query(phrase))

    def test_create_after_spaced_bot_mention(self):
        from assistant.lib.message_context import strip_bot_mention
        from assistant.nlu.regex import regex_route

        raw = (
            "@PshAssistent bot организуй встречу с Вовой и Димой на завтра в 14:00, "
            "тема «Улучшение скрипта»"
        )
        clean = strip_bot_mention(raw, "PshAssistent_bot")
        self.assertTrue(clean.lower().startswith("организуй"))
        self.assertEqual(calendar_detect_route_kind(clean), "create")
        route = regex_route(clean)
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "calendar")
        self.assertEqual(route.sub_intent, "create")

    def test_list_meetings_tomorrow_still_free(self):
        self.assertEqual(
            calendar_detect_route_kind("какие встречи завтра"),
            "free",
        )

    def test_not_bitrix_task(self):
        self.assertIsNone(calendar_detect_route_kind("создай задачу позвонить"))

    def test_update_move_meeting_quoted_title(self):
        phrase = "в понедельник у меня встреча «кугра» передвинь ее на 15:00"
        self.assertEqual(calendar_detect_route_kind(phrase), "update")
        self.assertFalse(calendar_is_meeting_overview_query(phrase))

    def test_update_reschedule_with_topic(self):
        phrase = "перенеси встречу про тестирование на 18:00"
        self.assertEqual(calendar_detect_route_kind(phrase), "update")
        self.assertFalse(calendar_is_meeting_overview_query(phrase))

    def test_relative_day_tomorrow(self):
        parsed: dict = {}
        calendar_apply_relative_day(
            parsed, "Какие встречи завтра?", today_iso="2026-05-17"
        )
        self.assertEqual(parsed.get("free_slots_date"), "2026-05-18")

    def test_relative_day_monday(self):
        parsed: dict = {}
        calendar_apply_relative_day(
            parsed,
            "какие встречи у меня в понедельник?",
            today_iso="2026-05-22",
        )
        self.assertEqual(parsed.get("free_slots_date"), "2026-05-25")

    def test_hide_meetings_for_meeting_query(self):
        self.assertFalse(calendar_free_slots_hide_meetings("Какие встречи завтра?"))

    def test_hide_meetings_reordered_phrase(self):
        self.assertFalse(
            calendar_free_slots_hide_meetings("какие встречи у меня в понедельник?")
        )

    def test_hide_meetings_for_free_slots_query(self):
        self.assertTrue(calendar_free_slots_hide_meetings("когда я свободен завтра"))

    def test_meeting_overview_flexible_order(self):
        self.assertTrue(
            calendar_is_meeting_overview_query("какие встречи у меня в понедельник")
        )

    def test_clarify_update_match_query(self):
        pending = {
            "mode": "await_clarify",
            "clarify_field": "match_query",
            "parsed": {
                "intent": "update_event",
                "match_query": "тестирование",
                "start": "2026-05-22T18:00:00",
            },
        }
        out = calendar_try_resolve_clarify(pending, "автоматизация тестирования")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("match_query"), "автоматизация тестирования")
        self.assertEqual(out.get("intent"), "update_event")

    def test_clarify_title_answer(self):
        pending = {
            "mode": "await_clarify",
            "parsed": {
                "intent": "create_event",
                "title": "",
                "start": "2026-05-22T12:00:00",
                "questions": ["Как назвать встречу?"],
            },
        }
        out = calendar_try_resolve_clarify(pending, "оперритм")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("title"), "оперритм")
        self.assertFalse(out.get("need_more_info"))

    def test_clarify_kakoe_nazvanie(self):
        pending = {
            "mode": "await_clarify",
            "clarify_field": "title",
            "questions": ["Какое название у встречи?"],
            "parsed": {
                "intent": "create_event",
                "title": "на 12",
                "start": "2026-05-22T12:00:00",
            },
        }
        out = calendar_try_resolve_clarify(pending, "оперритм")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("title"), "оперритм")

    def test_clarify_kakova_tema_with_placeholder_title(self):
        pending = {
            "mode": "await_clarify",
            "clarify_field": "title",
            "questions": ["Какова тема встречи?"],
            "parsed": {
                "intent": "create_event",
                "title": "Встреча",
                "start": "2026-05-22T12:00:00",
                "need_more_info": True,
                "questions": ["Какова тема встречи?"],
            },
        }
        self.assertTrue(calendar_clarify_expects_title(pending))
        self.assertTrue(calendar_title_is_placeholder("Встреча"))
        out = calendar_try_resolve_clarify(pending, "оперритм")
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out.get("title"), "оперритм")

    def test_busy_from_timed_event(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Europe/Moscow")
        d = datetime(2026, 5, 22, 9, 0, tzinfo=tz)
        work_start = datetime(2026, 5, 22, 9, 0, tzinfo=tz)
        work_end = datetime(2026, 5, 22, 20, 0, tzinfo=tz)
        ev = {
            "start": {"dateTime": "2026-05-22T10:00:00+03:00"},
            "end": {"dateTime": "2026-05-22T11:00:00+03:00"},
        }
        busy = calendar_busy_from_events(
            [ev], tz=tz, work_start=work_start, work_end=work_end
        )
        self.assertEqual(len(busy), 1)
        self.assertEqual(busy[0][0].hour, 10)

    def test_entry_kind_out_of_office(self):
        self.assertEqual(
            calendar_entry_kind_label({"eventType": "outOfOffice"}),
            "Занятость",
        )

    def test_cancelled_not_busy(self):
        self.assertFalse(
            calendar_event_counts_as_busy({"status": "cancelled"})
        )


if __name__ == "__main__":
    unittest.main()
