"""Тесты knowledge retrieval."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.lib.knowledge_retrieval import (
    is_knowledge_base_query,
    retrieve_kb_context,
)
from assistant.stores import knowledge_base_store as kb_store


class KnowledgeRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["KNOWLEDGE_DB_PATH"] = str(Path(self._tmp.name) / "kb.sqlite")
        kb_store._CONN = None  # type: ignore[attr-defined]
        kb = kb_store.create_knowledge_base(
            owner_telegram_user_id=42,
            title="Corp KB",
            source_type="google_docs",
            source_url="https://docs.google.com/document/d/test/edit",
        )
        self.kb_id = str(kb["id"])
        kb_store.replace_kb_index(
            self.kb_id,
            documents=[
                {
                    "doc_id": "d1",
                    "title": "Регламенты",
                    "content_hash": "x",
                    "modified_at": None,
                }
            ],
            chunks=[
                {
                    "doc_id": "d1",
                    "heading": "Должностные регламенты",
                    "text": "Список должностных регламентов компании.",
                    "chunk_index": 0,
                },
                {
                    "doc_id": "d1",
                    "heading": "Интеграции",
                    "text": "Информация про интеграции с внешними системами.",
                    "chunk_index": 1,
                },
            ],
            aggregate_hash="agg",
        )

    def tearDown(self) -> None:
        kb_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()
        os.environ.pop("KNOWLEDGE_DB_PATH", None)

    def test_query_heuristics(self) -> None:
        self.assertTrue(is_knowledge_base_query("найди в базе инфу про интеграции"))
        self.assertTrue(
            is_knowledge_base_query("найди в базе знаний список должностных регламентов")
        )

    def test_retrieve_context(self) -> None:
        frags, ctx = retrieve_kb_context(
            42,
            {"search_query": "интеграции"},
            original_question="найди в базе инфу про интеграции",
        )
        self.assertGreaterEqual(len(frags), 1)
        self.assertIn("интеграц", ctx.lower())


if __name__ == "__main__":
    unittest.main()
