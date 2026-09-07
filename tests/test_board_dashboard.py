"""HTML дашборда решений."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.board import store
from assistant.board.dashboard import render_decision, render_index


class BoardDashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        store.reset_connection()
        store.init_db()

    def tearDown(self) -> None:
        store.reset_connection()
        self._tmp.cleanup()

    def test_index_and_card(self) -> None:
        m = store.create_meeting(user_id=1, chat_id=1, question="Цена")
        d = store.save_decision(
            meeting_id=m["id"],
            payload={"problem": "Повышение цены", "decision": "Тест", "kpis": ["ARPA"]},
            chat_id="1",
        )
        html = render_index(board_base="/dashboard/board", qs="")
        self.assertIn("Executive Board", html)
        self.assertIn("Повышение цены", html)
        card = render_decision(d["id"], board_base="/dashboard/board")
        self.assertIsNotNone(card)
        self.assertIn("Тест", card or "")
