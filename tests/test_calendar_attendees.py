"""Участники встречи из текста и контактов."""

from __future__ import annotations

import unittest

from assistant.lib.calendar_attendees import (
    enrich_parsed_attendees,
    extract_attendee_names_from_text,
    extract_telegram_usernames_from_text,
    names_match,
)


class CalendarAttendeesTests(unittest.TestCase):
    def test_extract_from_meeting_with(self):
        names = extract_attendee_names_from_text("поставь встречу с Гарей сегодня в 11")
        self.assertTrue(any("гар" in n.lower() for n in names))

    def test_enrich_merges_llm(self):
        parsed: dict = {"attendee_names": []}
        enrich_parsed_attendees(parsed, "встреча с Марией завтра в 15")
        self.assertTrue(parsed.get("attendee_names"))

    def test_names_match_russian_case(self):
        self.assertTrue(names_match("Гаря", "Гарий"))
        self.assertTrue(names_match("Гарей", "Гара"))

    def test_skip_today_word(self):
        self.assertEqual(extract_attendee_names_from_text("встреча сегодня в 11"), [])

    def test_extract_slots_u_person(self):
        names = extract_attendee_names_from_text(
            "свободные слоты у Андрея Мыздрикова сегодня"
        )
        self.assertTrue(any("андрей" in n.lower() or "андрея" in n.lower() for n in names))
        self.assertTrue(any("мыздрик" in n.lower() for n in names))

    def test_skip_u_menya(self):
        self.assertEqual(
            extract_attendee_names_from_text("свободные слоты у меня сегодня"),
            [],
        )

    def test_extract_telegram_username(self):
        names = extract_telegram_usernames_from_text("встреча с @ivanov завтра")
        self.assertEqual(names, ["@ivanov"])


if __name__ == "__main__":
    unittest.main()
