"""«Фокус», этап 3: важное из чатов, отчёты (план дня / итоги недели)."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from assistant.services import proactive, proactive_ai as pa, proactive_reports as pr
from assistant.stores import notes as notes_store

TODAY = date(2026, 10, 17)


class ChatInsightsTests(unittest.TestCase):
    def test_transcript_marks_me(self) -> None:
        t = pa.chat_transcript_for(7, [
            {"message_id": 1, "user_id": 7, "text": "Пришлю смету в пн", "ts_utc": "2026-10-16T11:02:00"},
            {"message_id": 2, "user_id": 9, "display_name": "Мария", "text": "Ждём смету", "ts_utc": "2026-10-16T12:40:00"},
            {"message_id": 3, "user_id": 9, "text": "  "},
        ])
        self.assertEqual(t.splitlines(), ["#1 [11:02] Я: Пришлю смету в пн", "#2 [12:40] Мария: Ждём смету"])

    def test_normalize_and_cards(self) -> None:
        items = pa.normalize_chat_items({"items": [
            {"kind": "request", "text": "Мария просит смету", "who": "Мария", "msg": "2"},
            {"kind": "promise", "text": "Пришлю доступы", "msg": 1, "due": "2026-10-20"},
            {"kind": "gossip", "text": "x"},
        ]})
        self.assertEqual([i["kind"] for i in items], ["request", "promise"])
        req = pa.chat_card(items[0], chat_id=-1001234567890, day="2026-10-16", title="Продукт", today=TODAY, task_titles=[])
        self.assertEqual(req["urgency"], 3)
        self.assertEqual(req["source"], "чат «Продукт» · вчера · Мария")
        self.assertEqual(req["actions"][1], {"label": "Открыть чат", "act": "url", "payload": {"url": "https://t.me/c/1234567890/2"}})
        prom = pa.chat_card(items[1], chat_id=-555, day="2026-10-17", title="Продукт", today=TODAY, task_titles=[])
        self.assertEqual(prom["tag"], "🤝 вы обещали")
        self.assertNotIn("url", [a["act"] for a in prom["actions"]])  # у обычной группы ссылки на сообщение нет
        self.assertIsNone(pa.chat_card(items[1], chat_id=-1, day="2026-10-17", title="П", today=TODAY, task_titles=["Прислать доступы"]))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        os.environ["USER_PREFS_DIR"] = os.path.join(self._tmp.name, "users")
        for m in (notes_store, pa, proactive):
            m._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        os.environ.pop("USER_PREFS_DIR", None)
        for m in (notes_store, pa, proactive):
            m._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_chat_cards_from_saved_insights(self) -> None:
        pa.save(1, "digest:-1001:2026-10-16", "h", "Продукт", [{"kind": "escalation", "text": "Клиент недоволен сроками", "who": "Иван", "msg": 5, "due": ""}])
        cards = pa.chat_cards(1)
        self.assertEqual(cards[0]["type"], "chat_escalation")
        self.assertEqual(cards[0]["actions"][0]["label"], "Встреча")
        self.assertEqual(pa.chat_cards(1, chat_id=-1001, day="2026-10-15"), [])
        self.assertEqual(pa.insight_cards(1, task_titles=[]), [])  # дайджест не путается с заметками

    def test_one_report_note_per_day(self) -> None:
        with mock.patch.object(pr, "_llm", return_value="<h3>Сначала</h3><ul><li>Смета</li></ul><script>x</script>"), mock.patch(
            "assistant.integrations.openrouter_client.set_openrouter_usage_telegram_user"
        ):
            now = datetime(2026, 10, 17, 7, 0, tzinfo=timezone.utc)
            a = pr.day_plan(1, now=now, events=[])
            b = pr.day_plan(1, now=now + timedelta(hours=2), events=[])
        self.assertEqual(a["id"], b["id"])
        self.assertNotIn("script", b["body"])
        self.assertIn("План на день · 17 окт", b["title"])
        from assistant.stores import tags as tags_store

        tags = tags_store.get_item_tags(1, "local", a["id"])
        self.assertEqual([t["name"] for t in tags], [pr.PROJECT])

    def test_weekly_card(self) -> None:
        with mock.patch.object(pr, "_llm", return_value="<h3>Главное</h3>"), mock.patch(
            "assistant.integrations.openrouter_client.set_openrouter_usage_telegram_user"
        ):
            note = pr.weekly(1, now=datetime(2026, 10, 16, 16, 0, tzinfo=timezone.utc), events=[])
        self.assertIn("Итоги недели", note["title"])
        card = pr.weekly_card(1, now=datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(card["note"]["id"], note["id"])
        self.assertIsNone(pr.weekly_card(1, now=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)))


class SanitizeTests(unittest.TestCase):
    def test_sanitize(self) -> None:
        out = pr._sanitize('```html\n<h3>Итоги</h3><p onclick="x">a</p><img src=x><ul><li>b</li></ul>```')
        self.assertEqual(out, "<h3>Итоги</h3><p>a</p><ul><li>b</li></ul>")


if __name__ == "__main__":
    unittest.main()
