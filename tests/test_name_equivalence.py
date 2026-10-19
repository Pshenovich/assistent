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

    def test_declined_diminutive_finds_full_name(self) -> None:
        # «поставь встречу с Вовой» — творительный падеж; в контакте «Владимир …» и ник.
        for needle in ("Вовой", "Вове", "Володей", "Владимиром", "вова"):
            self.assertTrue(names_equivalent(needle, "Владимир"), needle)
        contact = {
            "name": "knazorlov",
            "email": "knazorlov@gmail.com",
            "aliases": ["vova", "Владимир Савашкевич"],
            "telegram_username": "pshenovich",
        }
        other = {"name": "Виктор Петров", "email": "v@x.ru", "aliases": []}
        with patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[other, contact]
        ):
            hit = find_contact_by_name(871463833, "Вовой", telegram_username="vinse_u")
        self.assertIsNotNone(hit)
        self.assertEqual(hit.get("telegram_username"), "pshenovich")

    def test_dictionary_names_do_not_fuzzy_match(self) -> None:
        self.assertFalse(names_equivalent("Саша", "Алексей"))
        self.assertFalse(names_equivalent("Вова", "Виктор"))
        self.assertTrue(names_equivalent("Маше", "Мария"))

    def test_nick_only_contact_found_by_orbita_profile_name(self) -> None:
        contact = {"name": "@pshenovich", "email": "", "aliases": [], "telegram_username": "pshenovich"}
        with patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[contact]
        ), patch(
            "assistant.stores.telegram_registry.lookup_user_id", return_value=555
        ), patch(
            "assistant.stores.note_members.get_profile",
            return_value={"first_name": "Владимир", "last_name": "Савашкевич"},
        ):
            hit = find_contact_by_name(871463833, "Вовой", telegram_username="vinse_u")
        self.assertIs(hit, contact)

    def test_nick_only_contact_invited_by_orbita_account_email(self) -> None:
        from assistant.lib.calendar_attendees import resolve_attendee_names

        contact = {"name": "Вова", "email": "", "aliases": [], "telegram_username": "pshenovich"}
        with patch(
            "assistant.stores.contacts_store.load_contacts", return_value=[contact]
        ), patch(
            "assistant.stores.telegram_registry.lookup_user_id", return_value=555
        ), patch(
            "assistant.lib.calendar_user_lookup.account_email_for_user", return_value="Vova@Gmail.com"
        ):
            att, missing = resolve_attendee_names(871463833, ["Вовой"])
        self.assertEqual(att, [{"email": "vova@gmail.com"}])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
