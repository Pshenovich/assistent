from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from assistant.stores import calendar_tasks as calendar_tasks_store


class TestCalendarRangeHelpers(TestCase):
    def test_parse_calendar_day(self) -> None:
        from usage_server import _parse_calendar_day

        fallback = date(2026, 1, 1)
        self.assertEqual(_parse_calendar_day("2026-10-03", fallback), date(2026, 10, 3))
        self.assertEqual(_parse_calendar_day("bad", fallback), fallback)
        self.assertEqual(_parse_calendar_day(None, fallback), fallback)

    def _isolated_tasks_db(self):
        td = tempfile.TemporaryDirectory()
        os.environ["CALENDAR_TASKS_DB_PATH"] = os.path.join(td.name, "t.sqlite")
        calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
        return td

    def test_range_swaps_and_caps_dates(self) -> None:
        from usage_server import _calendar_range_payload

        isolated = self._isolated_tasks_db()
        try:
            with (
                patch("assistant.integrations.google_calendar_oauth.user_token_path") as token_path,
                patch(
                    "assistant.services.calendar._tz_for",
                    return_value=ZoneInfo("Europe/Moscow"),
                ),
                patch("usage_server._miniapp_dev_mode_on", return_value=False),
                patch("assistant.services.google_tasks.pull_into_leo_brief", return_value=[]),
            ):
                token_path.return_value.is_file.return_value = False
                payload = _calendar_range_payload(1, "2026-12-31", "2026-01-01")
                self.assertEqual(payload["from"], "2026-01-01")
                self.assertEqual(payload["to"], "2026-03-04")
                self.assertFalse(payload["connected"])
                self.assertEqual(payload["events"], [])
        finally:
            calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
            os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
            isolated.cleanup()

    def test_range_and_today_merge_leo_tasks(self) -> None:
        from usage_server import _calendar_range_payload, _calendar_today_payload

        isolated = self._isolated_tasks_db()
        tz = ZoneInfo("Europe/Moscow")
        try:
            row = calendar_tasks_store.create_task(
                1,
                title="Зелёная",
                start_at=datetime(2026, 10, 7, 15, 0, tzinfo=tz),
                tz=tz,
            )
            with (
                patch("assistant.integrations.google_calendar_oauth.user_token_path") as token_path,
                patch("assistant.services.calendar._tz_for", return_value=tz),
                patch("usage_server._miniapp_dev_mode_on", return_value=False),
                patch("assistant.services.google_tasks.pull_into_leo_brief", return_value=[]),
            ):
                token_path.return_value.is_file.return_value = False
                ranged = _calendar_range_payload(1, "2026-10-07", "2026-10-07")
                today = _calendar_today_payload(1, "2026-10-07")
            task_events = [e for e in ranged["events"] if e.get("entry_type") == "task"]
            self.assertEqual(len(task_events), 1)
            self.assertEqual(task_events[0]["summary"], "Зелёная")
            self.assertEqual(task_events[0]["task_id"], row["id"])
            self.assertEqual(task_events[0]["kind"], "Задача")
            today_tasks = [e for e in today["events"] if e.get("entry_type") == "task"]
            self.assertEqual(len(today_tasks), 1)
            self.assertEqual(today_tasks[0]["summary"], "Зелёная")
        finally:
            calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
            os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
            isolated.cleanup()

    def test_today_does_not_wait_for_google_tasks_pull(self) -> None:
        import time

        from usage_server import _calendar_today_payload

        isolated = self._isolated_tasks_db()
        tz = ZoneInfo("Europe/Moscow")
        from assistant.services import google_tasks as google_tasks_svc

        google_tasks_svc._reset_pull_state_for_tests()
        hung = {"n": 0}

        def _hang(*_a, **_k):
            hung["n"] += 1
            time.sleep(20)

        try:
            calendar_tasks_store.create_task(
                9011,
                title="Локальная",
                start_at=datetime(2026, 10, 7, 11, 0, tzinfo=tz),
                tz=tz,
            )
            with (
                patch("assistant.integrations.google_calendar_oauth.user_token_path") as token_path,
                patch("assistant.services.calendar._tz_for", return_value=tz),
                patch("usage_server._miniapp_dev_mode_on", return_value=False),
                patch("assistant.services.google_tasks.pull_into_leo_brief", return_value=[]),
                patch("assistant.services.google_tasks.pull_into_leo", _hang),
            ):
                token_path.return_value.is_file.return_value = False
                t0 = time.monotonic()
                today = _calendar_today_payload(9011, "2026-10-07")
                elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0)
            titles = [e.get("summary") for e in today["events"] if e.get("entry_type") == "task"]
            self.assertEqual(titles, ["Локальная"])
            self.assertEqual(hung["n"], 0)
        finally:
            calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
            os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
            isolated.cleanup()

    def test_attach_links_gcal_event_to_google_task_by_title(self) -> None:
        from usage_server import _attach_leo_tasks

        isolated = self._isolated_tasks_db()
        tz = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 10, 7, 0, 0, tzinfo=tz)
        end = datetime(2026, 10, 8, 0, 0, tzinfo=tz)
        row = calendar_tasks_store.create_task(
            1,
            title="Купить молоко",
            start_at=datetime(2026, 10, 7, 9, 0, tzinfo=tz),
            tz=tz,
        )
        calendar_tasks_store.update_task(1, row["id"], google_task_id="gt-1")
        ev = {
            "id": "gcal-meet-1",
            "summary": "Купить молоко",
            "kind": "Встреча",
            "entry_type": "event",
            "start": {"dateTime": "2026-10-07T15:00:00+03:00"},
            "end": {"dateTime": "2026-10-07T15:30:00+03:00"},
            "start_day": "2026-10-07",
            "calendar_id": "primary",
        }
        try:
            with patch("assistant.services.google_tasks.pull_into_leo_brief", return_value=[]):
                out = _attach_leo_tasks({"events": [ev]}, 1, start, end, tz)
            ids = [e.get("id") for e in out["events"]]
            self.assertNotIn("gcal-meet-1", ids)
            tasks = [e for e in out["events"] if e.get("entry_type") == "task"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["summary"], "Купить молоко")
            linked = calendar_tasks_store.get_task(1, row["id"])
            self.assertEqual(linked["google_event_id"], "gcal-meet-1")
        finally:
            calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
            os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
            isolated.cleanup()

    def test_attach_imports_solo_gcal_event_as_task(self) -> None:
        from usage_server import _attach_leo_tasks

        isolated = self._isolated_tasks_db()
        tz = ZoneInfo("Europe/Moscow")
        start = datetime(2026, 10, 7, 0, 0, tzinfo=tz)
        end = datetime(2026, 10, 8, 0, 0, tzinfo=tz)
        ev = {
            "id": "gcal-solo-1",
            "summary": "Купить молоко",
            "kind": "Задача",
            "entry_type": "task",
            "start": {"dateTime": "2026-10-07T15:00:00+03:00"},
            "end": {"dateTime": "2026-10-07T15:30:00+03:00"},
            "calendar_id": "primary",
        }
        try:
            with patch("assistant.services.google_tasks.pull_into_leo_brief", return_value=[]):
                out = _attach_leo_tasks({"events": [ev]}, 1, start, end, tz)
            ids = [e.get("id") for e in out["events"]]
            self.assertNotIn("gcal-solo-1", ids)
            tasks = [e for e in out["events"] if e.get("entry_type") == "task"]
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["summary"], "Купить молоко")
            self.assertTrue(str(tasks[0]["id"]).startswith("task-"))
        finally:
            calendar_tasks_store._CONN = None  # type: ignore[attr-defined]
            os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
            isolated.cleanup()
