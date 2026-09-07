"""SQLite-стор Executive Board."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.board import store


class BoardStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        store.reset_connection()
        store.init_db()

    def tearDown(self) -> None:
        store.reset_connection()
        self._tmp.cleanup()

    def test_meeting_and_messages(self) -> None:
        company = store.get_or_create_company_for_chat(1, "Acme")
        store.upsert_company_context(company["id"], "SaaS, 12 клиентов")
        store.upsert_user_context(7, "CEO")
        m = store.create_meeting(
            user_id=7, chat_id=1, question="Поднять цену?", company_id=company["id"]
        )
        self.assertEqual(m["status"], "CREATED")
        store.add_message(meeting_id=m["id"], agent="P", content="Считать ARPA", round=1)
        msgs = store.list_messages(m["id"])
        self.assertEqual(len(msgs), 1)
        active = store.get_active_meeting(1)
        self.assertEqual(active["id"], m["id"])
        store.set_meeting_status(m["id"], "COMPLETED")
        self.assertIsNone(store.get_active_meeting(1))

    def test_decision_search(self) -> None:
        m = store.create_meeting(user_id=1, chat_id=99, question="pricing")
        store.save_decision(
            meeting_id=m["id"],
            payload={
                "problem": "Повышение цены",
                "decision": "Запустить тест 80k",
                "why": ["мало данных"],
                "kpis": ["conversion"],
                "actions": [
                    {
                        "action": "A/B тест",
                        "owner": "P",
                        "deadline": "2026-09-20",
                        "success_metric": "ARPA",
                    }
                ],
            },
            chat_id="99",
            user_id="1",
        )
        found = store.search_decisions(99, "цены")
        self.assertTrue(found)
        self.assertIn("тест", found[0]["decision"].lower())
        self.assertEqual(len(store.list_actions(found[0]["id"])), 1)

    def test_company_documents(self) -> None:
        company = store.get_or_create_company_for_chat(3, "Acme")
        store.upsert_company_context(company["id"], "заметка")
        store.add_company_document(
            company_id=company["id"],
            filename="products.md",
            kind="products",
            text="Pro 80k / Start 20k",
        )
        pack = store.get_company_pack(company["id"])
        self.assertIsNotNone(pack)
        docs = pack["_documents"]
        self.assertEqual(len(docs), 1)
        self.assertIn("Pro 80k", docs[0]["text"])
        ctx = store.get_company_context(company["id"])
        self.assertEqual(ctx["raw_text"], "заметка")
        self.assertIn("Pro 80k", ctx.get("products") or "")
        n = store.delete_company_documents(company["id"])
        self.assertEqual(n, 1)
        self.assertEqual(store.list_company_documents(company["id"]), [])
