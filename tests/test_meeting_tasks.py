"""Задачи встречи: таблица в саммари, один статус у пункта, «Беру» достаётся первому."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from unittest import mock

from assistant.services import focus_items as fi
from assistant.services import meeting_tasks as mt
from assistant.services import proactive
from assistant.services import proactive_ai as pa
from assistant.stores import calendar_tasks as tasks_store
from assistant.stores import note_members
from assistant.stores import notes as notes_store

TASKS = [
    {"id": "iaaaaaaaaa1", "assignee": "Аня", "task": "Согласовать бюджет", "deadline": "31 окт"},
    {"id": "iaaaaaaaaa2", "assignee": "", "task": "Проверить юр. тексты", "deadline": ""},
    {"id": "iaaaaaaaaa3", "assignee": "Вова", "task": "Посмотреть цены", "deadline": ""},
]


class MeetingTasksTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["CALENDAR_TASKS_DB_PATH"] = os.path.join(self._tmp.name, "calendar_tasks.sqlite")
        self._mods = (notes_store, pa, proactive, fi, tasks_store, note_members)
        for m in self._mods:
            m._CONN = None  # type: ignore[attr-defined]
        tasks = [dict(t) for t in TASKS]
        body = "<h2>📌 Кратко</h2><p>Синк.</p>" + mt.table_html(tasks) + "<h2>❓ Вопросы</h2><ul><li><p>Цены</p></li></ul>"
        self.note = notes_store.create_note(1, "Синк по запуску", body, role="transcription", meta={"tasks": tasks})
        self.nid = int(self.note["id"])
        # фоновое обновление таблицы по событиям задачи — в тестах сразу, без потока
        self._thread = mock.patch.object(tasks_store, "_meeting_task_changed", lambda nid: mt.refresh_note(1, nid))
        self._thread.start()

    def tearDown(self) -> None:
        self._thread.stop()
        os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
        for m in self._mods:
            m._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _new_task(self, owner: int = 1, assignee: int | None = None, title: str = "Согласовать бюджет") -> dict:
        return tasks_store.create_task(
            owner, title=title, start_at="2026-10-31T12:00:00+03:00", all_day=True,
            assignee_user_id=assignee or owner, assignee_name="Аня" if assignee else "", note_id=str(self.nid),
        )

    def _items(self) -> dict:
        state = mt.rows(notes_store.get_note(1, self.nid))
        return state, {x["id"]: x for x in state["items"]}

    def test_section_becomes_table(self) -> None:
        from assistant.services.transcription_notes import _summary_body

        tasks = [{"assignee": "Аня", "task": "Согласовать бюджет", "deadline": "31 окт"},
                 {"assignee": "Вова", "task": "Демо", "deadline": "", "completed": True}]
        md = "## 📌 Кратко\nСинк.\n\n## ✅ Выполненные задачи\n- [x] Вова — Демо\n\n## 📋 Задачи\n- [ ] Аня — Согласовать бюджет — 31 окт\n\n## ❓ Вопросы\n- Цены"
        out = _summary_body(md, {"tasks": tasks})
        self.assertEqual(out.count("<table>"), 1)
        self.assertNotIn("Выполненные задачи", out)
        self.assertIn(f'<td data-leo-item="{tasks[0]["id"]}"><p>Согласовать бюджет</p></td><td><p>Не разобрано</p></td><td><p>Аня</p></td>', out)
        self.assertIn("<p>Сделано</p>", out)
        self.assertLess(out.index("<table>"), out.index("Вопросы"))  # на месте раздела, не в конце

    def test_counter_and_statuses(self) -> None:
        state, items = self._items()
        self.assertEqual((state["decided"], state["total"]), (0, 3))
        self.assertEqual(items["iaaaaaaaaa1"]["label"], "Не разобрано")

    def test_dismiss_excluded_and_moved_down(self) -> None:
        mt.apply(1, self.nid, "iaaaaaaaaa1", "dismiss", by="Вова", reason="not_a_task")
        state, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "not_task")
        self.assertEqual((state["decided"], state["total"]), (0, 2))  # не в счётчике
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertLess(body.index("iaaaaaaaaa2"), body.index("iaaaaaaaaa1"))  # строка ушла вниз таблицы
        self.assertIn("<p>Не задача</p>", body)
        mt.apply(1, self.nid, "iaaaaaaaaa1", "undo", by="Вова")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "open")

    def test_claim_links_task_and_second_claim_conflicts(self) -> None:
        task = mt.claim(1, self.nid, "iaaaaaaaaa1", lambda: self._new_task(), by="Вова")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "taken")
        self.assertEqual(items["iaaaaaaaaa1"]["label"], "Взял Вова")
        self.assertEqual(items["iaaaaaaaaa1"]["task_id"], task["id"])
        self.assertEqual(items["iaaaaaaaaa1"]["due"], "31 окт")
        created = []
        with self.assertRaises(mt.Taken) as ctx:
            mt.claim(1, self.nid, "iaaaaaaaaa1", lambda: created.append(1) or self._new_task(), by="Аня")
        self.assertEqual(str(ctx.exception), "Уже взял Вова")
        self.assertEqual(created, [])  # вторую задачу не создавали

    def test_parallel_claims_create_one_task(self) -> None:
        made = []
        errors = []
        gate = threading.Barrier(2)

        def go(name: str) -> None:
            gate.wait()
            try:
                mt.claim(1, self.nid, "iaaaaaaaaa2", lambda: made.append(name) or self._new_task(title="Юр. тексты"), by=name)
            except mt.Taken:
                errors.append(name)

        threads = [threading.Thread(target=go, args=(n,)) for n in ("Вова", "Аня")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(made), 1)
        self.assertEqual(len(errors), 1)

    def test_delegated_task_status_follows_assignee(self) -> None:
        task = mt.claim(1, self.nid, "iaaaaaaaaa1", lambda: self._new_task(assignee=2), by="Вова")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "assigned")
        self.assertEqual(items["iaaaaaaaaa1"]["who"], "Аня")
        tasks_store.triage_accept(2, task["id"])
        tasks_store.set_assignee_status(2, task["id"], "in_progress")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["label"], "В работе")
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertIn("<p>В работе</p>", body)  # ячейка в тексте обновилась по событию задачи
        tasks_store.triage_decline(2, task["id"], "не моя зона", actor_name="Аня")
        state, items = self._items()
        self.assertEqual(items["iaaaaaaaaa1"]["label"], "Отказ: не моя зона")
        self.assertEqual(state["decided"], 0)  # отказ снова ждёт решения

    def test_focus_done_updates_cells_not_mark(self) -> None:
        fi.remember(1, {
            "key": "ins:1:x", "card_key": "ins:1:x", "kind": "agreement", "text": "Проверить юр. тексты", "who": "",
            "source": {"kind": "note", "id": self.nid, "title": "Синк", "item": "iaaaaaaaaa2"},
        })
        fi.apply(1, "ins:1:x", "done", by="Вова")
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertNotIn("note-focus-mark", body)
        self.assertIn("<p>Сделано</p>", body)

    def test_update_fields(self) -> None:
        mt.apply(1, self.nid, "iaaaaaaaaa2", "update", by="Вова", fields={"assignee": "Дима", "deadline": "5 ноя"})
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertIn('<td data-leo-item="iaaaaaaaaa2"><p>Проверить юр. тексты</p></td><td><p>Не разобрано</p></td><td><p>Дима</p></td><td><p>5 ноя</p></td>', body)


    def test_leo_answers_status_by_approximate_title(self) -> None:
        import json

        from assistant.services.gpt_calendar_tools import GptCalendarContext, tool_meeting_tasks_search

        mt.claim(1, self.nid, "iaaaaaaaaa1", lambda: self._new_task(assignee=2), by="Вова")
        res = json.loads(tool_meeting_tasks_search(GptCalendarContext(uid=1), {"query": "бюджет согласовать"}))
        self.assertEqual(len(res["items"]), 1)
        it = res["items"][0]
        self.assertEqual((it["status"], it["responsible"], it["meeting"]), ("Поручено · ждёт", "Аня", "Синк по запуску"))
        res = json.loads(tool_meeting_tasks_search(GptCalendarContext(uid=1), {"query": "квартальный отчёт"}))
        self.assertEqual(res["items"], [])
        self.assertIn("note", res)


    def _card(self, viewer: str):
        from datetime import datetime, timezone

        note = notes_store.get_note(1, self.nid)
        cards = proactive.rule_summary_tasks([note], {}, datetime.now(timezone.utc), timezone.utc, viewer=viewer)
        return cards[0] if cards else None

    def test_focus_card_only_items_to_decide(self) -> None:
        note_members.upsert_profile(user_id=1, username="v", first_name="Владимир", last_name="П", photo_url=None)
        note_members.upsert_profile(user_id=2, username="a", first_name="Анна", last_name="К", photo_url=None)
        note_members.add_member(1, self.nid, 2)
        anna = self._card("2")
        self.assertEqual([i["item"] for i in anna["items"]], ["iaaaaaaaaa1"])  # «Аня» — это Анна
        self.assertEqual(anna["tag"], "Задачи встречи · 0/3")
        self.assertEqual(anna["source"], "Синк по запуску")  # футер — название заметки
        self.assertEqual(anna["note"]["anchor"], "tasks")
        self.assertTrue(anna["title"].startswith("1 пункт ждёт вашего решения: Согласовать бюджет"))
        owner = self._card("1")
        self.assertEqual([i["item"] for i in owner["items"]], ["iaaaaaaaaa3", "iaaaaaaaaa2"])  # свой (Вова) первым, потом ничей
        # «Не моя» от Анны — пункт у владельца, у Анны карточки больше нет
        mt.not_me(2, self.nid, "iaaaaaaaaa1")
        self.assertIsNone(self._card("2"))
        self.assertIn("iaaaaaaaaa1", [i["item"] for i in self._card("1")["items"]])

    def test_skip_meeting_tasks(self) -> None:
        # «Не разбирать»: задачи ведут в Битриксе — в Фокусе карточки нет, статусы «Не разбираем»
        mt.claim(1, self.nid, "iaaaaaaaaa3", lambda: self._new_task(title="Демо"), by="Вова")
        state = mt.set_skip(1, self.nid, True, by="Вова")
        items = {x["id"]: x for x in state["items"]}
        self.assertEqual(state["skipped"]["by"], "Вова")
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "skipped")
        self.assertEqual(items["iaaaaaaaaa1"]["label"], "Не разбираем")
        self.assertEqual(items["iaaaaaaaaa3"]["code"], "taken")  # разобранное — как было
        self.assertIsNone(self._card("1"))
        self.assertIn("<p>Не разбираем</p>", notes_store.get_note(1, self.nid)["body"])
        # взять один пункт можно и так
        mt.claim(1, self.nid, "iaaaaaaaaa2", lambda: self._new_task(title="Юр. тексты"), by="Вова")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa2"]["code"], "taken")
        mt.set_skip(1, self.nid, False, by="Вова")
        state, items = self._items()
        self.assertNotIn("skipped", state)
        self.assertEqual(items["iaaaaaaaaa1"]["code"], "open")
        self.assertEqual([i["item"] for i in self._card("1")["items"]], ["iaaaaaaaaa1"])

    def test_declined_returns_to_owner(self) -> None:
        note_members.upsert_profile(user_id=2, username="a", first_name="Анна", last_name="К", photo_url=None)
        note_members.add_member(1, self.nid, 2)
        task = mt.claim(2, self.nid, "iaaaaaaaaa2", lambda: self._new_task(owner=1, assignee=2, title="Юр. тексты"), by="Вова")
        self.assertNotIn("iaaaaaaaaa2", [i["item"] for i in self._card("1")["items"]])
        tasks_store.triage_decline(2, task["id"], "нет доступа", actor_name="Анна")
        card = self._card("1")
        item = [i for i in card["items"] if i["item"] == "iaaaaaaaaa2"][0]
        self.assertEqual(item["declined"], "Отказ: нет доступа")
        self.assertIn("отказ", card["title"])


    def test_tracker_claim(self) -> None:
        link = {"url": "https://b24.example/task/7", "title": "Юр. тексты", "provider": "bitrix24", "stage": "Новые"}
        out = mt.claim_tracker(1, self.nid, "iaaaaaaaaa2", lambda: ("ok", link), by="Вова")
        self.assertEqual(out, "ok")
        _, items = self._items()
        self.assertEqual(items["iaaaaaaaaa2"]["code"], "tracker")
        self.assertEqual(items["iaaaaaaaaa2"]["label"], "В трекере · Новые")
        self.assertEqual(items["iaaaaaaaaa2"]["url"], link["url"])
        with self.assertRaises(mt.Taken):
            mt.claim_tracker(1, self.nid, "iaaaaaaaaa2", lambda: ("again", link), by="Аня")


    def test_brief_line_for_stale_meeting(self) -> None:
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        note = notes_store.get_note(1, self.nid)
        card = proactive.rule_summary_tasks([note], {}, now, timezone.utc, viewer="1")[0]
        fresh = proactive.brief_message([card], [], timezone.utc, now)
        self.assertNotIn("🗂", fresh)  # меньше суток — не напоминаем
        old = proactive.brief_message([{**card, "created_at": (now - timedelta(hours=30)).isoformat()}], [], timezone.utc, now)
        self.assertIn("🗂 «Синк по запуску»: 3 пункта ждут вашего решения больше суток.", old)
        self.assertIn(f"note={self.nid}&amp;tasks=1", old)
        self.assertNotIn("Главное", old)


    def test_verifier_dropped_kept_as_doubtful(self) -> None:
        from assistant.services.transcription_notes import create_with_summary

        tasks = [{"assignee": "Аня", "task": "Согласовать бюджет", "deadline": "31 окт"}]
        dropped = [{"task": "Подумать о ребрендинге", "kind": "not_task", "reason": "размышление вслух",
                    "assignee": "Вова", "deadline": ""}]
        md = "## 📋 Задачи\n- [ ] Аня — Согласовать бюджет — 31 окт"
        note = create_with_summary(1, title="Встреча", transcript="x", summary=md,
                                   meta={"tasks": tasks, "tasks_dropped": dropped})
        n = notes_store.get_note(1, int(note["id"]))
        self.assertEqual(len(n["meta"]["tasks"]), 2)
        self.assertEqual(n["meta"]["tasks_dropped"], [])
        state = mt.rows(n)
        doubt = state["items"][1]
        self.assertEqual((doubt["code"], doubt["label"], doubt["note"]), ("doubtful", "Возможно, не задача", "размышление вслух"))
        self.assertEqual((state["decided"], state["total"]), (0, 1))  # сомнительный — не в счётчике
        body = n["body"]
        self.assertLess(body.index("Согласовать бюджет"), body.index("Подумать о ребрендинге"))  # внизу таблицы
        mt.apply(1, int(note["id"]), doubt["id"], "undo", by="Вова")  # «Это задача»
        state = mt.rows(notes_store.get_note(1, int(note["id"])))
        self.assertEqual(state["items"][1]["code"], "open")
        self.assertEqual(state["total"], 2)

    def _order(self) -> list:
        import re

        body = notes_store.get_note(1, self.nid)["body"]
        return re.findall(r'data-leo-item="(i[0-9a-f]{10})"', body)

    def test_nest_rows_counts_and_order(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        mt.nest(1, self.nid, [a], c, by="Вова")  # первый — под третий
        state, items = self._items()
        self.assertEqual(items[a]["parent"], c)
        self.assertEqual(items[a]["code"], "nested")
        self.assertEqual(items[a]["depth"], 1)
        self.assertEqual(state["decided"], 1)  # вложенный решается вместе с основным
        self.assertEqual(self._order(), [b, c, a])  # подзадача сразу под основной
        mt.nest(1, self.nid, [b], a, by="Вова")  # второй уровень
        _, items = self._items()
        self.assertEqual(items[b]["depth"], 2)
        with self.assertRaises(ValueError):
            mt.nest(1, self.nid, [c], b, by="Вова")  # в свою же подзадачу
        mt.nest(1, self.nid, [a], None, by="Вова")  # вынуть: b едет вместе с a
        _, items = self._items()
        self.assertEqual(items[a]["parent"], "")
        self.assertEqual(items[b]["parent"], a)

    def test_drag_reorder_without_nesting(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        mt.nest(1, self.nid, [c], None, by="Вова", anchor=a, pos="before")
        self.assertEqual(self._order(), [c, a, b])
        mt.nest(1, self.nid, [c], a, by="Вова")  # вложили — встал последним под основным
        mt.nest(1, self.nid, [b], None, by="Вова", anchor=a, pos="after")  # после a вместе с его c
        self.assertEqual(self._order(), [a, c, b])

    def test_nest_into_third_level_goes_beside(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        note = notes_store.get_note(1, self.nid)
        d = "iaaaaaaaaa4"
        meta = dict(note["meta"], tasks=note["meta"]["tasks"] + [{"id": d, "task": "Купить домен", "assignee": ""}])
        notes_store.update_note(1, self.nid, meta=meta)
        mt.nest(1, self.nid, [b], a, by="Вова")
        mt.nest(1, self.nid, [c], b, by="Вова")
        mt.nest(1, self.nid, [d], c, by="Вова")  # c — уже второй уровень: d встаёт рядом с ним
        _, items = self._items()
        self.assertEqual(items[d]["parent"], b)
        self.assertEqual(items[d]["depth"], 2)

    def test_nest_under_taken_row_creates_subtask(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        top = mt.claim(1, self.nid, c, lambda: self._new_task(title="Посмотреть цены"), by="Вова")
        mt.nest(1, self.nid, [b], c, by="Вова")
        _, items = self._items()
        self.assertTrue(items[b]["task_id"])
        sub = tasks_store._get_task_row(items[b]["task_id"])
        self.assertEqual(sub["parent_id"], top["id"])
        mt.nest(1, self.nid, [b], None, by="Вова")  # вынуть — задача становится самостоятельной
        self.assertIsNone(tasks_store._get_task_row(items[b]["task_id"])["parent_id"])

    def test_claim_parent_adopts_nested_rows(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        mt.nest(1, self.nid, [a, b], c, by="Вова")
        top = mt.claim(1, self.nid, c, lambda: self._new_task(title="Посмотреть цены"), by="Вова")
        kids = tasks_store.list_subtasks(top["id"])
        self.assertEqual(sorted(k["title"] for k in kids), ["Проверить юр. тексты", "Согласовать бюджет"])
        state, _ = self._items()
        self.assertEqual(state["decided"], 3)

    def test_dismiss_lifts_children(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        mt.nest(1, self.nid, [b], a, by="Вова")
        mt.nest(1, self.nid, [a], c, by="Вова")
        mt.apply(1, self.nid, a, "dismiss", by="Вова", reason="not_a_task")
        _, items = self._items()
        self.assertEqual(items[b]["parent"], c)

    def _with_groups(self, groups, marks) -> None:
        note = notes_store.get_note(1, self.nid)
        tasks = [dict(t) for t in note["meta"]["tasks"]]
        for i, (key, lead) in marks.items():
            tasks[i]["group_key"] = key
            if lead:
                tasks[i]["group_parent"] = True
        notes_store.update_note(1, self.nid, meta=dict(note["meta"], tasks=tasks, task_groups=groups))

    def test_group_with_lead_row_merges_by_nesting(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        self._with_groups([{"key": "g1", "title": "Цены и бюджет", "state": "suggested"}], {0: ("g1", False), 2: ("g1", True)})
        state, _ = self._items()
        self.assertEqual(state["groups"], [{"id": "g1", "title": "Цены и бюджет", "parent": c, "items": [a]}])
        out = mt.group_op(1, self.nid, "g1", "accept", by="Вова")
        self.assertEqual(out["parent"], c)
        _, items = self._items()
        self.assertEqual(items[a]["parent"], c)
        self.assertEqual(mt.rows(notes_store.get_note(1, self.nid))["groups"], [])

    def test_group_without_lead_adds_row(self) -> None:
        a, b, c = (t["id"] for t in TASKS)
        self._with_groups([{"key": "g1", "title": "Запуск", "state": "suggested"}], {0: ("g1", False), 1: ("g1", False)})
        out = mt.group_op(1, self.nid, "g1", "accept", by="Вова")
        new = out["parent"]
        _, items = self._items()
        self.assertEqual(items[new]["task"], "Запуск")
        self.assertEqual({items[a]["parent"], items[b]["parent"]}, {new})
        self.assertEqual(self._order()[:3], [new, a, b])

    def test_group_reject_and_stale(self) -> None:
        self._with_groups([{"key": "g1", "title": "x", "state": "suggested"}], {0: ("g1", False), 1: ("g1", False)})
        mt.group_op(1, self.nid, "g1", "reject", by="Вова")
        self.assertEqual(mt.rows(notes_store.get_note(1, self.nid))["groups"], [])
        with self.assertRaises(ValueError):
            mt.group_op(1, self.nid, "g1", "accept", by="Вова")

    def test_suggest_groups_on_demand(self) -> None:
        from assistant.nlu import llm

        a, b, c = (t["id"] for t in TASKS)
        with mock.patch.object(llm, "group_tasks", return_value=[{"title": "Бюджет", "parent": None, "items": [0, 2]}]):
            state = mt.suggest_groups(1, self.nid, by="Вова")
        self.assertTrue(state["groups_asked"])
        self.assertEqual(state["groups"][0]["items"], [a, c])


if __name__ == "__main__":
    unittest.main()
