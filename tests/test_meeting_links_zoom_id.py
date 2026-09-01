"""Extract Zoom meeting id from join URLs."""

import unittest

from assistant.lib.meeting_links import meeting_id_from_zoom_url


class TestMeetingIdFromZoomUrl(unittest.TestCase):
    def test_standard_join(self) -> None:
        self.assertEqual(
            meeting_id_from_zoom_url(
                "https://pooja-onelogin-test.zoom.us/j/93433084161?pwd=abc"
            ),
            "93433084161",
        )

    def test_wc_join(self) -> None:
        self.assertEqual(
            meeting_id_from_zoom_url("https://zoom.us/wc/join/12345678901"),
            "12345678901",
        )

    def test_non_zoom(self) -> None:
        self.assertEqual(meeting_id_from_zoom_url("https://meet.google.com/abc"), "")


if __name__ == "__main__":
    unittest.main()
