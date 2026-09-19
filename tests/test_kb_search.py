"""Тесты поиска по базе знаний (русская морфология)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.lib.kb_search import (
    extract_kb_search_query,
    extract_note_search_query,
    normalize_kb_name_filter,
    note_query_content_words,
    resolve_kb_name_and_search_query,
    score_note_against_query,
    score_query_against_haystack,
    word_matches_haystack,
)
from assistant.stores import knowledge_base_store as kb_store


class KbSearchTests(unittest.TestCase):
    def test_extract_search_query(self) -> None:
        q = extract_kb_search_query("найди в базе знаний инструкцию по календарю")
        self.assertIn("инструк", q.lower())
        self.assertIn("календар", q.lower())

    def test_russian_word_forms(self) -> None:
        hay = "инструкция по календарю google calendar"
        self.assertTrue(word_matches_haystack("инструкцию", hay))
        self.assertTrue(word_matches_haystack("календарю", hay))
        self.assertTrue(word_matches_haystack("календарь", hay))

    def test_kb_name_stopwords(self) -> None:
        self.assertEqual(normalize_kb_name_filter("знаний"), "")

    def test_resolve_kb_name_from_title(self) -> None:
        kb_name, search_query = resolve_kb_name_and_search_query(
            "найди в базе знаний ОП доступы в тестовый кабинет",
            knowledge_bases=[{"id": "1", "title": "База знаний ОП"}],
        )
        self.assertEqual(kb_name, "оп")
        self.assertIn("доступ", search_query.lower())

    def test_score_calendar_instruction(self) -> None:
        hay = "Инструкция по подключению Google Calendar в Leo"
        score = score_query_against_haystack(
            "инструкцию по календарю",
            hay,
        )
        self.assertGreaterEqual(score, 2.0)

    def test_extract_note_search_query(self) -> None:
        q = extract_note_search_query("найди в заметках про филиалы в Куркино")
        self.assertIn("филиал", q.lower())
        self.assertIn("куркино", q.lower())
        self.assertNotIn("найди", q.lower())

    def test_note_content_words_drop_stopwords(self) -> None:
        words = note_query_content_words("найди заметку про филиалы куркино")
        self.assertIn("филиалы", words)
        self.assertIn("куркино", words)
        self.assertNotIn("найди", words)
        self.assertNotIn("заметку", words)
        self.assertNotIn("про", words)

    def test_score_note_title_beats_body_noise(self) -> None:
        good = score_note_against_query(
            "филиалы куркино",
            "Филиалы в Куркино",
            "Адрес",
        )
        noisy = score_note_against_query(
            "филиалы куркино",
            "План встреч",
            "Встречи на этой неделе",
        )
        self.assertGreater(good, 0)
        self.assertEqual(noisy, 0)


class KbStoreSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["KNOWLEDGE_DB_PATH"] = str(Path(self._tmp.name) / "kb.sqlite")
        kb_store._CONN = None  # type: ignore[attr-defined]
        kb = kb_store.create_knowledge_base(
            owner_telegram_user_id=1,
            title="Corp",
            source_type="bitrix24_knowledge",
            source_url="https://x.bitrix24.ru/knowledge/test/",
        )
        self.kb_id = str(kb["id"])
        kb_store.replace_kb_index(
            self.kb_id,
            documents=[
                {
                    "doc_id": "calendar",
                    "title": "Календарь",
                    "content_hash": "x",
                    "modified_at": None,
                }
            ],
            chunks=[
                {
                    "doc_id": "calendar",
                    "heading": "Инструкция",
                    "text": "Инструкция по подключению Google Calendar и работе с календарём.",
                    "chunk_index": 0,
                }
            ],
            aggregate_hash="agg",
        )

    def tearDown(self) -> None:
        kb_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()
        os.environ.pop("KNOWLEDGE_DB_PATH", None)

    def test_search_instruction_calendar(self) -> None:
        hits = kb_store.search_kb_chunks(
            [self.kb_id],
            "инструкцию по календарю",
        )
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
