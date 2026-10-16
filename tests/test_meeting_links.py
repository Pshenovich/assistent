"""Материалы встречи: связь заметок/транскрипций со встречами, участники, подбор встречи."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.stores import meeting_links
from assistant.stores import notes as notes_store

EV = {
    "id": "ev1_20261016T080000Z",
    "calendar_id": "primary",
    "summary": "1:1 с Андреем",
    "start": {"dateTime": "2026-10-16T11:00:00+03:00"},
    "end": {"dateTime": "2026-10-16T11:30:00+03:00"},
    "meet_url": "https://telemost.yandex.ru/j/123",
    "attendees": [{"email": "andrey@x.ru", "name": "Андрей"}, {"email": "ANDREY@x.ru", "name": "дубль"}],
}


class MeetingLinksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_link_snapshot_and_event_lookup(self) -> None:
        n = notes_store.create_note(1, "Итоги", "<p>x</p>")
        link = meeting_links.link(1, n["id"], EV)
        self.assertEqual(link["event_key"], "primary:ev1_20261016T080000Z")
        self.assertEqual(link["start_iso"], "2026-10-16T11:00:00+03:00")
        self.assertEqual([a["email"] for a in link["attendees"]], ["andrey@x.ru"])  # дубль по почте убран
        rows = meeting_links.notes_for_event(1, link["event_key"])
        self.assertEqual([r["note_id"] for r in rows], [n["id"]])
        # Чужой пользователь не видит материалов этой встречи.
        self.assertEqual(meeting_links.notes_for_event(2, link["event_key"]), [])
        self.assertIsNone(meeting_links.get_for_note(2, n["id"]))

    def test_kinds_for_badges_and_relink(self) -> None:
        a = notes_store.create_note(1, "Итоги", "<p>x</p>")
        t = notes_store.create_note(1, "Запись", "<p>y</p>", role="transcription")
        meeting_links.link(1, a["id"], EV)
        meeting_links.link(1, t["id"], EV)
        key = meeting_links.event_key("primary", EV["id"])
        self.assertEqual(meeting_links.kinds_for_events(1, [key]), {key: {"transcription": 1, "note": 1}})
        other = dict(EV, id="ev2")
        meeting_links.link(1, a["id"], other)  # перепривязка, а не вторая связь
        self.assertEqual(meeting_links.kinds_for_events(1, [key])[key], {"transcription": 1, "note": 0})
        self.assertTrue(meeting_links.unlink(1, t["id"]))
        self.assertEqual(meeting_links.kinds_for_events(1, [key]), {})

    def test_deleted_note_drops_out_of_materials(self) -> None:
        n = notes_store.create_note(1, "Итоги", "<p>x</p>")
        link = meeting_links.link(1, n["id"], EV)
        notes_store.delete_note(1, n["id"])
        self.assertEqual(meeting_links.notes_for_event(1, link["event_key"]), [])

    def test_people(self) -> None:
        n = notes_store.create_note(1, "Созвон", "")
        meeting_links.add_person(1, n["id"], name="Ольга")
        people = meeting_links.add_person(1, n["id"], name="Пётр", email="Petr@x.ru")
        self.assertEqual([p["name"] for p in people], ["Ольга", "Пётр"])
        people = meeting_links.remove_person(1, n["id"], "petr@x.ru")
        self.assertEqual([p["name"] for p in people], ["Ольга"])
        with self.assertRaises(ValueError):
            meeting_links.add_person(1, n["id"])


class CandidateRankingTests(unittest.TestCase):
    def test_recording_time_matches_meeting(self) -> None:
        import usage_server

        def ev(i: str, start: str, end: str) -> dict:
            return {"id": i, "start": {"dateTime": start}, "end": {"dateTime": end}, "entry_type": "event"}

        at = datetime(2026, 10, 16, 9, 10, tzinfo=timezone.utc)  # запись создана после встречи 8:00–8:30
        res = usage_server._rank_meeting_candidates(
            [
                ev("later", "2026-10-16T12:00:00+00:00", "2026-10-16T13:00:00+00:00"),
                ev("match", "2026-10-16T08:00:00+00:00", "2026-10-16T08:30:00+00:00"),
                ev("old", "2026-10-15T08:00:00+00:00", "2026-10-15T08:30:00+00:00"),
                {"id": "task-1", "start": {"dateTime": "2026-10-16T08:00:00+00:00"}, "entry_type": "task"},
                {"id": "allday", "start": {"date": "2026-10-16"}},
            ],
            at,
        )
        self.assertEqual([e["id"] for e in res["suggested"]], ["match"])
        self.assertEqual([e["id"] for e in res["others"]], ["old"])


class AutolinkTests(unittest.TestCase):
    def test_matches_by_zoom_id_and_url(self) -> None:
        from assistant.services import meeting_autolink

        raw = {"location": "https://us02web.zoom.us/j/81234567890?pwd=abc", "summary": "Демо"}
        self.assertTrue(meeting_autolink._matches(raw, url="https://us02web.zoom.us/j/81234567890", native_id=""))
        self.assertTrue(meeting_autolink._matches(raw, url="", native_id="81234567890"))
        self.assertFalse(meeting_autolink._matches(raw, url="https://zoom.us/j/1", native_id="999999999"))

    def test_picks_closest_matching_event(self) -> None:
        from assistant.services import meeting_autolink

        at = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)
        url = "https://telemost.yandex.ru/j/555"

        def norm(i: str, start: datetime, link: str) -> dict:
            return {
                "event_id": i,
                "calendar_id": "primary",
                "start": start,
                "raw": {"id": i, "summary": i, "location": link, "start": {"dateTime": start.isoformat()}, "end": {}},
            }

        evs = [
            norm("other-link", at, "https://telemost.yandex.ru/j/777"),
            norm("far", at - timedelta(hours=10), url),
            norm("near", at + timedelta(minutes=5), url),
        ]
        with mock.patch("assistant.integrations.google_calendar_oauth.user_token_path") as tp, mock.patch(
            "assistant.services.calendar_sources.list_events_in_window", return_value=evs
        ):
            tp.return_value.is_file.return_value = True
            found = meeting_autolink.find_event_for_recording(1, meeting_url=url, start_at=at)
        self.assertEqual(found["id"], "near")
        self.assertEqual(found["summary"], "near")


if __name__ == "__main__":
    unittest.main()
