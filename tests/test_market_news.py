"""Выпуск «Рынок» под организацию: сборка без сети (RSS подменён), отбор по конкурентам, хранение, отдача."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from assistant.services import market_news, market_settings
from assistant.services.market_news import generator
from assistant.stores import notes as notes_store
from assistant.stores import orgs_store

NOW = datetime(2026, 10, 29, 5, 0, tzinfo=timezone.utc)


def rss(items):
    body = "".join(
        f"<item><title>{t}</title><link>https://ex.com/{i}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(NOW - timedelta(hours=h))}</pubDate></item>"
        for i, (t, d, h) in enumerate(items)
    )
    return f"<rss><channel>{body}</channel></rss>".encode()


class MarketNewsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = os.path.join(self._tmp.name, "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        generator._POOL.clear()

        def fake_fetch(url, timeout=20):
            if "Gong" in url or "Clari" in url:
                return rss([("Gong запускает AI-агента для продаж", "Gong добавил агента", 5),
                            ("Clari купила стартап", "сделка Clari", 10)])
            if "own.example" in url:
                return rss([("Наш отраслевой обзор", "про звонки", 3)])
            return rss([("Погода в Москве", "дождь", 2), ("Старая новость про Gong", "Gong", 300)])

        self._p = [
            mock.patch.object(generator, "fetch_url", fake_fetch),
            mock.patch.object(generator, "editorial", lambda name, st, top: {
                "summary": [f"{top[0].title} — главное для {name}"], "ideas": ["Идея"], "signals": ["Сигнал"]}),
            mock.patch.object(orgs_store, "verified_emails", lambda uid: []),
        ]
        for p in self._p:
            p.start()

    def tearDown(self) -> None:
        for p in self._p:
            p.stop()
        self._tmp.cleanup()
        notes_store._CONN = None  # type: ignore[attr-defined]

    def test_feeds_from_settings(self) -> None:
        feeds = generator.org_feeds({"competitors": ["Gong", "Clari"], "topics": ["речевая аналитика"],
                                     "sources": ["https://own.example/rss"]})
        names = [f["name"] for f in feeds]
        self.assertIn("Google News: Gong OR Clari", names)
        self.assertIn('Google News: "речевая аналитика"', names)
        self.assertEqual(feeds[-1]["category"], "own")

    def test_build_issue_ranks_competitors_and_renders(self) -> None:
        org = {"id": 1, "name": "Акме"}
        st = {**market_settings.DEFAULTS, "competitors": ["Gong", "Clari"], "sources": ["https://own.example/rss"]}
        res = generator.build_issue(org, st, now=NOW)
        page = res["html"]
        self.assertEqual(res["date"], "2026-10-29")
        self.assertIn("Акме: отраслевой дайджест за 29.10.2026", page)
        self.assertIn("Идеи для Акме", page)
        self.assertIn("Gong запускает AI-агента для продаж — главное для Акме", page)  # редакция Лео
        self.assertLess(page.index("Gong запускает"), page.index("Погода в Москве") if "Погода в Москве" in page else 10**9)
        self.assertNotIn("Старая новость про Gong", page)  # старше 96 часов
        day, items = market_settings.issue_digest(page)  # в Telegram уйдёт «Главное»
        self.assertEqual(day, "2026-10-29")
        self.assertTrue(items)

    def test_competitor_name_needs_business_context(self) -> None:
        arts = [
            generator.Article("Gong cha opens first store in UAE", "https://a/1", "x", "competitors", "en", "bubble tea"),
            generator.Article("Gong launches AI agent for sales teams", "https://a/2", "x", "competitors", "en", ""),
            generator.Article("Погода: дождь и ветер", "https://a/3", "x", "global_ai", "ru", ""),
        ]
        generator.score(arts, high=["Gong", "речевая аналитика"], medium=[], competitors=["Gong"], now=NOW)
        self.assertEqual([a.score > 0 for a in arts], [False, True, False])

    def test_store_due_and_page_for_user(self) -> None:
        org = orgs_store.create_org(1, "Акме")
        self.assertEqual(market_news.due_orgs(NOW), [])  # без конкурентов/тем — общий выпуск
        market_settings.save(1, org["id"], {"competitors": "Gong", "time": "08:00"})
        early = datetime(2026, 10, 29, 4, 30, tzinfo=timezone.utc)  # 07:30 МСК — рано (сборка с 07:40)
        self.assertEqual(market_news.due_orgs(early), [])
        self.assertEqual(market_news.due_orgs(NOW), [org["id"]])  # 08:00 МСК
        market_news.build_for_org(org["id"], now=NOW)
        self.assertEqual(market_news.due_orgs(NOW), [])  # уже собран
        page = market_news.page_for_user(1)
        self.assertEqual(page["day"], "2026-10-29")
        self.assertIn("Акме", page["html"])
        self.assertIsNone(market_news.page_for_user(2))  # не в организации


if __name__ == "__main__":
    unittest.main()
