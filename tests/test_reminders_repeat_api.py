"""Повтор напоминания из окна мини-приложения: проверка RRULE и правка в хранилище."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from fastapi import HTTPException

from assistant.stores import reminders_store


class ReminderRepeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("REMINDERS_STORE_PATH")
        os.environ["REMINDERS_STORE_PATH"] = os.path.join(self._tmp.name, "reminders.json")
        with open(os.environ["REMINDERS_STORE_PATH"], "w", encoding="utf-8") as f:
            json.dump([], f)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("REMINDERS_STORE_PATH", None)
        else:
            os.environ["REMINDERS_STORE_PATH"] = self._old
        self._tmp.cleanup()

    def test_clean_recurrence(self) -> None:
        import usage_server

        self.assertIsNone(usage_server._clean_reminder_recurrence(None))
        self.assertEqual(usage_server._clean_reminder_recurrence(""), "")
        self.assertEqual(usage_server._clean_reminder_recurrence("FREQ=DAILY"), "RRULE:FREQ=DAILY")
        self.assertEqual(
            usage_server._clean_reminder_recurrence("freq=weekly;byday=mo,tu,we,th,fr"),
            "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
        )
        with self.assertRaises(HTTPException):
            usage_server._clean_reminder_recurrence("FREQ=SECONDLY")
        with self.assertRaises(HTTPException):
            usage_server._clean_reminder_recurrence("FREQ=DAILY\nX-EVIL")

    def test_patch_sets_and_clears_recurrence(self) -> None:
        rid = reminders_store.add_reminder(user_id=1, chat_id=1, task="t", when_iso="2026-10-22T09:00:00+03:00")
        row = reminders_store.patch_reminder(rid, recurrence="FREQ=DAILY")
        self.assertEqual(row["recurrence"], "RRULE:FREQ=DAILY")
        row = reminders_store.patch_reminder(rid, task="t2")
        self.assertEqual(row["recurrence"], "RRULE:FREQ=DAILY")  # None — не трогаем
        row = reminders_store.patch_reminder(rid, recurrence="")
        self.assertNotIn("recurrence", row)


if __name__ == "__main__":
    unittest.main()
