import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import usage_server as us

TZ = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 20, 14, 30, tzinfo=TZ)  # вторник


class _FixedDT(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW


class VoiceCommandTests(unittest.TestCase):
    def _run(self, parsed):
        calls = {}

        async def rem(body, principal):
            calls["reminder"] = body
            return {"item": {"id": "r1"}}

        async def task(body, principal):
            calls["task"] = body
            return {"task": {"id": 1}}

        async def ev(body, principal):
            calls["event"] = body
            return {"event": {"id": "e1"}}

        principal = SimpleNamespace(telegram_user_id=11, user={})
        with mock.patch.object(us, "datetime", _FixedDT), \
            mock.patch("assistant.services.calendar._tz_for", return_value=TZ), \
            mock.patch("assistant.nlu.llm.parse_voice_command", return_value=parsed), \
            mock.patch.object(us, "miniapp_reminders_create", rem), \
            mock.patch.object(us, "miniapp_calendar_tasks_create", task), \
            mock.patch.object(us, "miniapp_calendar_event_create", ev):
            out = asyncio.run(
                us.miniapp_voice_command(us._MiniappVoiceCommandBody(text="фраза"), principal)
            )
        return out, calls

    def test_question(self):
        out, calls = self._run({"kind": "question"})
        self.assertEqual(out, {"kind": "question", "text": "фраза"})
        self.assertEqual(calls, {})

    def test_reminder_time_passed_today_goes_tomorrow(self):
        out, calls = self._run({"kind": "reminder", "title": "Позвонить маме", "time": "10:00"})
        self.assertEqual(out["kind"], "reminder")
        self.assertEqual(out["when_label"], "завтра в 10:00")
        self.assertTrue(calls["reminder"].when_iso.startswith("2026-10-21T10:00"))

    def test_reminder_without_time_in_hour(self):
        out, _ = self._run({"kind": "reminder", "title": "Вода"})
        self.assertEqual(out["when_label"], "сегодня в 15:30")

    def test_task_all_day(self):
        out, calls = self._run({"kind": "task", "title": "Отчёт", "date": "2026-10-23"})
        self.assertEqual(out["when_label"], "23 октября")
        self.assertTrue(calls["task"].all_day)

    def test_meeting_needs_time(self):
        out, calls = self._run({"kind": "meeting", "title": "Созвон", "date": "2026-10-21"})
        self.assertEqual(out["kind"], "question")
        self.assertEqual(calls, {})

    def test_meeting_duration(self):
        out, calls = self._run(
            {"kind": "meeting", "title": "Созвон", "date": "2026-10-21", "time": "15:00", "duration_min": 30}
        )
        self.assertEqual(out["when_label"], "завтра в 15:00")
        self.assertTrue(calls["event"].end.startswith("2026-10-21T15:30"))


if __name__ == "__main__":
    unittest.main()
