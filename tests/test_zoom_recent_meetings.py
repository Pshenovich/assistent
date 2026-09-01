"""Тесты хранилища недавних Zoom-встреч."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import assistant.stores.zoom_recent_meetings as zrm


class TestZoomRecentMeetings(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        patcher = patch.object(zrm, "ROOT", self.root)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_register_and_list_today(self) -> None:
        zrm.register_meeting(
            42,
            {"id": "999", "topic": "Reviewer test", "type": 1, "join_url": "https://zoom.us/j/1"},
        )
        tz = ZoneInfo("UTC")
        hits = zrm.list_recent(42, "", None, tz=tz)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "999")

    def test_remove_meeting(self) -> None:
        zrm.register_meeting(42, {"id": "111", "topic": "A", "type": 1})
        zrm.remove_meeting(42, "111")
        self.assertEqual(zrm.list_recent(42, "", None, tz=ZoneInfo("UTC")), [])


    def test_list_prefers_newest(self) -> None:
        zrm.register_meeting(42, {"id": "1", "topic": "Old", "type": 1})
        zrm.register_meeting(42, {"id": "2", "topic": "New", "type": 2, "start_time": "2026-07-21T17:00:00Z"})
        hits = zrm.list_recent(42, "", None, tz=ZoneInfo("UTC"))
        self.assertEqual(hits[0]["id"], "2")


if __name__ == "__main__":
    unittest.main()
