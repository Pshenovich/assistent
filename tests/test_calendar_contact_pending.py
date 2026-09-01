"""Контакт при создании встречи: падежи имён и формат строки."""

from __future__ import annotations

import re
import unittest

from assistant.lib.calendar_attendees import names_match

_RE_CONTACT_LINE = re.compile(
    r"^(.+?)\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})(?:\s+@([a-zA-Z][a-zA-Z0-9_]{4,31}))?\s*$"
)


class CalendarContactPendingTests(unittest.TestCase):
    def test_regex_contact_line_name_email(self):
        m = _RE_CONTACT_LINE.match("Геннадий gena@alegator.com")
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.group(1).strip(), "Геннадий")
        self.assertEqual(m.group(2).lower(), "gena@alegator.com")

    def test_names_match_instrumental(self):
        self.assertTrue(names_match("Геннадием", "Геннадий"))


if __name__ == "__main__":
    unittest.main()
