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

    def test_picked_day_shows_later_meetings_in_order(self) -> None:
        import usage_server

        def ev(i: str, start: str, end: str) -> dict:
            return {"id": i, "start": {"dateTime": start}, "end": {"dateTime": end}, "entry_type": "event"}

        at = datetime(2026, 10, 16, 9, 10, tzinfo=timezone.utc)
        res = usage_server._rank_meeting_candidates(
            [
                ev("late", "2026-10-20T15:00:00+00:00", "2026-10-20T16:00:00+00:00"),
                ev("early", "2026-10-20T08:00:00+00:00", "2026-10-20T08:30:00+00:00"),
            ],
            at,
            any_time=True,
        )
        self.assertEqual([e["id"] for e in res["others"]], ["early", "late"])

    def test_recorded_at_parsing(self) -> None:
        import usage_server
        from datetime import timedelta

        now = datetime.now(timezone.utc)
        ms = str(int((now - timedelta(days=5)).timestamp() * 1000))
        got = usage_server._parse_recorded_at(ms)
        self.assertIsNotNone(got)
        self.assertLess(abs((got - (now - timedelta(days=5))).total_seconds()), 2)
        self.assertIsNone(usage_server._parse_recorded_at(str(int((now + timedelta(days=1)).timestamp() * 1000))))
        self.assertIsNone(usage_server._parse_recorded_at("0"))
        self.assertIsNone(usage_server._parse_recorded_at("мусор"))
        note = {"created_at": now.isoformat(), "meta": {"recorded_at": (now - timedelta(days=4)).isoformat()}}
        self.assertLess(usage_server._note_recorded_at(note), now - timedelta(days=3))
        self.assertGreater(usage_server._note_recorded_at({"created_at": now.isoformat(), "meta": {}}), now - timedelta(minutes=1))


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


class AssistantToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _tr_note(self) -> dict:
        return notes_store.create_note(
            1, "1:1 с Андреем", "<p>Обсудили тренды и найм.</p>", role="transcription",
            meta={"main_topic": "Тренды", "tasks": [{"assignee": "Андрей", "task": "Релиз трендов", "deadline": "пт"}],
                  "decisions": ["Анонимизатор — мини-модель"]},
            primary_sheet_title="Саммари",
        )

    def test_meeting_materials(self) -> None:
        import json as _json

        from assistant.services import gpt_calendar_tools as g

        n = self._tr_note()
        meeting_links.link(1, n["id"], EV)
        ctx = g.GptCalendarContext(uid=1)
        out = _json.loads(g.tool_meeting_materials(ctx, {"event_id": EV["id"], "calendar_id": "primary"}))
        self.assertEqual(out["meeting"]["title"], "1:1 с Андреем")
        self.assertIn("Андрей", out["meeting"]["people"])
        mat = out["materials"][0]
        self.assertEqual(mat["kind"], "transcription")
        self.assertEqual(mat["tasks"][0]["task"], "Релиз трендов")
        self.assertEqual(mat["decisions"], ["Анонимизатор — мини-модель"])
        self.assertIn("тренды", mat["text"])
        empty = _json.loads(g.tool_meeting_materials(ctx, {"event_id": "nope"}))
        self.assertEqual(empty["materials"], [])

    def test_person_history(self) -> None:
        import json as _json

        from assistant.services import gpt_calendar_tools as g

        n = self._tr_note()
        meeting_links.link(1, n["id"], EV)
        other = notes_store.create_note(1, "Созвон в кафе", "<p>Андрей обещал план найма.</p>")
        meeting_links.add_person(1, other["id"], name="Андрей", email="andrey@x.ru")
        start = datetime.now(timezone.utc) - timedelta(days=2)
        evs = [
            {"event_id": EV["id"], "calendar_id": "primary", "summary": "1:1 с Андреем", "start": start, "end": start,
             "raw": {"attendees": [{"email": "andrey@x.ru"}]}},
            {"event_id": "chu", "calendar_id": "primary", "summary": "Без Андрея", "start": start, "end": start,
             "raw": {"attendees": [{"email": "other@x.ru"}]}},
        ]
        ctx = g.GptCalendarContext(uid=1)
        with mock.patch.object(g, "has_google_calendar", return_value=True), mock.patch.object(
            g, "_events_in_window", return_value=evs
        ), mock.patch.object(g, "_contacts", return_value=[{"name": "Андрей", "email": "andrey@x.ru"}]), mock.patch.object(
            g, "_resolve_people", return_value=(["andrey@x.ru"], [])
        ):
            out = _json.loads(g.tool_person_history(ctx, {"person": "Андрей"}))
        self.assertEqual([m["title"] for m in out["meetings"]], ["1:1 с Андреем"])
        self.assertEqual(out["meetings"][0]["materials"][0]["decisions"], ["Анонимизатор — мини-модель"])
        self.assertEqual([m["title"] for m in out["other_materials"]], ["Созвон в кафе"])


