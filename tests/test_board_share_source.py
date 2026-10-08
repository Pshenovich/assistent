"""Живой документ компании из share-ссылки миниаппа."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from assistant.board.share_source import (
    DEFAULT_SHARE_URL,
    default_company_share_url,
    fetch_share_document,
    looks_like_share_source,
    normalize_share_url,
    parse_share_token,
    resolve_company_share,
    share_body_to_text,
)
from assistant.board import share_source

TOKEN = "TestShareToken_0123456789-ab"
SHARE_URL = f"https://assistent.networ.ru/share/{TOKEN}"


class ShareSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        share_source._cache.clear()
        share_source._stale.clear()
        self._old = os.environ.get("BOARD_COMPANY_SHARE_URL")
        os.environ.pop("BOARD_COMPANY_SHARE_URL", None)

    def tearDown(self) -> None:
        share_source._cache.clear()
        share_source._stale.clear()
        if self._old is None:
            os.environ.pop("BOARD_COMPANY_SHARE_URL", None)
        else:
            os.environ["BOARD_COMPANY_SHARE_URL"] = self._old

    def test_parse_share_url(self) -> None:
        self.assertEqual(parse_share_token(SHARE_URL), TOKEN)
        self.assertTrue(looks_like_share_source(SHARE_URL))
        self.assertFalse(looks_like_share_source("SaaS, 12 клиентов"))

    def test_html_body_to_text(self) -> None:
        text = share_body_to_text("<h3>Речевая аналитика</h3><p>Платформа</p><li>Дашборд</li>")
        self.assertIn("Речевая аналитика", text)
        self.assertIn("Платформа", text)
        self.assertNotIn("<h3>", text)

    def test_fetch_uses_public_api(self) -> None:
        payload = {"title": "Все продукты", "body": "<p>Тариф Pro</p>", "updated_at": "2026-09-07"}
        with patch.object(share_source, "_fetch_http", return_value=payload) as http:
            doc = fetch_share_document(SHARE_URL)
        http.assert_called_once()
        self.assertEqual(doc["title"], "Все продукты")
        self.assertIn("Тариф Pro", doc["text"])
        self.assertFalse(doc["stale"])

    def test_resolve_from_env(self) -> None:
        os.environ["BOARD_COMPANY_SHARE_URL"] = SHARE_URL
        payload = {"title": "Все продукты", "body": "каталог"}
        with patch.object(share_source, "_fetch_http", return_value=payload):
            url, doc, err = resolve_company_share("")
        self.assertTrue(url.endswith(f"/share/{TOKEN}"))
        self.assertIsNone(err)
        self.assertIn("каталог", doc["text"])

    def test_resolve_without_config_is_disabled(self) -> None:
        with patch.object(share_source, "_fetch_http") as http:
            self.assertEqual(resolve_company_share(""), ("", None, None))
        http.assert_not_called()
        self.assertEqual(DEFAULT_SHARE_URL, "")
        self.assertEqual(default_company_share_url(), "")

    def test_normalize_keeps_host(self) -> None:
        self.assertEqual(normalize_share_url(SHARE_URL + "?x=1"), SHARE_URL)
