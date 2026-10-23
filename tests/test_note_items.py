"""ID строк: задача саммари / пункт Фокуса держат связь со строкой исходника по ID, а не по тексту."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from assistant.lib import note_items as ni
from assistant.services import focus_items as fi
from assistant.services import proactive
from assistant.services import proactive_ai as pa
from assistant.services import transcription_notes as tnotes
from assistant.stores import calendar_tasks as tasks_store
from assistant.stores import notes as notes_store

NOTE = (
    "<p>Обсудили вайбкодинг с командой.</p>"
    "<ul><li><p>Саша соберёт примеры промптов до пятницы</p></li>"
    "<li><p>Сделать внутренний воркшоп</p></li></ul>"
)


class LinesTests(unittest.TestCase):
    def test_stamp_find_and_marks_skipped(self) -> None:
        mark = '<span class="note-focus-mark" data-leo-mark="task">Задача</span>'
        body = NOTE.replace("воркшоп</p>", "воркшоп " + mark + "</p>") + "<p>" + mark + "</p>"
        text, rows = ni.numbered_text(body)
        self.assertEqual(text.splitlines(), [
            "[1] Обсудили вайбкодинг с командой.",
            "[2] Саша соберёт примеры промптов до пятницы",
            "[3] Сделать внутренний воркшоп",
        ])
        out, ids = ni.stamp(body, [(rows[2], "i0000000001")])
        self.assertEqual(ids, ["i0000000001"])
        self.assertIn('<li data-leo-item="i0000000001"><p>Сделать', out)
        # Строку переписали — по ID она находится всё равно; уже стоящий ID не меняется.
        edited = out.replace("Сделать внутренний воркшоп", "Провести воркшоп для всех")
        self.assertEqual(ni.find_line(edited, "i0000000001")["text"], "Провести воркшоп для всех")
        again, ids2 = ni.stamp(edited, [(ni.find_line(edited, "i0000000001"), "i0000000002")])
        self.assertEqual((again, ids2), (edited, ["i0000000001"]))


class _Db(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["CALENDAR_TASKS_DB_PATH"] = os.path.join(self._tmp.name, "calendar_tasks.sqlite")
        for m in (notes_store, pa, proactive, fi, tasks_store):
            m._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
        for m in (notes_store, pa, proactive, fi, tasks_store):
            m._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()


class SummaryIdsTests(_Db):
    def test_summary_saved_as_html_with_task_ids(self) -> None:
        tasks = [
            {"assignee": "Дима", "task": "Подготовить API", "deadline": "до пятницы"},
            {"assignee": "", "task": "Собрать фидбек"},
        ]
        md = "## 📌 Кратко\n\nСинк.\n\n## 📋 Задачи\n\n- [ ] Дима — Подготовить API — до пятницы\n- [ ] Собрать фидбек"
        note = tnotes.create_with_summary(1, title="Синк", transcript="текст", summary=md, meta={"tasks": tasks})
        body = notes_store.get_note(1, int(note["id"]))["body"]
        ids = [t["id"] for t in notes_store.get_note(1, int(note["id"]))["meta"]["tasks"]]
        self.assertTrue(body.startswith("<h2>📌 Кратко</h2>"))
        self.assertTrue(all(ni.ITEM_ID_RE.match(i) for i in ids))
        self.assertEqual(ni.find_line(body, ids[0])["text"], "Дима — Подготовить API — до пятницы")
        self.assertEqual(ni.find_line(body, ids[1])["text"], "Собрать фидбек")

        # Ключ пункта Фокуса — по ID задачи: правка формулировки его не меняет.
        n = notes_store.get_note(1, int(note["id"]))
        now = datetime.now(timezone.utc)
        card = proactive.rule_summary_tasks([n], {}, now, timezone.utc)[0]
        self.assertEqual(card["items"][0]["item"], ids[0])
        n["meta"]["tasks"][0]["task"] = "Подготовить API v2"
        card2 = proactive.rule_summary_tasks([n], {}, now, timezone.utc)[0]
        self.assertEqual(card["items"][0]["id"], card2["items"][0]["id"])

        # Пункт поставлен в задачу — чип встаёт в строку задачи, даже если строку переписали.
        row = fi.item_from_card(card)[0]
        fi.remember(1, row)
        edited = body.replace("Подготовить API — до пятницы", "Сделать новое API к понедельнику")
        notes_store.update_note(1, int(note["id"]), body=edited)
        fi.apply(1, row["key"], "converted", by="Вова", task_id=5)
        line = ni.find_line(notes_store.get_note(1, int(note["id"]))["body"], ids[0])
        self.assertIn("note-focus-mark", notes_store.get_note(1, int(note["id"]))["body"].split(ids[0], 1)[1].split("</li>", 1)[0])
        self.assertEqual(line["text"], "Дима — Сделать новое API к понедельнику")
        meta_task = notes_store.get_note(1, int(note["id"]))["meta"]["tasks"][0]
        self.assertTrue(meta_task["completed"])


class InsightAnchorTests(_Db):
    def _analyze(self, items: list[dict]) -> list[dict]:
        note = notes_store.get_note(1, self.nid)
        with mock.patch.object(pa, "_llm_items", return_value=items) as llm:
            out = pa.analyze_note(1, note, "plain", "h")
        self.sent = llm.call_args.args[1]
        return out

    def test_insight_gets_line_id_and_stable_key(self) -> None:
        self.nid = int(notes_store.create_note(1, "Зум документ по вайбкоду", NOTE)["id"])
        # ИИ ошибся номером строки — код находит строку по цитате.
        items = self._analyze([{"kind": "agreement", "text": "Запустить воркшоп по вайбкодингу", "who": "я", "due": "",
                                "people": [], "quote": "Сделать внутренний воркшоп", "line": 1}])
        self.assertIn("[3] Сделать внутренний воркшоп", self.sent)
        iid = items[0]["item"]
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertEqual(ni.find_line(body, iid)["text"], "Сделать внутренний воркшоп")
        today = datetime.now(timezone.utc).date()
        card = pa.card_for(items[0], src="из «Зум»", note={"id": self.nid, "kind": "note", "title": "Зум"},
                           today=today, task_titles=[])
        self.assertEqual(card["key"], f"ins:{self.nid}:{iid}")
        # Повторный разбор пересказал пункт иначе — ключ тот же.
        items2 = self._analyze([{"kind": "agreement", "text": "Организовать воркшоп для команды", "who": "я", "due": "",
                                 "people": [], "quote": "внутренний воркшоп", "line": 3}])
        self.assertEqual(items2[0]["item"], iid)

        # В задачу — чип в строке; задачу переименовали — подпись чипа новая.
        row = fi.item_from_card(card)[0]
        fi.remember(1, row)
        task = tasks_store.create_task(1, title="Воркшоп по вайбкоду", start_at=datetime.now(timezone.utc) + timedelta(days=1),
                                       note_id=str(self.nid))
        fi.apply(1, row["key"], "converted", by="Вова", task_id=task["id"])
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertIn(">Воркшоп по вайбкоду</span></p></li>", body)
        tasks_store.update_task(1, task["id"], title="Воркшоп 30 октября")
        body = notes_store.get_note(1, self.nid)["body"]
        self.assertIn(">Воркшоп 30 октября</span></p></li>", body)
        self.assertEqual(body.count("note-focus-mark"), 1)

    def test_unverified_line_is_not_anchored(self) -> None:
        self.nid = int(notes_store.create_note(1, "Заметка", NOTE)["id"])
        items = self._analyze([{"kind": "open_question", "text": "Кто платит за подписки?", "who": "", "due": "",
                                "people": [], "quote": "бюджет на подписки", "line": 2}])
        self.assertNotIn("item", items[0])
        self.assertEqual(notes_store.get_note(1, self.nid)["body"], NOTE)
