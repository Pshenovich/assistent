"""Тесты адаптера базы знаний Bitrix24."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from assistant.integrations import knowledge_bitrix24
from assistant.stores import knowledge_base_store as kb_store


class KnowledgeBitrix24Tests(unittest.TestCase):
    def test_parse_knowledge_url(self) -> None:
        ref = knowledge_bitrix24.parse_bitrix_knowledge_url(
            "https://obuchat.bitrix24.ru/knowledge/baza_znaniy_v_svetloy_teme3/"
        )
        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertEqual(ref.portal_host, "obuchat.bitrix24.ru")
        self.assertEqual(ref.kb_code, "baza_znaniy_v_svetloy_teme3")
        self.assertIsNone(ref.page_code)

    def test_parse_knowledge_page_url(self) -> None:
        ref = knowledge_bitrix24.parse_bitrix_knowledge_url(
            "https://obuchat.bitrix24.ru/knowledge/baza_znaniy_v_svetloy_teme3/reglamenty/"
        )
        self.assertIsNotNone(ref)
        assert ref is not None
        self.assertEqual(ref.page_code, "reglamenty")

    def test_detect_source_type(self) -> None:
        self.assertEqual(
            kb_store.detect_source_type(
                "https://obuchat.bitrix24.ru/knowledge/baza_znaniy_v_svetloy_teme3/"
            ),
            "bitrix24_knowledge",
        )

    def test_blocks_to_text(self) -> None:
        text = knowledge_bitrix24._blocks_to_text(
            [{"content": "<h2>Интеграции</h2><p>Описание API</p>"}]
        )
        self.assertIn("интеграц", text.lower())
        self.assertIn("описание", text.lower())

    def test_kb_site_code_variants(self) -> None:
        variants = knowledge_bitrix24._kb_site_code_variants("baza_znaniy_v_svetloy_teme3")
        self.assertIn("/baza_znaniy_v_svetloy_teme3/", variants)

    @patch("assistant.integrations.knowledge_bitrix24._fetch_page_document")
    @patch("assistant.integrations.knowledge_bitrix24._list_site_pages")
    @patch("assistant.integrations.knowledge_bitrix24._find_knowledge_site")
    @patch("assistant.integrations.knowledge_bitrix24.resolve_rest_webhook")
    def test_fetch_documents(
        self,
        mock_webhook: unittest.mock.MagicMock,
        mock_site: unittest.mock.MagicMock,
        mock_pages: unittest.mock.MagicMock,
        mock_page_doc: unittest.mock.MagicMock,
    ) -> None:
        mock_webhook.return_value = "https://obuchat.bitrix24.ru/rest/1/xxx"
        mock_site.return_value = {"ID": 10, "TITLE": "KB"}
        mock_pages.return_value = [{"ID": 101, "TITLE": "Статья", "CODE": "art"}]
        mock_page_doc.return_value = knowledge_bitrix24.KbFetchedDocument(
            doc_id="art",
            title="Статья",
            text="Текст статьи",
            url="https://obuchat.bitrix24.ru/knowledge/baza_znaniy_v_svetloy_teme3/art/",
        )
        docs = knowledge_bitrix24.fetch_documents(
            "https://obuchat.bitrix24.ru/knowledge/baza_znaniy_v_svetloy_teme3/",
            owner_telegram_user_id=1,
        )
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].title, "Статья")


if __name__ == "__main__":
    unittest.main()
