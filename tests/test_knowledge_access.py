"""Тесты улучшенного поиска и прямых ответов по базе знаний."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.lib.kb_search import resolve_kb_name_and_search_query
from assistant.lib.knowledge_retrieval import (
    format_kb_direct_answer,
    retrieve_kb_context,
    should_quote_kb_context_directly,
)
from assistant.stores import knowledge_base_store as kb_store


class KnowledgeRetrievalAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["KNOWLEDGE_DB_PATH"] = str(Path(self._tmp.name) / "kb.sqlite")
        kb_store._CONN = None  # type: ignore[attr-defined]
        kb = kb_store.create_knowledge_base(
            owner_telegram_user_id=42,
            title="База знаний ОП",
            source_type="bitrix24_knowledge",
            source_url="https://obuchat.bitrix24.ru/knowledge/baza_op/",
        )
        self.kb_id = str(kb["id"])
        kb_store.replace_kb_index(
            self.kb_id,
            documents=[
                {
                    "doc_id": "dostupy",
                    "title": "Доступы",
                    "content_hash": "x",
                    "modified_at": None,
                    "url": "https://obuchat.bitrix24.ru/knowledge/baza_op/dostupy/",
                }
            ],
            chunks=[
                {
                    "doc_id": "dostupy",
                    "heading": "Доступы",
                    "text": (
                        "Тестовый кабинет умный анализ, testcabinet, "
                        "#^:5WH73uMuUg;M, https://platform.obuchat.me/login, "
                        "после логина: https://analysis.obuchat.me/"
                    ),
                    "chunk_index": 0,
                },
                {
                    "doc_id": "dostupy",
                    "heading": "Другие сервисы",
                    "text": "Прочие доступы отдела продаж.",
                    "chunk_index": 1,
                },
            ],
            aggregate_hash="agg",
        )

    def tearDown(self) -> None:
        kb_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()
        os.environ.pop("KNOWLEDGE_DB_PATH", None)

    def test_resolve_kb_name_op(self) -> None:
        kb_name, search_query = resolve_kb_name_and_search_query(
            "найди в базе знаний ОП доступы в тестовый кабинет",
            knowledge_bases=[{"id": self.kb_id, "title": "База знаний ОП"}],
        )
        self.assertEqual(kb_name, "оп")
        self.assertIn("доступ", search_query.lower())
        self.assertIn("кабинет", search_query.lower())

    def test_retrieve_expands_document_chunks(self) -> None:
        frags, _ctx = retrieve_kb_context(
            42,
            {"search_query": "доступы в тестовый кабинет", "kb_name": "оп"},
            original_question="найди в базе знаний ОП доступы в тестовый кабинет",
        )
        self.assertGreaterEqual(len(frags), 2)
        joined = "\n".join(f.text for f in frags)
        self.assertIn("testcabinet", joined)
        self.assertIn("platform.obuchat.me/login", joined)

    def test_direct_answer_contains_credentials(self) -> None:
        question = "найди в базе знаний ОП доступы в тестовый кабинет"
        self.assertTrue(should_quote_kb_context_directly(question))
        frags, _ctx = retrieve_kb_context(
            42,
            {"search_query": "доступы в тестовый кабинет", "kb_name": "оп"},
            original_question=question,
        )
        answer = format_kb_direct_answer(frags)
        self.assertIn("testcabinet", answer)
        self.assertIn("#^:5WH73uMuUg;M", answer)
        self.assertIn("analysis.obuchat.me", answer)


if __name__ == "__main__":
    unittest.main()
