import unittest

from assistant.nlu import regex as regex_route_mod
from assistant.nlu.regex import (
    parse_summary_intent,
    parse_summary_search_intent,
    parse_transcribe_intent,
)


class SummaryIntentTests(unittest.TestCase):
    def test_parse_summary_intent(self) -> None:
        self.assertTrue(parse_summary_intent("сделай саммари"))
        self.assertTrue(parse_summary_intent("саммари, пожалуйста"))
        self.assertTrue(parse_summary_intent("выжимка этого файла"))
        self.assertTrue(parse_summary_intent("сделай протокол"))
        self.assertTrue(parse_summary_intent("протокол встречи"))
        self.assertFalse(parse_summary_intent("транскрибируй"))

    def test_summary_not_transcribe(self) -> None:
        self.assertFalse(parse_transcribe_intent("сделай саммари"))
        self.assertTrue(parse_transcribe_intent("транскрибируй"))

    def test_parse_summary_search_intent(self) -> None:
        q = parse_summary_search_intent("найди саммари про мобильное приложение")
        self.assertIsNotNone(q)
        self.assertIn("мобильное", q or "")
        q2 = parse_summary_search_intent("что решили по интеграции Zoom")
        self.assertIsNotNone(q2)
        self.assertIn("интеграции", q2 or "")
        q3 = parse_summary_search_intent("какие задачи ставили Андрею")
        self.assertIsNotNone(q3)
        self.assertIn("Андрею", q3 or "")

    def test_regex_route_summary(self) -> None:
        r = regex_route_mod.regex_route("сделай саммари")
        self.assertIsNotNone(r)
        self.assertEqual(r.skill, "summary")
        r2 = regex_route_mod.regex_route("протокол")
        self.assertIsNotNone(r2)
        self.assertEqual(r2.skill, "summary")

    def test_regex_route_summary_search(self) -> None:
        r = regex_route_mod.regex_route("что решили по релизу")
        self.assertIsNotNone(r)
        self.assertEqual(r.skill, "summary_search")


if __name__ == "__main__":
    unittest.main()
