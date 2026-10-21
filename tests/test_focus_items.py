"""«Фокус»: разбор пунктов — статусы, правки, отметка в исходнике, не воскрешать разобранное."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from assistant.services import focus_items as fi
from assistant.services import proactive
from assistant.services import proactive_ai as pa
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
        for m in (notes_store, pa, proactive, fi):
            m._CONN = None  # type: ignore[attr-defined]
        self.note = notes_store.create_note(
            1, "Синк с Альфой", SUMMARY,
            meta={"tasks": [
                {"assignee": "Дима", "task": "Подготовить API для интеграции", "deadline": "пятница"},
                {"assignee": "Ира", "task": "Отправить договор", "deadline": "среда"},
            ]},
        )

    def tearDown(self) -> None:
        for m in (notes_store, pa, proactive, fi):
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
