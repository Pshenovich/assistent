"""Тесты маршрутизации knowledge_qa."""

from __future__ import annotations

import unittest

from assistant.lib.journal_retrieval import is_journal_archive_query
from assistant.lib.knowledge_retrieval import is_knowledge_base_query
from assistant.nlu.regex import regex_route


class KnowledgeRoutingTests(unittest.TestCase):
    def test_kb_query_routes_to_knowledge_qa(self) -> None:
        route = regex_route("найди в базе инфу про интеграции")
        self.assertIsNotNone(route)
        assert route is not None
        self.assertEqual(route.skill, "knowledge_qa")

    def test_kb_vs_journal_distinction(self) -> None:
        self.assertTrue(is_knowledge_base_query("найди в базе знаний регламенты"))
        self.assertFalse(is_knowledge_base_query("найди в заметках про проект"))
        self.assertTrue(is_journal_archive_query("о чем договорились на встрече вчера"))


if __name__ == "__main__":
    unittest.main()
