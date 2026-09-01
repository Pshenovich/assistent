import unittest

from assistant.lib.calendar_intent_heuristics import calendar_detect_route_kind
from assistant.lib.calendar_event_utils import calendar_is_meeting_overview_query
from assistant.lib.journal_retrieval import is_journal_archive_query
from assistant.nlu.regex import regex_route


class JournalQaRoutingTests(unittest.TestCase):
    def test_last_meeting_tasks_is_archive_not_calendar(self) -> None:
        q = "какие задачи были на последней встрече?"
        self.assertTrue(is_journal_archive_query(q))
        self.assertIsNone(calendar_detect_route_kind(q))
        self.assertFalse(calendar_is_meeting_overview_query(q.lower()))

    def test_regex_routes_journal_qa(self) -> None:
        route = regex_route("какие задачи были на последней встрече?")
        self.assertIsNotNone(route)
        self.assertEqual(route.skill, "journal_qa")

    def test_today_meetings_still_calendar(self) -> None:
        q = "какие встречи у меня сегодня?"
        self.assertFalse(is_journal_archive_query(q))
        self.assertEqual(calendar_detect_route_kind(q), "free")
        route = regex_route(q)
        self.assertIsNotNone(route)
        self.assertEqual(route.skill, "calendar")


if __name__ == "__main__":
    unittest.main()
