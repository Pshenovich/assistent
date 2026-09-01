"""Имена: кириллица/латиница, прозвища."""

import unittest

from assistant.lib.calendar_attendees import find_contact_by_name, names_match
from assistant.lib.name_equivalence import names_equivalent
from unittest.mock import patch


class TestNameEquivalence(unittest.TestCase):
    def test_vova_cyrillic_latin(self) -> None:
        self.assertTrue(names_equivalent("Вова", "vova"))
        self.assertTrue(names_match("Вова", "vova"))

    def test_find_contact_by_vova_alias(self) -> None:
        contact = {
            "name": "knazorlov",
            "email": "knazorlov@gmail.com",
            "aliases": ["vova", "Владимир Савашкевич"],
            "telegram_username": "pshenovich",
        }
        with patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[contact]
        ):
            hit = find_contact_by_name(871463833, "Вова", telegram_username="artyawn")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.get("telegram_username"), "pshenovich")


if __name__ == "__main__":
    unittest.main()
