"""Follow-up и разбор дедлайнов."""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from assistant.board import store
from assistant.board.followup import parse_deadline, schedule_followups


class BoardFollowupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        store.reset_connection()
        store.init_db()

    def tearDown(self) -> None:
        store.reset_connection()
        self._tmp.cleanup()

    def test_parse_iso_and_relative(self) -> None:
        dt = parse_deadline("2026-09-20")
        self.assertEqual(dt.day, 20)
        rel = parse_deadline("через 2 недели")
        self.assertIsNotNone(rel)
        self.assertGreater(rel, datetime.now(timezone.utc))

    def test_schedule_when_kpi_present(self) -> None:
        m = store.create_meeting(user_id=1, chat_id=3, question="q")
        d = store.save_decision(
            meeting_id=m["id"],
            payload={
                "problem": "p",
                "decision": "тест цены",
                "kpis": ["conversion"],
                "actions": [
                    {
                        "action": "тест",
                        "owner": "P",
                        "deadline": (datetime.now(timezone.utc) + timedelta(days=10)).date().isoformat(),
                        "success_metric": "ARPA",
                    }
                ],
            },
            chat_id="3",
            user_id="1",
        )
        fu = schedule_followups(d["id"])
        self.assertIsNotNone(fu)
        self.assertEqual(fu["status"], "pending")

    def test_no_followup_without_kpi(self) -> None:
        m = store.create_meeting(user_id=1, chat_id=3, question="q")
        d = store.save_decision(
            meeting_id=m["id"],
            payload={"problem": "p", "decision": "ничего", "kpis": []},
            chat_id="3",
        )
        self.assertIsNone(schedule_followups(d["id"]))
