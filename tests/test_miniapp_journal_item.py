import os
import tempfile
import unittest

from assistant.lib import usage_store


class MiniappJournalItemTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        usage_store.init_db()
        usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="g1",
            usage={
                "text": "<b>📌 Кратко</b><br><br>Текст",
                "source_url": "https://disk.yandex.ru/i/test",
                "telegram_link": "https://t.me/example/1",
                "meta": {"main_topic": "Тест"},
            },
            telegram_user_id="11",
        )

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_journal_source_links_importable_flow(self) -> None:
        row = usage_store.get_user_usage_event("11", 1)
        self.assertIsNotNone(row)
        raw = str(row.get("raw_usage_json") or "")
        links = usage_store.journal_source_links_from_raw(raw)
        self.assertEqual(links.get("source_url"), "https://disk.yandex.ru/i/test")
        self.assertEqual(links.get("telegram_link"), "https://t.me/example/1")

    def test_update_user_journal_event(self) -> None:
        updated = usage_store.update_user_journal_event(
            "11",
            1,
            text="Новый текст",
            main_topic="Новый заголовок",
        )
        self.assertIsNotNone(updated)
        self.assertEqual(
            usage_store.journal_text_from_raw(updated.get("raw_usage_json")),
            "Новый текст",
        )
        meta = usage_store.journal_meta_from_raw(updated.get("raw_usage_json"))
        self.assertEqual(meta.get("main_topic"), "Новый заголовок")

    def test_find_summary_event_for_transcript(self) -> None:
        transcript_id = usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="t1",
            usage={"text": "transcript body", "meta": {"meeting_topic": "Weekly sync"}},
            telegram_user_id="11",
        )
        summary_id = usage_store.insert_usage_event(
            operation="summarize",
            model="m",
            generation_id="s1",
            usage={
                "text": "## Summary",
                "meta": {"main_topic": "Weekly sync", "transcript_event_id": transcript_id},
            },
            telegram_user_id="11",
        )
        found = usage_store.find_summary_event_for_transcript("11", transcript_id)
        self.assertEqual(found, summary_id)


if __name__ == "__main__":
    unittest.main()
