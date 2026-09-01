"""Тесты футера «Источник» для базы знаний."""

from __future__ import annotations

import unittest

from assistant.lib.knowledge_retrieval import KbFragment
from assistant.lib.knowledge_urls import format_kb_sources_footer_html, primary_kb_fragments


class KnowledgeSourceFooterTests(unittest.TestCase):
    def test_primary_fragments_only_top_document(self) -> None:
        frags = [
            KbFragment(
                kb_id="kb1",
                kb_title="База ОП",
                kb_source_url="https://x/knowledge/op/",
                kb_source_type="bitrix24_knowledge",
                doc_id="dostupy",
                doc_title="Доступы",
                doc_url="https://x/knowledge/op/dostupy/",
                heading="",
                text="текст",
                chunk_index=0,
                score=5.0,
            ),
            KbFragment(
                kb_id="kb1",
                kb_title="База ОП",
                kb_source_url="https://x/knowledge/op/",
                kb_source_type="bitrix24_knowledge",
                doc_id="telphin",
                doc_title="Инструкция Телфин",
                doc_url="https://x/knowledge/op/telphin/",
                heading="",
                text="другое",
                chunk_index=0,
                score=2.0,
            ),
        ]
        primary = primary_kb_fragments(frags)
        self.assertEqual(len(primary), 1)
        self.assertEqual(primary[0].doc_title, "Доступы")

    def test_footer_lists_single_document(self) -> None:
        frags = [
            KbFragment(
                kb_id="kb1",
                kb_title="База знаний ОП",
                kb_source_url="https://obuchat.bitrix24.ru/knowledge/op/",
                kb_source_type="bitrix24_knowledge",
                doc_id="dostupy",
                doc_title="Доступы",
                doc_url="https://obuchat.bitrix24.ru/knowledge/op/dostupy/",
                heading="",
                text="текст",
                chunk_index=0,
                score=5.0,
            ),
            KbFragment(
                kb_id="kb1",
                kb_title="База знаний ОП",
                kb_source_url="https://obuchat.bitrix24.ru/knowledge/op/",
                kb_source_type="bitrix24_knowledge",
                doc_id="reglament",
                doc_title="Регламент менеджера",
                doc_url="https://obuchat.bitrix24.ru/knowledge/op/reglament/",
                heading="",
                text="другое",
                chunk_index=0,
                score=1.0,
            ),
        ]
        footer = format_kb_sources_footer_html(frags)
        self.assertIn("Доступы", footer)
        self.assertNotIn("Регламент", footer)
        self.assertNotIn("Телфин", footer)


if __name__ == "__main__":
    unittest.main()
