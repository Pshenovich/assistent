"""Тесты regex-маршрутизации Bitrix24."""

import unittest

from assistant.nlu.regex import parse_bitrix_intent, regex_route


class TestBitrixRegex(unittest.TestCase):
    def test_explicit_bitrix(self) -> None:
        self.assertIsNotNone(parse_bitrix_intent("создай задачу в битрикс"))
        route = regex_route("найди задачи в bitrix24")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "bitrix")

    def test_task_actions(self) -> None:
        self.assertIsNotNone(parse_bitrix_intent("поставь задачу на завтра"))
        self.assertIsNotNone(parse_bitrix_intent("найди задачу про интеграцию"))
        self.assertIsNotNone(parse_bitrix_intent("дай описание задачи по переносу на кордекса"))

    def test_task_followup_phrases(self) -> None:
        from assistant.skills.bitrix import bitrix_likely_followup

        self.assertTrue(bitrix_likely_followup("получить описание этой задачи"))
        self.assertTrue(bitrix_likely_followup("покажи описание этой задачи"))

    def test_crm_keywords(self) -> None:
        self.assertIsNotNone(parse_bitrix_intent("создай сделку в CRM"))
        self.assertIsNotNone(parse_bitrix_intent("перемести сделку на стадию"))

    def test_not_calendar_meeting(self) -> None:
        self.assertIsNone(parse_bitrix_intent("встреча завтра в 15:00"))
        route = regex_route("поставь встречу на завтра")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "calendar")
