"""Тесты связки Zoom ↔ Google Calendar (без API)."""

import unittest

from assistant.config import ZOOM_MEETING_PROP_KEY
from assistant.lib import zoom_gcal


class TestZoomGcalHelpers(unittest.TestCase):
    def test_read_zoom_meeting_id(self) -> None:
        ev = {
            "extendedProperties": {
                "private": {ZOOM_MEETING_PROP_KEY: "12345"},
            }
        }
        self.assertEqual(zoom_gcal.read_zoom_meeting_id(ev), "12345")

    def test_upsert_zoom_description(self) -> None:
        desc = zoom_gcal._upsert_zoom_description("Тема", "https://zoom.us/j/1")
        self.assertIn("Zoom:", desc)
        self.assertIn("https://zoom.us/j/1", desc)
        desc2 = zoom_gcal._upsert_zoom_description(desc, "https://zoom.us/j/2")
        self.assertEqual(desc2.count("Zoom:"), 1)
        self.assertIn("https://zoom.us/j/2", desc2)

    def test_auto_attach_disabled_without_token(self) -> None:
        result = {"event_id": "e1", "start": None, "end": None}
        out = zoom_gcal.try_auto_attach_after_create(999999001, result)
        self.assertEqual(out, result)


if __name__ == "__main__":
    unittest.main()
