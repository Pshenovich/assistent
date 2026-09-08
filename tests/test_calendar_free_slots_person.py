"""Свободные слоты другого человека, не организатора."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from assistant.lib.calendar_attendees import (
    find_contact_by_name,
    resolve_free_slots_person,
)
from assistant.services import calendar as cal_svc


class FreeSlotsPersonTests(unittest.TestCase):
    def test_prefers_full_name_over_other_andrey(self) -> None:
        contacts = [
            {
                "name": "Андрей Медведев",
                "email": "medvedev.a@smtalk.ru",
                "aliases": [],
            },
            {
                "name": "Андрей Мыздриков",
                "email": "amyzdrikov77@gmail.com",
                "aliases": [],
                "telegram_username": "asmalltalk",
            },
        ]
        with patch(
            "assistant.lib.calendar_attendees.contacts.load_contacts",
            return_value=contacts,
        ):
            hit = find_contact_by_name(1, "Андрея Мыздрикова")
        assert hit is not None
        self.assertEqual(hit["email"], "amyzdrikov77@gmail.com")

    def test_resolve_uses_other_user_token(self) -> None:
        parsed = {"attendee_names": ["Андрея Мыздрикова"]}
        contact = {
            "name": "Андрей Мыздриков",
            "email": "amyzdrikov77@gmail.com",
            "telegram_username": "asmalltalk",
        }
        with patch(
            "assistant.lib.calendar_attendees.find_contact_by_name",
            return_value=contact,
        ):
            with patch(
                "assistant.lib.calendar_attendees.attendee_calendar_user_id",
                return_value=555,
            ):
                with patch(
                    "assistant.integrations.google_calendar_oauth.user_token_path"
                ) as tp:
                    tp.return_value.is_file.return_value = True
                    out = resolve_free_slots_person(100, parsed)
        self.assertEqual(out["kind"], "person")
        self.assertEqual(out["user_id"], 555)
        self.assertFalse(out.get("via_email"))

    def test_message_does_not_use_owner_slots_for_named_person(self) -> None:
        parsed = {"attendee_names": ["Андрея Мыздрикова"]}
        target = {
            "kind": "person",
            "user_id": 555,
            "label": "Андрей Мыздриков",
            "email": "amyzdrikov77@gmail.com",
        }
        with patch(
            "assistant.lib.calendar_attendees.resolve_free_slots_person",
            return_value=target,
        ):
            with patch.object(
                cal_svc,
                "free_slots_day",
                return_value=[],
            ) as free_day:
                text = cal_svc.free_slots_message(
                    100, parsed, "2026-09-08", telegram_username="pshenovich"
                )
        free_day.assert_called_once_with(555, "2026-09-08")
        self.assertIn("Андрей Мыздриков", text)

    def test_missing_contact_message(self) -> None:
        parsed = {"attendee_names": ["Неизвестен"]}
        with patch(
            "assistant.lib.calendar_attendees.find_contact_by_name",
            return_value=None,
        ):
            text = cal_svc.free_slots_message(100, parsed, "2026-09-08")
        self.assertIn("контактах нет", text.lower())
        self.assertNotIn("Свободно 2026-09-08", text)


if __name__ == "__main__":
    unittest.main()