if __name__ == "__main__":
    unittest.main()


class SharedMeetingResultsTests(unittest.TestCase):
    """Общая встреча: запись загрузил владелец, участнику документ открыт — у участника итоги тоже есть."""

    def setUp(self) -> None:
        from assistant.stores import note_members

        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]
        note_members._CONN = None  # type: ignore[attr-defined]
        # Владелец 1 загрузил запись «Фермы» и привязал её к своей встрече; участник 2 получил доступ.
        self.start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=2)
        self.owner_ev = {
            "id": "orb-7",
            "calendar_id": "orbita",
            "summary": "Ферма",
            "start": {"dateTime": self.start.isoformat()},
            "end": {"dateTime": (self.start + timedelta(minutes=45)).isoformat()},
            "attendees": [{"email": "andrey@x.ru", "name": "Андрей"}],
        }
        t = notes_store.create_note(1, "Ферма", "<p>саммари</p>", role="transcription")
        meeting_links.link(1, t["id"], self.owner_ev)
        note_members.add_member(1, t["id"], 2)

    def tearDown(self) -> None:
        from assistant.stores import note_members

        notes_store._CONN = None  # type: ignore[attr-defined]
        meeting_links._CONN = None  # type: ignore[attr-defined]
        note_members._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _member_ev(self, **kw) -> dict:
        return dict(
            self.owner_ev,
            calendar_id="andrey@x.ru",
            attendees=[{"email": "vova@x.ru", "name": "Вова"}],
            **kw,
        )

    def test_shared_kinds_match_by_id_or_time_and_title(self) -> None:
        same_id = self._member_ev()
        google_copy = self._member_ev(id="g123")  # у владельца встреча Орбиты, у участника — Google
        other = self._member_ev(id="g999", summary="Другая")
        got = meeting_links.shared_kinds_for_events(2, [same_id, google_copy, other])
        self.assertEqual(got.get("andrey@x.ru:orb-7"), {"transcription": 1, "note": 0})
        self.assertEqual(got.get("andrey@x.ru:g123"), {"transcription": 1, "note": 0})
        self.assertNotIn("andrey@x.ru:g999", got)
        # Без доступа к документу — ничего; у самого владельца свои привязки идут через kinds_for_events.
        self.assertEqual(meeting_links.shared_kinds_for_events(3, [same_id]), {})
        self.assertEqual(meeting_links.shared_kinds_for_events(1, [same_id]), {})
        # Обычные значки участника (только свои привязки) по-прежнему пусты — отсюда и была ошибка.
        self.assertEqual(meeting_links.kinds_for_events(2, ["andrey@x.ru:orb-7"]), {})

    def test_kinds_and_materials_with_shared(self) -> None:
        # Значки календаря и материалы встречи у участника: его копия события с другим id.
        ev = self._member_ev(id="g123")
        own = notes_store.create_note(2, "Мои заметки", "<p>z</p>")
        meeting_links.link(2, own["id"], ev)
        self.assertEqual(meeting_links.kinds_with_shared(2, [ev]), {"andrey@x.ru:g123": {"transcription": 1, "note": 1}})
        rows = meeting_links.notes_for_event_with_shared(2, ev)
        self.assertEqual([(r["owner_user_id"], r["note_id"]) for r in rows][0], ("2", own["id"]))
        self.assertEqual(rows[1]["owner_user_id"], "1")  # общий документ — привязка владельца
        self.assertEqual(notes_store.get_accessible_note(2, rows[1]["note_id"], with_sharing=False)["title"], "Ферма")
        # Инструменты Лео: событие с event_id и start как datetime.
        leo_ev = {"event_id": "g123", "calendar_id": "andrey@x.ru", "summary": "Ферма", "start": self.start}
        self.assertIn("andrey@x.ru:g123", meeting_links.kinds_with_shared(2, [leo_ev]))
        # По одному id без темы и времени чужую встречу не угадываем.
        self.assertEqual(len(meeting_links.notes_for_event_with_shared(2, {"id": "g123", "calendar_id": "x"})), 0)
        self.assertEqual(len(meeting_links.notes_for_event_with_shared(2, {"id": "orb-7", "calendar_id": "x"})), 1)
        self.assertEqual(meeting_links.notes_for_event_with_shared(3, ev), [])

    def test_member_gets_no_no_results_hint(self) -> None:
        from assistant.services import proactive

        evs = [self._member_ev(id="g123")]
        with mock.patch("assistant.stores.user_prefs.proactive_prefs", return_value={"after_meetings": True}), \
                mock.patch("assistant.services.calendar._tz_for", return_value=timezone.utc):
            member = proactive.build_feed(2, evs)
            stranger = proactive.build_feed(3, evs)
        self.assertFalse([i for i in member if i.get("type") == "no_results"])
        # Тот, кому документ не открыт, подсказку по-прежнему получает.
        self.assertTrue([i for i in stranger if i.get("type") == "no_results"])
