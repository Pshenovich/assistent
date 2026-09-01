"""primary calendar id и проверка занятости участников."""

import unittest
from unittest.mock import patch

from assistant.services import calendar_sources as cs


class TestResolvePrimaryCalendar(unittest.TestCase):
    def test_get_role_for_primary_alias(self) -> None:
        with patch.object(cs, "list_readable_calendars") as lst:
            lst.return_value = [
                {
                    "id": "user@gmail.com",
                    "summary": "Main",
                    "primary": True,
                    "accessRole": "owner",
                },
            ]
            self.assertEqual(cs.get_calendar_access_role(1, "primary"), "owner")
            self.assertEqual(cs.resolve_calendar_id(1, "primary"), "user@gmail.com")

    def test_assert_writable_primary(self) -> None:
        with patch.object(cs, "list_readable_calendars") as lst:
            lst.return_value = [
                {
                    "id": "user@gmail.com",
                    "primary": True,
                    "accessRole": "owner",
                },
            ]
            cs.assert_calendar_writable(1, "primary")


if __name__ == "__main__":
    unittest.main()
