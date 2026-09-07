"""Поиск прошлых решений."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.board import store
from assistant.board.memory import find_related_decisions, history_mentions_past


class BoardMemoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        store.reset_connection()
        store.init_db()

    def tearDown(self) -> None:
        store.reset_connection()
        self._tmp.cleanup()

    def test_detect_history_question(self) -> None:
        self.assertTrue(history_mentions_past("Мы уже обсуждали повышение цены?"))
        self.assertFalse(history_mentions_past("Стоит ли нанять двух разработчиков?"))

    def test_find_related(self) -> None:
        m = store.create_meeting(user_id=1, chat_id=5, question="price")
        store.save_decision(
            meeting_id=m["id"],
            payload={"problem": "Повышение цены продукта", "decision": "Тест 80k"},
            chat_id="5",
        )
        found = find_related_decisions(5, "повышение цены", limit=3)
        self.assertEqual(len(found), 1)
        self.assertIn("80k", found[0]["decision"])
