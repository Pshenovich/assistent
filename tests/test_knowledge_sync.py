"""Тесты chunking и URL-парсинга knowledge sync."""

from __future__ import annotations

import unittest

from assistant.integrations import knowledge_google_docs, knowledge_notion
from assistant.services import knowledge_sync


class KnowledgeSyncTests(unittest.TestCase):
    def test_chunk_text_splits_long_body(self) -> None:
        body = "А" * 2500
        chunks = knowledge_sync.chunk_text(body, doc_title="Doc")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c["text"]) <= 1200 for c in chunks))

    def test_chunk_text_preserves_heading(self) -> None:
        text = "# Интеграции\n\nОписание API.\n\n# Регламенты\n\nСписок должностей."
        chunks = knowledge_sync.chunk_text(text, doc_title="KB")
        headings = {c["heading"] for c in chunks}
        self.assertIn("Интеграции", headings)
        self.assertIn("Регламенты", headings)

    def test_google_doc_id_parser(self) -> None:
        doc_id = knowledge_google_docs.parse_google_doc_id(
            "https://docs.google.com/document/d/AbC123_xYz/edit"
        )
        self.assertEqual(doc_id, "AbC123_xYz")

    def test_notion_page_id_parser(self) -> None:
        page_id = knowledge_notion.parse_notion_page_id(
            "https://www.notion.so/my/Title-abcdef0123456789abcdef0123456789"
        )
        self.assertIsNotNone(page_id)
        self.assertIn("-", page_id or "")


if __name__ == "__main__":
    unittest.main()
