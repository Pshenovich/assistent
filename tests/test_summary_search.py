import os
import tempfile
import unittest

from assistant.lib import usage_store
from assistant.skills import summary as summary_skill


class SummarySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        usage_store.init_db()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _insert_summary(
        self,
        *,
        text: str,
        meta: dict,
        user_id: str = "42",
    ) -> None:
        usage_store.insert_usage_event(
            operation="summarize",
            model="test-model",
            generation_id="s1",
            usage={
                "text": text,
                "transcript": "полный транскрипт",
                "source": "telegram",
                "meta": meta,
            },
            telegram_user_id=user_id,
        )

    def test_search_by_decisions(self) -> None:
        self._insert_summary(
            text="📌 Кратко\nРешили перенести релиз.",
            meta={
                "content_type": "meeting",
                "main_topic": "Релиз приложения",
                "decisions": ["релиз переносится на 15 июня", "добавляем Google auth"],
                "topics": ["мобильное приложение"],
                "tasks": [],
            },
        )
        hits = usage_store.search_summaries("42", "Google auth", field="decisions")
        self.assertEqual(len(hits), 1)
        decisions = hits[0]["meta"].get("decisions") or []
        self.assertTrue(any("Google" in str(d) for d in decisions))

    def test_search_by_transcript_text(self) -> None:
        usage_store.insert_usage_event(
            operation="summarize",
            model="test-model",
            generation_id="s-tr",
            usage={
                "text": "📌 Кратко\nКороткое саммари.",
                "transcript": "Обсуждали интеграцию Bitrix и API ключи",
                "source": "telegram",
                "meta": {
                    "content_type": "meeting",
                    "main_topic": "Bitrix",
                    "decisions": [],
                    "topics": [],
                    "tasks": [],
                },
            },
            telegram_user_id="42",
        )
        hits = usage_store.search_summaries("42", "Bitrix API")
        self.assertEqual(len(hits), 1)
        self.assertIn("Bitrix", hits[0].get("transcript") or "")

    def test_search_by_topics(self) -> None:
        self._insert_summary(
            text="📌 О чем\nМобильная разработка",
            meta={
                "content_type": "meeting",
                "main_topic": "Синк",
                "topics": ["мобильное приложение", "дизайн"],
                "decisions": [],
                "tasks": [],
            },
        )
        hits = usage_store.search_summaries(
            "42", "мобильное приложение", field="topics"
        )
        self.assertEqual(len(hits), 1)

    def test_search_tasks_by_assignee(self) -> None:
        self._insert_summary(
            text="📋 Задачи",
            meta={
                "content_type": "meeting",
                "main_topic": "Спринт",
                "tasks": [
                    {"assignee": "Андрей", "task": "настроить сервер", "deadline": "12 июня"},
                    {"assignee": "Иван", "task": "макеты", "deadline": "10 июня"},
                ],
                "decisions": [],
                "topics": [],
            },
        )
        hits = usage_store.search_summaries(
            "42", "сервер", field="tasks", assignee="Андрей"
        )
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["meta"]["tasks"][0]["assignee"], "Андрей")

    def test_search_preserves_html_in_result(self) -> None:
        html = (
            "<b>📌 Кратко</b><br><br>Решили перенести релиз."
            "<br><br><b>📋 Задачи</b><br><br>• Настроить сервер"
        )
        self._insert_summary(
            text=html,
            meta={
                "content_type": "meeting",
                "main_topic": "Релиз приложения",
                "decisions": ["релиз переносится"],
                "topics": [],
                "tasks": [],
            },
        )
        hits = usage_store.search_summaries("42", "релиз")
        self.assertEqual(len(hits), 1)
        self.assertIn("<br>", hits[0]["text"])
        self.assertIn("<b>📋 Задачи</b>", hits[0]["text"])

    def test_format_summary_message_preserves_html(self) -> None:
        html = "<b>📌 Кратко</b><br><br>Решили перенести релиз."
        msg = summary_skill._format_summary_message(
            {
                "headline": "📌 Кратко",
                "text": html,
                "ts_utc": "2026-06-16T12:00:00",
            }
        )
        self.assertIn("## 📌 Кратко", msg)
        self.assertIn("*16 июня 2026, 12:00*", msg)
        self.assertIn("Решили перенести релиз.", msg)

    def test_format_summary_message_preserves_plain_newlines(self) -> None:
        text = "📌 Кратко\n\nРешили перенести релиз."
        msg = summary_skill._format_summary_message(
            {
                "headline": "📌 Кратко",
                "text": text,
            }
        )
        self.assertEqual(msg, text)


if __name__ == "__main__":
    unittest.main()
