from __future__ import annotations

from datetime import date
from unittest import TestCase
from unittest.mock import patch


class TestCalendarRangeHelpers(TestCase):
    def test_parse_calendar_day(self) -> None:
        from usage_server import _parse_calendar_day

        fallback = date(2026, 1, 1)
        self.assertEqual(_parse_calendar_day("2026-10-03", fallback), date(2026, 10, 3))
        self.assertEqual(_parse_calendar_day("bad", fallback), fallback)
        self.assertEqual(_parse_calendar_day(None, fallback), fallback)

    def test_range_swaps_and_caps_dates(self) -> None:
        from zoneinfo import ZoneInfo

        from usage_server import _calendar_range_payload

        with (
            patch("assistant.integrations.google_calendar_oauth.user_token_path") as token_path,
            patch("assistant.services.calendar._tz_for", return_value=ZoneInfo("Europe/Moscow")),
            patch("usage_server._miniapp_dev_mode_on", return_value=False),
        ):
            token_path.return_value.is_file.return_value = False
            payload = _calendar_range_payload(1, "2026-12-31", "2026-01-01")
            self.assertEqual(payload["from"], "2026-01-01")
            self.assertEqual(payload["to"], "2026-03-04")
            self.assertFalse(payload["connected"])
            self.assertEqual(payload["events"], [])
