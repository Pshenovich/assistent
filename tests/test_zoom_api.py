"""Тесты payload Zoom REST (без сети)."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from assistant.integrations import zoom_api


class TestZoomApiPayload(unittest.TestCase):
    @patch("assistant.integrations.zoom_api.requests.post")
    def test_create_scheduled_meeting_payload(self, mock_post: MagicMock) -> None:
        mock_post.return_value = MagicMock(
            ok=True,
            json=lambda: {"id": "1", "join_url": "https://zoom.us/j/1"},
        )
        start = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 3, 13, 0, tzinfo=timezone.utc)
        zoom_api.create_scheduled_meeting(
            "token",
            topic="Тест",
            start_utc=start,
            end_utc=end,
            timezone_str="Europe/Moscow",
        )
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1].get("json")
        self.assertEqual(payload["type"], 2)
        self.assertEqual(payload["topic"], "Тест")
        self.assertEqual(payload["duration"], 60)
        self.assertIn("start_time", payload)


if __name__ == "__main__":
    unittest.main()
