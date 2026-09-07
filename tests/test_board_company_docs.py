"""Документы компании для контекста агентов."""

from __future__ import annotations

import io
import unittest
import zipfile

from assistant.board.context import _fmt_company
from assistant.board.docs import detect_doc_kind, extract_document_text


class CompanyDocsTest(unittest.TestCase):
    def test_kind_from_name(self) -> None:
        self.assertEqual(detect_doc_kind("products.md", ""), "products")
        self.assertEqual(detect_doc_kind("staff.csv", "команда"), "team")
        self.assertEqual(detect_doc_kind("notes.txt", ""), "general")

    def test_extract_txt(self) -> None:
        text, err = extract_document_text("team.csv", b"name,role\nAnn,CEO\n")
        self.assertIsNone(err)
        self.assertIn("Ann,CEO", text)

    def test_reject_pdf(self) -> None:
        text, err = extract_document_text("x.pdf", b"%PDF-1.4")
        self.assertEqual(text, "")
        self.assertIn("PDF", err or "")

    def test_docx(self) -> None:
        xml = (
            b'<?xml version="1.0"?><w:document '
            b'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p><w:r><w:t>Hello team</w:t></w:r></w:p></w:body></w:document>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("word/document.xml", xml)
        text, err = extract_document_text("team.docx", buf.getvalue())
        self.assertIsNone(err)
        self.assertIn("Hello team", text)

    def test_fmt_includes_docs(self) -> None:
        out = _fmt_company(
            {
                "raw_text": "SaaS B2B",
                "_documents": [
                    {
                        "filename": "products.md",
                        "kind": "products",
                        "text": "Tariff Pro 80k",
                    }
                ],
            }
        )
        self.assertIn("SaaS B2B", out)
        self.assertIn("Tariff Pro 80k", out)

    def test_fmt_live_share_first(self) -> None:
        out = _fmt_company(
            {
                "_documents": [
                    {
                        "filename": "Все продукты",
                        "kind": "live",
                        "text": "Речевая аналитика",
                    }
                ]
            }
        )
        self.assertIn("живой документ", out.lower())
        self.assertIn("Речевая аналитика", out)
