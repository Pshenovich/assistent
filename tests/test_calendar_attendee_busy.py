"""Проверка занятости календаря участника, не организатора."""

import unittest
from unittest.mock import patch

from assistant.lib.calendar_attendees import (
    attendee_names_missing_calendar_link,
    iter_attendees_for_calendar_busy_check,
)


class TestAttendeeBusyResolution(unittest.TestCase):
    def test_resolve_invitee_by_contact_email(self) -> None:
        owner_id = 100
        parsed = {"attendee_names": ["Иван"], "attendees": []}
        contact = {
            "name": "Иван",
            "email": "ivan@gmail.com",
            "telegram_username": "",
        }
        with patch(
            "assistant.lib.calendar_attendees.find_contact_by_name",
            return_value=contact,
        ):
            with patch(
                "assistant.lib.calendar_attendees.attendee_calendar_user_id",
                return_value=None,
            ):
                with patch(
                    "assistant.lib.calendar_user_lookup.lookup_user_id_by_calendar_email",
                    return_value=200,
                ):
                    with patch(
                        "assistant.integrations.google_calendar_oauth.user_token_path"
                    ) as tp:
                        tp.return_value.is_file.return_value = True
                        out = iter_attendees_for_calendar_busy_check(
                            owner_id, parsed
                        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], 200)

    def test_skips_owner_as_attendee(self) -> None:
        parsed = {"attendee_names": ["@me"], "attendees": []}
        with patch(
            "assistant.lib.calendar_attendees.telegram_registry.lookup_user_id",
            return_value=100,
        ):
            with patch(
                "assistant.integrations.google_calendar_oauth.user_token_path"
            ) as tp:
                tp.return_value.is_file.return_value = True
                out = iter_attendees_for_calendar_busy_check(100, parsed)
        self.assertEqual(out, [])


    def test_not_checked_empty_when_short_name_matches_checked(self) -> None:
        parsed = {"attendee_names": ["Диана", "Диана Беспамятная"]}
        checked = [(200, "Диана Беспамятная")]
        contact = {
            "name": "Диана",
            "email": "diana@test.com",
            "telegram_user_id": 200,
        }
        with patch(
            "assistant.lib.calendar_attendees.find_contact_by_name",
            return_value=contact,
        ):
            with patch(
                "assistant.lib.calendar_attendees.attendee_calendar_user_id",
                return_value=200,
            ):
                with patch(
                    "assistant.lib.calendar_user_lookup.lookup_user_id_by_calendar_email",
                    return_value=None,
                ):
                    missing = attendee_names_missing_calendar_link(
                        100, parsed, checked
                    )
        self.assertEqual(missing, [])

    def test_vova_not_listed_when_pshenovich_checked(self) -> None:
        parsed = {"attendee_names": ["@pshenovich", "Вова"]}
        checked = [(106278723, "Владимир")]
        contact = {
            "name": "knazorlov",
            "email": "knazorlov@gmail.com",
            "aliases": ["vova"],
            "telegram_username": "pshenovich",
        }
        with patch(
            "assistant.lib.calendar_attendees.find_contact_by_name",
            return_value=contact,
        ):
            with patch(
                "assistant.lib.calendar_attendees.attendee_calendar_user_id",
                return_value=106278723,
            ):
                with patch(
                    "assistant.lib.calendar_user_lookup.lookup_user_id_by_calendar_email",
                    return_value=106278723,
                ):
                    missing = attendee_names_missing_calendar_link(
                        871463833, parsed, checked, telegram_username="artyawn"
                    )
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
