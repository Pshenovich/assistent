import os
import tempfile
import unittest
from datetime import datetime, timezone
from typing import Optional

from assistant.lib import usage_store
from assistant.lib.journal_retrieval import (
    build_journal_context_text,
    journal_qa_likely_user_text,
    retrieve_journal_context,
)
from assistant.stores import notes as notes_store


class JournalRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        os.environ["USAGE_DB_PATH"] = os.path.join(self._tmpdir.name, "usage.sqlite")
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmpdir.name, "notes.sqlite")
        usage_store.init_db()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _insert_summary(
        self,
        *,
        text: str,
        meta: dict,
        transcript: str = "",
        user_id: str = "42",
        when: Optional[datetime] = None,
    ) -> int:
        return usage_store.insert_usage_event(
            operation="summarize",
            model="test-model",
            generation_id=f"s-{when}",
            usage={
                "text": text,
                "transcript": transcript,
                "source": "telegram",
                "meta": meta,
            },
            telegram_user_id=user_id,
            ts_utc=when,
        )

    def test_cross_source_retrieval(self) -> None:
        notes_store.create_note("42", "GPT заметка", "Обсуждали интеграцию GPT с продуктом")
        usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="t1",
            usage={"text": "На встрече говорили про GPT и модели", "source": "telegram"},
            telegram_user_id="42",
        )
        self._insert_summary(
            text="📌 Кратко\nДоговорились внедрить GPT.",
            meta={
                "content_type": "meeting",
                "main_topic": "GPT",
                "decisions": ["внедрить GPT в продукт"],
                "topics": ["GPT"],
                "tasks": [],
            },
        )
        parsed = {
            "search_query": "GPT",
            "focus": "general",
            "sources": ["notes", "transcripts", "summaries"],
        }
        fragments, context = retrieve_journal_context("42", parsed, original_question="GPT")
        types = {f.source_type for f in fragments}
        self.assertIn("note", types)
        self.assertIn("transcript", types)
        self.assertIn("summary", types)
        self.assertIn("GPT", context)

    def test_search_summary_by_transcript(self) -> None:
        self._insert_summary(
            text="📌 Кратко\nКороткое саммари.",
            transcript="Клиент жаловался на скорость загрузки и баги в мобильном приложении",
            meta={
                "content_type": "meeting",
                "main_topic": "Синк",
                "decisions": [],
                "topics": [],
                "tasks": [],
            },
        )
        hits = usage_store.search_summaries("42", "жаловался скорость")
        self.assertEqual(len(hits), 1)

    def test_date_filter(self) -> None:
        self._insert_summary(
            text="📌 Вчера\nСтарые задачи",
            meta={"content_type": "meeting", "main_topic": "A", "tasks": [], "decisions": [], "topics": []},
            when=datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc),
        )
        self._insert_summary(
            text="📌 Сегодня\nНовые задачи",
            meta={"content_type": "meeting", "main_topic": "B", "tasks": [], "decisions": [], "topics": []},
            when=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        )
        parsed = {
            "search_query": "задачи",
            "focus": "general",
            "date_from": "2026-06-17",
            "date_to": "2026-06-17",
            "sources": ["summaries"],
        }
        fragments, _ = retrieve_journal_context("42", parsed)
        self.assertEqual(len(fragments), 1)
        self.assertIn("Новые", fragments[0].text)

    def test_dedup_summary_and_linked_transcript(self) -> None:
        transcript_id = usage_store.insert_usage_event(
            operation="obuchat_transcribe",
            model=None,
            generation_id="t-linked",
            usage={"text": "Полный текст встречи про интеграции", "source": "telegram"},
            telegram_user_id="42",
        )
        self._insert_summary(
            text="📌 Кратко\nИтоги по интеграциям.",
            meta={
                "content_type": "meeting",
                "main_topic": "Интеграции",
                "transcript_event_id": transcript_id,
                "decisions": [],
                "topics": ["интеграции"],
                "tasks": [],
            },
        )
        parsed = {
            "search_query": "интеграции",
            "focus": "general",
            "sources": ["transcripts", "summaries"],
        }
        fragments, _ = retrieve_journal_context("42", parsed)
        transcript_frags = [f for f in fragments if f.source_type == "transcript"]
        summary_frags = [f for f in fragments if f.source_type == "summary"]
        self.assertEqual(len(summary_frags), 1)
        self.assertEqual(len(transcript_frags), 0)

    def test_relative_latest(self) -> None:
        self._insert_summary(
            text="📌 Старая\nСтарое саммари",
            meta={"content_type": "meeting", "main_topic": "Old", "tasks": [], "decisions": [], "topics": []},
            when=datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
        )
        self._insert_summary(
            text="📌 Новая\nПоследняя встреча про GPT",
            meta={
                "content_type": "meeting",
                "main_topic": "GPT sync",
                "tasks": [{"assignee": "", "task": "настроить API", "deadline": ""}],
                "decisions": [],
                "topics": ["GPT"],
            },
            when=datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc),
        )
        parsed = {
            "search_query": "GPT",
            "focus": "tasks",
            "relative": "latest",
            "sources": ["summaries"],
        }
        fragments, _ = retrieve_journal_context("42", parsed)
        self.assertTrue(all("2026-06-17" in (f.date_utc or "") for f in fragments))
        self.assertTrue(any("GPT" in f.headline or "GPT" in f.text for f in fragments))

    def test_build_context_respects_limit(self) -> None:
        from assistant.lib.journal_retrieval import JournalFragment

        frags = [
            JournalFragment(
                source_type="summary",
                item_id=1,
                ts_utc="2026-06-17",
                date_utc="2026-06-17",
                headline="Test",
                text="x" * 5000,
                score=1.0,
            )
        ]
        ctx = build_journal_context_text(frags)
        self.assertLessEqual(len(ctx), 12000)

    def test_journal_qa_heuristic(self) -> None:
        self.assertTrue(
            journal_qa_likely_user_text("Какие задачи были на встрече вчера?")
        )
        self.assertFalse(journal_qa_likely_user_text("встреча завтра в 15:00"))


if __name__ == "__main__":
    unittest.main()
