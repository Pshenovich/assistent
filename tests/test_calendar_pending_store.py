"""Персистентное состояние календаря."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from assistant.lib import calendar_pending_store as cps


class TestCalendarPendingStore(unittest.TestCase):
    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                cps.set_pending(
                    42,
                    {
                        "mode": "await_clarify",
                        "clarify_field": "title",
                        "parsed": {"intent": "create_event", "start": "2026-05-22T12:00:00"},
                    },
                )
                st = cps.get_pending(42, ttl_sec=3600)
            self.assertIsNotNone(st)
            assert st is not None
            self.assertEqual(st.get("mode"), "await_clarify")
            self.assertEqual(st.get("clarify_field"), "title")

    def test_find_by_prompt_message(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                cps.set_pending(
                    99,
                    {
                        "mode": "await_clarify",
                        "prompt_message_id": 1001,
                    },
                )
                hit = cps.find_by_prompt_message(99, 1001, ttl_sec=3600)
                miss = cps.find_by_prompt_message(99, 1002, ttl_sec=3600)
            self.assertIsNotNone(hit)
            self.assertIsNone(miss)

    def test_force_create_token_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cal.json"
            with mock.patch.object(cps, "store_path", return_value=path):
                tok = cps.put_force_create_token(7, "2026-06-05T09:30:00")
                iso = cps.pop_force_create_token(tok, user_id=7)
                bad = cps.pop_force_create_token(tok, user_id=7)
            self.assertEqual(iso, "2026-06-05T09:30:00")
            self.assertIsNone(bad)


if __name__ == "__main__":
    unittest.main()
