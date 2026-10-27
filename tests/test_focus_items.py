"""«Фокус»: разбор пунктов — статусы, правки, отметка в исходнике, не воскрешать разобранное."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from assistant.services import focus_items as fi
from assistant.services import proactive
from assistant.services import proactive_ai as pa
from assistant.stores import calendar_tasks as tasks_store
from assistant.stores import notes as notes_store

SUMMARY = (
    "<h2>Задачи</h2><ul>"
    "<li>Дима — подготовить API для интеграции до пятницы</li>"
    "<li>Ира — отправить договор до среды<ul><li>приложить смету</li></ul></li>"
    "</ul>"
)


class FocusItemsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["CALENDAR_TASKS_DB_PATH"] = os.path.join(self._tmp.name, "calendar_tasks.sqlite")
        for m in (notes_store, pa, proactive, fi, tasks_store):
            m._CONN = None  # type: ignore[attr-defined]
        self.note = notes_store.create_note(
            1, "Синк с Альфой", SUMMARY,
            meta={"tasks": [
                {"assignee": "Дима", "task": "Подготовить API для интеграции", "deadline": "пятница"},
                {"assignee": "Ира", "task": "Отправить договор", "deadline": "среда"},
            ]},
        )

    def tearDown(self) -> None:
        os.environ.pop("CALENDAR_TASKS_DB_PATH", None)
        for m in (notes_store, pa, proactive, fi, tasks_store):
            m._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def _remember(self, key: str = "ins:1:abc", text: str = "Подготовить API для интеграции", who: str = "Дима") -> dict:
        return fi.remember(1, {
            "key": key, "card_key": key, "kind": "agreement", "text": text, "who": who,
            "source": {"kind": "note", "id": int(self.note["id"]), "title": "Синк с Альфой"},
        })

    def test_done_marks_source_and_meta(self) -> None:
        self._remember()
        row = fi.apply(1, "ins:1:abc", "done", by="Вова")
        self.assertEqual(row["status"], "completed")
        note = notes_store.get_note(1, int(self.note["id"]))
        body = note["body"]
        self.assertIn('class="note-focus-mark"', body)
        self.assertIn('data-leo-mark-by="Вова"', body)
        # Значок — в конце нужной строки, а не у соседней.
        self.assertLess(body.index("note-focus-mark"), body.index("Ира"))
        t = note["meta"]["tasks"][0]
        self.assertTrue(t["completed"])
        self.assertEqual(t["completed_by"], "Вова")
        self.assertFalse(note["meta"]["tasks"][1].get("completed"))
        # Для ИИ значок — текст; для хеша и разбора — его нет.
        self.assertIn("[выполнено: Вова", fi.marks_to_text(body))
        self.assertEqual(fi.strip_marks(body).replace(" <", "<"), SUMMARY)
        # Отмена снимает значок.
        fi.apply(1, "ins:1:abc", "undo", by="Вова")
        note = notes_store.get_note(1, int(self.note["id"]))
        self.assertNotIn("note-focus-mark", note["body"])
        self.assertFalse(note["meta"]["tasks"][0]["completed"])

    def test_mark_goes_before_nested_list(self) -> None:
        self._remember("ins:1:ira", "Отправить договор", "Ира")
        fi.apply(1, "ins:1:ira", "link", by="Вова", url="https://bitrix24.ru/task/1")
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertLess(body.index("note-focus-mark"), body.index("приложить смету"))
        self.assertIn('data-leo-mark-url="https://bitrix24.ru/task/1"', body)
        self.assertIn(">Задача</span>", body)

    def test_converted_task_becomes_chip_in_source(self) -> None:
        self._remember()
        fi.apply(1, "ins:1:abc", "converted", by="Вова", task_id=42)
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertIn('data-leo-mark="task"', body)
        self.assertIn('data-leo-mark-task="42"', body)
        self.assertIn(">Подготовить API для интеграции</span>", body)
        self.assertNotIn(">✓<", body)
        self.assertIn("[поставлена задача «Подготовить API для интеграции»", fi.marks_to_text(body))
        self.assertEqual(fi.strip_marks(body).replace(" <", "<"), SUMMARY)
        # Повторная отметка заменяет чип, а не добавляет второй.
        fi.apply(1, "ins:1:abc", "converted", by="Вова", task_id=43)
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertEqual(body.count("note-focus-mark"), 1)
        self.assertIn('data-leo-mark-task="43"', body)

    def test_converted_paraphrased_insight_gets_chip_at_end(self) -> None:
        # Инсайт пересказан своими словами — похожей строки нет, но чип задачи всё равно в заметке.
        self._remember(key="ins:1:para", text="Запустить пилот вайбкодинга с командой", who="")
        fi.apply(1, "ins:1:para", "converted", by="Вова", task_id=159)
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertIn('data-leo-mark-task="159"', body)
        self.assertTrue(body.startswith(SUMMARY))
        self.assertTrue(body.endswith("</span></p>"))
        fi.apply(1, "ins:1:para", "converted", by="Вова", task_id=160)
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertEqual(body.count("note-focus-mark"), 1)
        self.assertNotIn("<p></p>", body)
        fi.apply(1, "ins:1:para", "undo", by="Вова")
        self.assertEqual(notes_store.get_note(1, int(self.note["id"]))["body"], SUMMARY)

    def test_paraphrased_dismiss_leaves_note_untouched(self) -> None:
        self._remember(key="ins:1:para", text="Запустить пилот вайбкодинга с командой", who="")
        fi.apply(1, "ins:1:para", "dismiss", by="Вова", reason="duplicate")
        self.assertEqual(notes_store.get_note(1, int(self.note["id"]))["body"], SUMMARY)

    def test_markdown_body_is_never_touched(self) -> None:
        md = "## 📋 Задачи\n- [ ] Дима — подготовить API\n"
        row = {"item_key": "ins:1:md", "text": "Что-то другое", "who": "", "status": "converted",
               "link": {"task_id": 7}, "fields": {}}
        self.assertEqual(fi.place_mark(md, row), (md, False))

    def test_summary_task_chip_goes_to_task_line_not_decision(self) -> None:
        # Те же слова в «Решениях» и в «Задачах»: у пункта нет ID строки — ID берём из meta.tasks.
        body = (
            "<h2>✅ Принятые решения</h2><ul><li><p>Создать задачи по BGPT и передать их на оценку.</p></li></ul>"
            '<h2>📋 Задачи</h2><ul class="note-task-list">'
            '<li data-leo-item="iebb2efaf59"><p>Спикер 1 — Создать задачи по BGPT и передать на оценку</p></li>'
            '<li data-leo-item="i7fa62c0fbc"><p>Спикер 1 — Отчитаться по задачам BGPT — Сегодня</p></li></ul>'
        )
        tasks = [
            {"id": "iebb2efaf59", "assignee": "Спикер 1", "task": "Создать задачи по BGPT и передать на оценку"},
            {"id": "i7fa62c0fbc", "assignee": "Спикер 1", "task": "Отчитаться по задачам BGPT", "deadline": "Сегодня"},
        ]
        row = {"item_key": "sumtask:1:x", "text": "Создать задачи по BGPT и передать на оценку", "who": "Спикер 1",
               "status": "converted", "link": {"task_id": 227, "title": "Создать задачи по BGPT"}, "fields": {},
               "source": {"kind": "note", "id": 1}}
        out, changed = fi.place_mark(body, row, tasks)
        self.assertTrue(changed)
        mark = out.index("note-focus-mark")
        self.assertGreater(mark, out.index("iebb2efaf59"))
        self.assertLess(mark, out.index("i7fa62c0fbc"))

    def test_mark_never_lands_on_heading(self) -> None:
        body = "<h2>📋 Задачи</h2><ul><li><p>Ира — отправить договор</p></li></ul>"
        row = {"item_key": "ins:1:h", "text": "Оценить задачи по почте", "who": "", "status": "dismissed",
               "reason": "not_relevant", "link": {}, "fields": {}}
        out, _ = fi.place_mark(body, row)
        self.assertNotIn("note-focus-mark", out)

    def test_quote_finds_line_for_paraphrase(self) -> None:
        row = {"item_key": "ins:1:q", "text": "Сделать интеграционный API", "who": "", "quote": "подготовить API для интеграции до пятницы",
               "status": "converted", "link": {"task_id": 7}, "fields": {}}
        body, changed = fi.place_mark(SUMMARY, row)
        self.assertTrue(changed)
        self.assertLess(body.index("note-focus-mark"), body.index("Ира"))

    def test_converted_via_assistant_finds_created_task(self) -> None:
        # «В задачу» на карточке Фокуса: задачу ставит ассистент, id не приходит — берём свежую похожую.
        self._remember()
        tasks_store.create_task(1, title="Другое дело", start_at=datetime.now(timezone.utc))
        task = tasks_store.create_task(1, title="Подготовить API для интеграции", start_at=datetime.now(timezone.utc))
        row = fi.apply(1, "ins:1:abc", "converted", by="Вова")
        self.assertEqual(row["link"]["task_id"], int(task["id"]))
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertIn('data-leo-mark-task="%s"' % task["id"], body)

    def test_dismissed_and_retyped_are_marked_in_source(self) -> None:
        self._remember()
        self._remember("ins:1:ira", "Отправить договор", "Ира")
        fi.apply(1, "ins:1:abc", "dismiss", by="Вова", reason="duplicate")
        fi.apply(1, "ins:1:ira", "retype", by="Вова", type="decision")
        body = notes_store.get_note(1, int(self.note["id"]))["body"]
        self.assertIn('data-leo-mark="dismissed"', body)
        self.assertIn(">дубль</span>", body)
        self.assertIn('data-leo-mark="retyped"', body)
        self.assertIn(">решение</span>", body)
        self.assertIn("[скрыто: дубль", fi.marks_to_text(body))
        fi.apply(1, "ins:1:abc", "undo", by="Вова")
        self.assertNotIn(">дубль<", notes_store.get_note(1, int(self.note["id"]))["body"])

    def test_feed_hides_handled_and_rephrased(self) -> None:
        self._remember()
        fi.apply(1, "ins:1:abc", "dismiss", reason="duplicate")
        card = {"key": "ins:1:abc", "type": "agreement", "title": "Дима — API", "items": [{"id": "ins:1:abc", "text": "x"}]}
        self.assertEqual(fi.apply_to_feed(1, [card]), [])
        # Переформулированный пункт ловится по словам (через done_titles → _covered).
        self.assertTrue(pa._covered("Дима подготовит API интеграции", proactive.done_titles(1)))

    def test_snooze_returns_after_until(self) -> None:
        self._remember()
        fi.apply(1, "ins:1:abc", "snooze", until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        card = {"key": "ins:1:abc", "type": "agreement", "title": "t"}
        self.assertEqual(fi.apply_to_feed(1, [card]), [])
        later = datetime.now(timezone.utc) + timedelta(hours=2)
        self.assertEqual(len(fi.apply_to_feed(1, [card], later)), 1)
        with self.assertRaises(ValueError):
            fi.apply(1, "ins:1:abc", "snooze", until=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())

    def test_update_and_retype(self) -> None:
        self._remember()
        fi.apply(1, "ins:1:abc", "update", fields={"assignee": "Ира", "deadline": "2026-10-22"})
        card = {"key": "ins:1:abc", "type": "agreement", "title": "Дима — API", "source": "из «Синк» · срок 24 окт"}
        out = fi.apply_to_feed(1, [card])[0]
        self.assertTrue(out["title"].startswith("Ира — "))
        self.assertEqual(out["source"], "из «Синк» · срок 22 окт")
        meta = notes_store.get_note(1, int(self.note["id"]))["meta"]
        self.assertEqual(meta["tasks"][0]["assignee"], "Ира")
        fi.apply(1, "ins:1:abc", "retype", type="promise")
        self.assertEqual(fi.apply_to_feed(1, [card])[0]["tag"], "🤝 обещание")
        fi.apply(1, "ins:1:abc", "retype", type="decision")
        self.assertEqual(fi.apply_to_feed(1, [card]), [])
        self.assertIn("Подготовить API для интеграции", notes_store.get_note(1, int(self.note["id"]))["meta"]["decisions"])

    def test_summary_group_drops_handled_items(self) -> None:
        card = proactive.rule_summary_tasks(
            [{"id": int(self.note["id"]), "title": "Синк", "created_at": datetime.now(timezone.utc).isoformat(),
              "meta": notes_store.get_note(1, int(self.note["id"]))["meta"]}],
            {}, datetime.now(timezone.utc), timezone.utc,
        )[0]
        snaps = fi.item_from_card(card)
        self.assertEqual(snaps[0]["who"], "Дима")
        for s in snaps:
            fi.remember(1, s)
        fi.apply(1, snaps[0]["key"], "done")
        out = fi.apply_to_feed(1, [card])[0]
        self.assertEqual(len(out["items"]), 1)
        self.assertIn("1 задача", out["title"])

    def test_chat_source_keeps_message_url(self) -> None:
        card = pa.chat_card(
            {"kind": "request", "text": "Скинь отчёт", "who": "Ира", "msg": 42},
            chat_id=-1001234567890, day="2026-10-22", title="Продажи",
            today=datetime(2026, 10, 22).date(), task_titles=[],
        )
        snap = fi.item_from_card(card)[0]
        self.assertEqual(snap["source"]["url"], "https://t.me/c/1234567890/42")
        row = fi.remember(1, snap)
        self.assertEqual(fi.public(row)["source"]["url"], "https://t.me/c/1234567890/42")

    def test_assistant_tool_and_chat_card(self) -> None:
        from assistant.services import gpt_calendar_tools as gcal

        self._remember()
        ctx = gcal.GptCalendarContext(uid=1, display_name="Вова")
        out = gcal.tool_focus_item_update(ctx, {"key": "ins:1:abc", "op": "update", "assignee": "Ира"})
        self.assertIn('"ok": true', out)
        # Карточка под ответом показывает живое состояние пункта.
        card = gcal.public_action(gcal.get_action(ctx.actions[0]), viewer_uid=1)
        self.assertEqual(card["kind"], "focus_item")
        self.assertEqual(card["focus"][0]["assignee"], "Ира")
        # Без key — по тексту.
        gcal.tool_focus_item_update(ctx, {"text": "API для интеграции", "op": "done"})
        self.assertEqual(fi.get(1, "ins:1:abc")["status"], "completed")
        self.assertIn("error", gcal.tool_focus_item_update(ctx, {"key": "ins:1:abc", "op": "update"}))

    def test_same_chat_item_on_two_days_is_one_card(self) -> None:
        text = "Минус у клиента Целевые Финансы, Вергиз, 32практика, BoostraKC, 369support"
        pa.save(1, "digest:-1001:2026-10-18", "h1", "Obuchat AI", [{"kind": "escalation", "text": text, "who": "Уведомления", "msg": 3, "due": ""}])
        pa.save(1, "digest:-1001:2026-10-20", "h2", "Obuchat AI", [{"kind": "request", "text": text, "who": "Уведомления", "msg": 9, "due": ""}])
        cards = pa.chat_cards(1)
        self.assertEqual(len(cards), 2)
        out = proactive.rank(cards, set())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["chat"]["date"], "2026-10-20")  # свежая копия
        # Скрыли одну копию — не всплывает и вторая.
        self.assertEqual(proactive.rank(cards, {cards[0]["key"]}), [])
        # Разобрали пункт — похожие пункты любого типа больше не поднимаются.
        fi.remember(1, {"key": out[0]["key"], "kind": "chat_request", "text": text, "source": {"kind": "chat"}})
        fi.apply(1, out[0]["key"], "dismiss", reason="duplicate")
        self.assertEqual(pa.chat_cards(1), [])

    def test_different_items_are_not_merged(self) -> None:
        a = {"key": "a", "type": "chat_request", "urgency": 3, "title": "Согласовать смету по проекту", "items": [{"id": "a"}]}
        b = {"key": "b", "type": "chat_request", "urgency": 3, "title": "Прислать доступы к серверу", "items": [{"id": "b"}]}
        self.assertEqual(len(proactive.rank([a, b], set())), 2)

    def test_link_needs_http(self) -> None:
        self._remember()
        with self.assertRaises(ValueError):
            fi.apply(1, "ins:1:abc", "link", url="javascript:alert(1)")


class PublicStatusTests(unittest.TestCase):
    def test_expired_snooze_is_open_again(self) -> None:
        now = datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc)
        row = {"item_key": "sumtask:1:a", "status": "snoozed", "snooze_until": "2026-10-21T06:00:00+00:00"}
        self.assertEqual(fi.public(row, now)["status"], "new")
        row["snooze_until"] = "2026-10-21T09:00:00+00:00"
        self.assertEqual(fi.public(row, now)["status"], "snoozed")
        self.assertEqual(fi.public({"item_key": "k", "status": "converted"}, now)["status"], "converted")


if __name__ == "__main__":
    unittest.main()
