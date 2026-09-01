import json
import os
import tempfile
import unittest

from assistant.lib import usage_store


class SummaryStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        usage_store.init_db()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_large_json_not_truncated(self) -> None:
        long_transcript = "слово " * 3000
        long_summary = "📌 Кратко\n" + ("решение " * 500)
        meta = {
            "content_type": "meeting",
            "confidence": 0.9,
            "main_topic": "Длинная встреча",
            "participants": ["Иван", "Олег"],
            "tasks": [{"assignee": "Иван", "task": "тест", "deadline": "1 июня"}],
            "decisions": ["решение A", "решение B"],
            "topics": ["тема 1"],
        }
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="g1",
            usage={
                "text": long_summary,
                "transcript": long_transcript,
                "meta": meta,
            },
            telegram_user_id="7",
        )
        row = usage_store.get_user_usage_event("7", 1)
        self.assertIsNotNone(row)
        raw = str(row.get("raw_usage_json") or "")
        self.assertGreater(len(raw), 4000)
        j = json.loads(raw)
        self.assertEqual(j["transcript"], long_transcript)
        self.assertEqual(j["text"], long_summary)
        parsed_meta = usage_store.journal_meta_from_raw(raw)
        self.assertEqual(parsed_meta.get("main_topic"), "Длинная встреча")
        self.assertEqual(len(parsed_meta.get("decisions") or []), 2)

    def test_token_only_summarize_hidden_from_journal(self) -> None:
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="tok",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            telegram_user_id="7",
        )
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="content",
            usage={"text": "<b>📌 Кратко</b><br><br>Текст", "meta": {}},
            telegram_user_id="7",
        )
        rows = usage_store.user_journal_entries("7", limit=10)
        summarize_rows = [r for r in rows if r.get("operation") == "summarize"]
        self.assertEqual(len(summarize_rows), 1)
        self.assertIn("Кратко", usage_store.journal_text_from_raw(summarize_rows[0]["raw_usage_json"]))


if __name__ == "__main__":
    unittest.main()
