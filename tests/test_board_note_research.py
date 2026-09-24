"""Research по заметке: JSON → markdown, идентичность, web plugin."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from assistant.board.note_research import (
    begin_job,
    format_research_comment,
    get_job,
    parse_research_payload,
    recover_jobs_after_restart,
    run_note_research,
)
from assistant.nlu import llm
from assistant.stores import notes as notes_store
from assistant.stores import share_comments
from assistant.board import store


class ResearchFormatTest(unittest.TestCase):
    def test_format_links_and_weak_findings(self) -> None:
        text = format_research_comment(
            {
                "question": "цена конкурента",
                "findings": [
                    {
                        "claim": "Тариф от 990 ₽",
                        "source_title": "Сайт",
                        "source_url": "https://example.com/price",
                        "confidence": 0.8,
                    },
                    {
                        "claim": "Слух без ссылки",
                        "source_title": "",
                        "source_url": "",
                        "confidence": 0.1,
                    },
                    {
                        "claim": "javascript:alert(1)",
                        "source_title": "bad",
                        "source_url": "javascript:alert(1)",
                        "confidence": 0.9,
                    },
                ],
                "contradictions": ["на другом сайте 1200 ₽"],
                "unknowns": ["скидка для SMB"],
                "so_what": "Клиент сравнивает нас с более дешёвым входом.",
                "next_queries": ["конкурент прайс 2026"],
                "ready_for_paie": True,
            }
        )
        self.assertIn("Research · справка", text)
        self.assertIn("Запрос: цена конкурента", text)
        self.assertIn("[Сайт](https://example.com/price)", text)
        self.assertIn("слабый: Слух без ссылки", text)
        self.assertIn("слабый: javascript:alert(1)", text)
        self.assertNotIn("javascript:alert(1)", text.split("слабый:")[0])
        self.assertIn("Противоречия", text)
        self.assertIn("Не нашли", text)
        self.assertIn("Что это значит", text)
        self.assertIn("Можно отдавать в PAIE", text)

    def test_empty_web_goes_to_unknowns(self) -> None:
        payload = parse_research_payload(
            json.dumps(
                {
                    "question": "рынок",
                    "findings": [],
                    "contradictions": [],
                    "unknowns": ["в открытом вебе нет свежих цифр"],
                    "so_what": "Фактов мало, решать рано.",
                    "next_queries": ["рынок 2026 отчёт"],
                    "ready_for_paie": False,
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(payload["findings"], [])
        self.assertIn("в открытом вебе нет свежих цифр", payload["unknowns"])
        text = format_research_comment(payload)
        self.assertIn("Не нашли", text)
        self.assertNotIn("Факты\n", text + "\n")

    def test_invalid_json_fallback(self) -> None:
        payload = parse_research_payload("это не json")
        self.assertEqual(payload["findings"], [])
        self.assertTrue(payload["unknowns"])
        self.assertFalse(payload["ready_for_paie"])


class ResearchJobTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        os.environ["NOTES_DB_PATH"] = str(Path(self._tmp.name) / "notes.sqlite")
        os.environ["RESEARCH_JOBS_PATH"] = str(Path(self._tmp.name) / "research_jobs.json")
        notes_store._CONN = None  # type: ignore[attr-defined]
        store.reset_connection()
        store.init_db()
        from assistant.board import note_research as note_research_mod

        note_research_mod._jobs.clear()

    async def asyncTearDown(self) -> None:
        store.reset_connection()
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_running_job_survives_restart_as_error(self) -> None:
        from assistant.board import note_research as note_research_mod

        job, started = begin_job(7, "local", 42)
        self.assertTrue(started)
        self.assertEqual(job["status"], "running")
        note_research_mod._jobs.clear()
        self.assertIsNone(get_job(7, "local", 42))
        recover_jobs_after_restart()
        recovered = get_job(7, "local", 42)
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered["status"], "error")
        self.assertIn("перезапуска", recovered.get("error") or "")

    def test_run_writes_research_comment_not_paie(self) -> None:
        from assistant.nlu import llm as nlu_llm

        note = notes_store.create_note(3, "Конкурент", "Сравниваем с Acme.")
        original = nlu_llm.research_with_web
        nlu_llm.research_with_web = lambda *_a, **_k: json.dumps(  # type: ignore[method-assign]
            {
                "question": "Acme цена",
                "findings": [
                    {
                        "claim": "Acme берёт 15$",
                        "source_title": "Acme",
                        "source_url": "https://acme.test/pricing",
                        "confidence": 0.7,
                    }
                ],
                "contradictions": [],
                "unknowns": [],
                "so_what": "Их вход дешевле.",
                "next_queries": [],
                "ready_for_paie": True,
            }
        )
        try:
            result = run_note_research(
                user_id=3,
                kind="local",
                item_id=note["id"],
                reply="сколько стоит Acme?",
            )
        finally:
            nlu_llm.research_with_web = original  # type: ignore[method-assign]
        comment = result["comment"]
        self.assertTrue(share_comments.is_research_comment(comment))
        self.assertFalse(share_comments.is_paie_comment(comment))
        self.assertFalse(share_comments.is_gpt_comment(comment))
        self.assertIn("https://acme.test/pricing", comment["body"])
        listed = share_comments.list_comments(3, "local", note["id"])
        self.assertEqual(share_comments.paie_thread(listed), [])


def test_research_with_web_sends_openrouter_web_plugin(monkeypatch) -> None:
    seen: dict = {}

    def fake_complete(payload, **kwargs):
        seen["payload"] = payload
        seen["timeout"] = kwargs.get("timeout")
        return {"choices": [{"message": {"content": '{"question":"x"}'}}]}

    monkeypatch.setattr(llm, "openrouter_chat_completion", fake_complete)
    out = llm.research_with_web("SYS", "найди факты")
    assert "question" in out
    assert seen["payload"]["plugins"] == [{"id": "web"}]
    assert float(seen["timeout"] or 0) >= 180
