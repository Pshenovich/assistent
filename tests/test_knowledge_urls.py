"""Тесты ссылок в строке «Источник» для базы знаний."""

from __future__ import annotations

import unittest

from assistant.lib.knowledge_retrieval import KbFragment
from assistant.lib.knowledge_urls import (
    format_kb_sources_footer_html,
    resolve_doc_url,
    strip_kb_source_line,
)


def _sample_fragment(**overrides) -> KbFragment:
    base = {
        "kb_id": "kb1",
        "kb_title": "База знаний ОП",
        "kb_source_url": "https://obuchat.bitrix24.ru/knowledge/baza_op/",
        "kb_source_type": "bitrix24_knowledge",
        "doc_id": "dostupy",
        "doc_title": "Доступы",
        "doc_url": "https://obuchat.bitrix24.ru/knowledge/baza_op/dostupy/",
        "heading": "Доступы",
        "text": "текст",
        "chunk_index": 0,
        "score": 5.0,
    }
    base.update(overrides)
    return KbFragment(**base)


class KnowledgeUrlsTests(unittest.TestCase):
    def test_resolve_bitrix_doc_url(self) -> None:
        url = resolve_doc_url(
            source_type="bitrix24_knowledge",
            kb_source_url="https://obuchat.bitrix24.ru/knowledge/baza_znaniy/",
            doc_id="instrukciya_po_kalendaru",
        )
        self.assertEqual(
            url,
            "https://obuchat.bitrix24.ru/knowledge/baza_znaniy/instrukciya_po_kalendaru/",
        )

    def test_format_footer_html_with_links(self) -> None:
        footer = format_kb_sources_footer_html([_sample_fragment()])
        self.assertIn('<a href="https://obuchat.bitrix24.ru/knowledge/baza_op/">', footer)
        self.assertIn("База знаний ОП", footer)
        self.assertIn('<a href="https://obuchat.bitrix24.ru/knowledge/baza_op/dostupy/">', footer)
        self.assertIn("Доступы", footer)

    def test_strip_llm_source_line(self) -> None:
        text = "Ответ по теме.\n\nИсточник: База знаний ОП | Документ: Доступы | Доступы"
        self.assertEqual(strip_kb_source_line(text), "Ответ по теме.")


if __name__ == "__main__":
    unittest.main()
