"""«Фокус» (этап 1): правила подсказок и скрытие."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from assistant.services import proactive
from assistant.stores import notes as notes_store

NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
TZ = timezone.utc


def ev(i: str, start: datetime, mins: int = 30, people=("Андрей",), **kw) -> dict:
    return {
        "id": i,
        "calendar_id": "primary",
        "summary": kw.get("summary", "Встреча " + i),
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=mins)).isoformat()},
        "attendees": [{"email": f"{p}@x.ru", "name": p} for p in people],
        "self_response_status": kw.get("status", "accepted"),
    }


class RulesTests(unittest.TestCase):
    def test_overdue_single_and_many(self) -> None:
        t1 = {"id": 1, "title": "Смета", "start_at": (NOW - timedelta(days=1)).isoformat(), "end_at": (NOW - timedelta(days=1)).isoformat(), "done": False}
        t2 = {"id": 2, "title": "Доступы", "start_at": (NOW - timedelta(days=2)).isoformat(), "end_at": (NOW - timedelta(days=2)).isoformat(),
              "done": False, "assignee_user_id": "7", "assignee_name": "Давид"}
        done = dict(t1, id=3, done=True)
        future = dict(t1, id=4, end_at=(NOW + timedelta(hours=2)).isoformat())
        one = proactive.rule_overdue([t1, done, future], NOW, TZ, "1")
        self.assertEqual(one[0]["title"], "Просрочено: «Смета»")
        self.assertEqual(one[0]["actions"][0]["payload"]["task_ids"], [1])
        many = proactive.rule_overdue([t1, t2], NOW, TZ, "1")
        self.assertEqual(many[0]["title"], "2 задачи просрочены")
        self.assertIn("Давид", many[0]["source"])

    def test_prep_only_soon_with_people(self) -> None:
        events = [
            ev("soon", NOW + timedelta(minutes=40)),
            ev("later", NOW + timedelta(hours=3)),
            ev("solo", NOW + timedelta(minutes=30), people=()),
            ev("declined", NOW + timedelta(minutes=20), status="declined"),
        ]
        out = proactive.rule_prep(events, {}, NOW, TZ)
        self.assertEqual([o["event"]["id"] for o in out], ["soon"])
        self.assertTrue(out[0]["title"].startswith("Через 40 мин"))
        self.assertEqual(out[0]["actions"][0]["act"], "event_note")
        with_note = proactive.rule_prep(events, {"primary:soon": {"note": 1, "transcription": 0}}, NOW, TZ)
        self.assertEqual(with_note[0]["actions"][0]["act"], "open_event")

    def test_no_results(self) -> None:
        events = [
            ev("done", NOW - timedelta(hours=3)),
            ev("has", NOW - timedelta(hours=2)),
            ev("short", NOW - timedelta(hours=1), mins=10),
            ev("old", NOW - timedelta(days=3)),
            ev("now", NOW - timedelta(minutes=10)),
        ]
        out = proactive.rule_no_results(events, {"primary:has": {"note": 1, "transcription": 0}}, NOW, TZ)
        self.assertEqual([o["event"]["id"] for o in out], ["done"])

    def test_summary_tasks(self) -> None:
        n = {"id": 5, "title": "Планёрка", "created_at": (NOW - timedelta(days=1)).isoformat(),
             "meta": {"tasks": [{"assignee": "Андрей", "task": "Релиз", "deadline": "пт"}]}}
        old = dict(n, id=6, created_at=(NOW - timedelta(days=30)).isoformat())
        out = proactive.rule_summary_tasks([n, old], {5: False, 6: False}, NOW, TZ)
        self.assertEqual(len(out), 1)
        self.assertIn("Андрей — Релиз — пт", out[0]["actions"][0]["payload"]["prompt"])
        self.assertEqual(proactive.rule_summary_tasks([n], {5: True}, NOW, TZ), [])
        self.assertEqual(out[0]["actions"][-1]["act"], "mark_done")

    def test_summary_tasks_partly_done(self) -> None:
        tasks = [{"assignee": "Андрей", "task": f"Задача {i}"} for i in range(9)]
        n = {"id": 5, "title": "Планёрка", "created_at": (NOW - timedelta(days=1)).isoformat(), "meta": {"tasks": tasks}}
        full = proactive.rule_summary_tasks([n], {}, NOW, TZ)[0]
        self.assertEqual(len(full["items"]), 9)
        self.assertIn("9 задач", full["title"])
        self.assertEqual(full["actions"][-1]["act"], "mark_done_pick")
        done = {i["id"] for i in full["items"][:3]}
        part = proactive.rule_summary_tasks([n], {}, NOW, TZ, done)[0]
        self.assertEqual(part["key"], full["key"])
        self.assertIn("6 задач", part["title"])
        prompt = part["actions"][0]["payload"]["prompt"]
        self.assertNotIn("Задача 0", prompt)
        self.assertIn("Задача 8", prompt)
        every = {i["id"] for i in full["items"]}
        self.assertEqual(proactive.rule_summary_tasks([n], {}, NOW, TZ, every), [])

    def test_rank_limits_and_order(self) -> None:
        items = [{"key": f"k{i}", "urgency": i % 4} for i in range(12)]
        out = proactive.rank(items, {"k3"})
        self.assertEqual(len(out), proactive.MAX_ITEMS)
        self.assertNotIn("k3", [o["key"] for o in out])
        self.assertEqual(out[0]["urgency"], 3)


class DismissTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        proactive._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        notes_store._CONN = None  # type: ignore[attr-defined]
        proactive._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_dismiss_and_snooze(self) -> None:
        proactive.dismiss(1, "a")
        proactive.dismiss(1, "b", until=datetime.now(timezone.utc) - timedelta(minutes=1))  # истёк
        proactive.dismiss(1, "c", until=datetime.now(timezone.utc) + timedelta(hours=1))
        self.assertEqual(proactive.dismissed_keys(1), {"a", "c"})
        self.assertEqual(proactive.dismissed_keys(2), set())

    def test_mark_done(self) -> None:
        items = [{"id": "sumtask:5:a", "text": "Андрей — Релиз"}, {"id": "sumtask:5:b", "text": "Мария — Смета"}]
        proactive.mark_done(1, "sumtasks:5", items[:1])
        self.assertEqual(proactive.done_keys(1), {"sumtask:5:a"})
        self.assertEqual(proactive.done_titles(1), ["Андрей — Релиз"])
        self.assertEqual(proactive.dismissed_keys(1), set())  # частично — карточка остаётся
        proactive.mark_done(1, "sumtasks:5", items, whole=True)
        self.assertEqual(proactive.done_keys(1), {"sumtask:5:a", "sumtask:5:b"})
        self.assertEqual(proactive.dismissed_keys(1), {"sumtasks:5"})
        self.assertEqual(proactive.done_keys(2), set())


if __name__ == "__main__":
    unittest.main()


class DigestPeopleTests(unittest.TestCase):
    def test_people_matched_to_contacts(self) -> None:
        from unittest import mock

        import usage_server

        contacts = [
            {"name": "Мария Иванова", "email": "maria@x.ru", "telegram_user_id": 22},
            {"name": "Давид", "email": "", "telegram_username": "david_k"},
        ]
        people = [
            {"telegram_user_id": 1, "username": "me", "name": "Я"},
            {"telegram_user_id": 22, "username": "mary", "name": "Маша"},
            {"telegram_user_id": 33, "username": "David_K", "name": "D"},
            {"telegram_user_id": 44, "username": "ivan", "name": "Иван К."},
        ]
        with mock.patch("assistant.stores.contacts_store.load_contacts", return_value=contacts), mock.patch.object(
            usage_server, "_miniapp_tg_username", return_value=None
        ):
            out = usage_server._digest_people(1, object(), people)
        self.assertEqual([p["name"] for p in out], ["Мария Иванова", "Давид", "Иван К."])
        self.assertEqual(out[0]["contact"]["email"], "maria@x.ru")
        self.assertEqual(out[1]["contact"]["key"], "tg:david_k")
        self.assertNotIn("contact", out[2])
