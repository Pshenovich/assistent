"""Тесты knowledge_base_store."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.stores import knowledge_base_store as kb_store


class KnowledgeBaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db = Path(self._tmp.name) / "kb.sqlite"
        os.environ["KNOWLEDGE_DB_PATH"] = str(self._db)
        kb_store._CONN = None  # type: ignore[attr-defined]

    def tearDown(self) -> None:
        kb_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()
        os.environ.pop("KNOWLEDGE_DB_PATH", None)

    def test_create_acl_and_search(self) -> None:
        kb = kb_store.create_knowledge_base(
            owner_telegram_user_id=1001,
            title="Компания",
            source_type="google_docs",
            source_url="https://docs.google.com/document/d/abc/edit",
        )
        kb_id = str(kb["id"])
        self.assertTrue(kb_store.user_is_kb_owner(1001, kb_id))
        self.assertTrue(kb_store.user_has_accessible_kbs(1001))

        kb_store.replace_kb_index(
            kb_id,
            documents=[
                {
                    "doc_id": "abc",
                    "title": "Регламенты",
                    "content_hash": "h1",
                    "modified_at": None,
                }
            ],
            chunks=[
                {
                    "doc_id": "abc",
                    "heading": "Интеграции",
                    "text": "Список интеграций с CRM и API.",
                    "chunk_index": 0,
                }
            ],
            aggregate_hash="agg1",
        )
        hits = kb_store.search_kb_chunks([kb_id], "интеграции CRM")
        self.assertEqual(len(hits), 1)
        self.assertIn("интеграций", hits[0]["text"].lower())

        self.assertTrue(
            kb_store.add_kb_member(kb_id, member_telegram_user_id=2002, granted_by=1001)
        )
        self.assertTrue(kb_store.user_can_access_kb(2002, kb_id))
        self.assertFalse(kb_store.user_is_kb_owner(2002, kb_id))

        self.assertTrue(
            kb_store.remove_kb_member(
                kb_id, member_telegram_user_id=2002, revoked_by=1001
            )
        )
        self.assertFalse(kb_store.user_can_access_kb(2002, kb_id))

    def test_detect_source_type(self) -> None:
        self.assertEqual(
            kb_store.detect_source_type("https://docs.google.com/document/d/x/edit"),
            "google_docs",
        )
        self.assertEqual(
            kb_store.detect_source_type("https://www.notion.so/team/Page-abc123"),
            "notion",
        )
        self.assertEqual(
            kb_store.detect_source_type("https://disk.yandex.ru/d/abc"),
            "yandex_disk",
        )


if __name__ == "__main__":
    unittest.main()
