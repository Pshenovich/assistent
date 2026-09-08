"""PAIE-разбор заметки → комментарий CHAIR."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.board.note_paei import (
    begin_job,
    format_chair_comment,
    get_job,
    load_note_for_paei,
    run_note_paei,
)
from assistant.stores import notes as notes_store
from assistant.stores import share_comments
from assistant.board import store


class _ScriptedProvider:
    def generate_json(self, *, system: str, user: str, operation: str, temperature: float = 0.4):
        del system, user, temperature
        if operation == "board_note_paei":
            return {
                "problem": "Заметка про запуск",
                "decision": "Запустить пилот на 2 недели, затем решить о масштабе.",
                "why": ["мало данных для полного запуска"],
                "actions": [{"action": "Собрать KPI пилота", "owner": "P", "deadline": "", "success_metric": ""}],
                "kpis": ["конверсия"],
                "risks": ["затянем решение"],
                "do_not_do": ["масштабировать сразу"],
                "confidence": 0.8,
                "paei": {"P": "сначала метрика", "A": "риск хаоса", "E": "пилот", "I": "не пугать команду"},
            }
        if operation == "board_analyze":
            return {
                "problem": "Что делать с заметкой",
                "decision_required": "Решение по заметке",
                "known_facts": ["есть текст"],
                "assumptions": [],
                "missing_information": [],
                "decision_type": "general",
                "severity": "LOW",
                "reversibility": "REVERSIBLE",
                "title": "Заметка",
            }
        if operation.startswith("board_agent_"):
            agent = operation.split("_")[2].upper()
            return {
                "agent": agent,
                "position": f"{agent} считает, что нужен пилот на 2 недели, а не сразу масштабировать.",
                "responding_to": ["заметка"],
                "agreement": [],
                "disagreement": ["нельзя запускать всё сразу"],
                "new_arguments": ["сначала метрика"],
                "changed_position": False,
                "proposal": "Пилот 2 недели",
                "confidence": 0.7,
                "needs_user_input": False,
                "claims": [],
            }
        if operation == "board_orchestrator":
            return {
                "continue_debate": False,
                "next_agent": "P",
                "reason": "достаточно",
                "mode": "SYNTHESIS",
                "consensus_score": 0.7,
                "agreement_rate": 0.6,
                "unresolved": [],
                "scores": {},
            }
        if operation == "board_round_summary":
            return {
                "summary": "пилот",
                "consensus": "пилот",
                "disagreements": "",
                "open_questions": "",
                "assumptions": "",
                "agreement_rate": 0.6,
            }
        if operation == "board_chair":
            return {
                "problem": "Заметка про запуск",
                "decision": "Запустить пилот на 2 недели, затем решить о масштабе.",
                "why": ["мало данных для полного запуска"],
                "actions": [{"action": "Собрать KPI пилота", "owner": "P", "deadline": "", "success_metric": ""}],
                "kpis": ["конверсия"],
                "risks": ["затянем решение"],
                "assumptions": [],
                "open_questions": [],
                "do_not_do": ["масштабировать сразу"],
                "confidence": 0.8,
                "unavailable_agents": [],
            }
        raise AssertionError(operation)


class _CaptureProvider(_ScriptedProvider):
    def __init__(self) -> None:
        self.users: list[str] = []

    def generate_json(self, *, system: str, user: str, operation: str, temperature: float = 0.4):
        self.users.append(str(user or ""))
        return super().generate_json(
            system=system, user=user, operation=operation, temperature=temperature
        )


class NotePaeiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        os.environ["NOTES_DB_PATH"] = str(Path(self._tmp.name) / "notes.sqlite")
        os.environ["BOARD_DELAY_MIN_SEC"] = "0"
        os.environ["BOARD_DELAY_MAX_SEC"] = "0"
        os.environ["BOARD_MAX_MEETING_SEC"] = "120"
        os.environ["BOARD_EXTRA_ROUNDS"] = "0"
        os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
        notes_store._CONN = None  # type: ignore[attr-defined]
        store.reset_connection()
        store.init_db()
        from assistant.board import note_paei as note_paei_mod

        note_paei_mod._jobs.clear()

    async def asyncTearDown(self) -> None:
        store.reset_connection()
        notes_store._CONN = None  # type: ignore[attr-defined]
        self._tmp.cleanup()

    def test_public_job_error_strips_nginx_html(self) -> None:
        from assistant.board.note_paei import _public_job_error

        html = (
            "<html> <head><title>502 Bad Gateway</title></head> "
            "<body><center><h1>502 Bad Gateway</h1></center></body></html>"
        )
        msg = _public_job_error(RuntimeError(html))
        self.assertNotIn("<html", msg.lower())
        self.assertIn("временно недоступен", msg)
        self.assertEqual(
            _public_job_error(RuntimeError("модель не ответила")),
            "модель не ответила",
        )

    def test_format_chair_comment(self) -> None:
        text = format_chair_comment(
            {
                "decision": "Не масштабировать сразу",
                "why": ["мало данных"],
                "actions": [{"action": "Пилот"}],
                "kpis": ["ARPA"],
                "confidence": 0.84,
            }
        )
        self.assertIn("PAIE · решение CHAIR", text)
        self.assertIn("Не масштабировать", text)
        self.assertIn("84%", text)

    def test_format_includes_paei_takes(self) -> None:
        text = format_chair_comment(
            {
                "decision": "Пилот",
                "paei": {"P": "KPI сначала", "E": "эксперимент"},
            }
        )
        self.assertIn("P: KPI сначала", text)
        self.assertIn("E: эксперимент", text)

    def test_load_local_note(self) -> None:
        note = notes_store.create_note(7, "Продукты", "<p>Тариф Pro</p>")
        title, body = load_note_for_paei(7, "local", note["id"])
        self.assertEqual(title, "Продукты")
        self.assertIn("Тариф Pro", body)
        self.assertNotIn("<p>", body)

    def test_begin_job_idempotent_while_running(self) -> None:
        first, started = begin_job(1, "local", 9)
        self.assertTrue(started)
        self.assertEqual(first["status"], "running")
        second, started2 = begin_job(1, "local", 9)
        self.assertFalse(started2)
        self.assertEqual(get_job(1, "local", 9)["status"], "running")

    def test_begin_job_restarts_stale(self) -> None:
        first, started = begin_job(1, "local", 4)
        self.assertTrue(started)
        job = get_job(1, "local", 4)
        assert job is not None
        from assistant.board import note_paei as note_paei_mod

        note_paei_mod._jobs[note_paei_mod.job_key(1, "local", 4)]["started_at"] = 1
        again, started2 = begin_job(1, "local", 4)
        self.assertTrue(started2)
        self.assertEqual(again["status"], "running")

    async def test_run_writes_chair_comment(self) -> None:
        note = notes_store.create_note(3, "Запуск", "Хотим сразу масштабировать отдел продаж.")
        result = await run_note_paei(
            user_id=3,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
        )
        comments = share_comments.list_comments(3, "local", note["id"])
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author_name"], "CHAIR")
        self.assertIn("пилот", comments[0]["body"].lower())
        self.assertEqual(result["comment"]["id"], comments[0]["id"])
        self.assertIn("PAIE", comments[0]["body"])
        self.assertEqual(comments[0]["quote"], "")
        self.assertIsNone(comments[0]["parent_id"])

    async def test_progress_callback_during_run(self) -> None:
        note = notes_store.create_note(4, "Пилот", "Нужен пилот, не масштаб.")
        seen: list[str] = []

        def on_progress(payload: dict) -> None:
            seen.append(str(payload.get("phase") or ""))

        await run_note_paei(
            user_id=4,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
            on_progress=on_progress,
        )
        self.assertTrue(any(p in {"ANALYZING", "DISCUSSION", "SYNTHESIS"} for p in seen))

    async def test_followup_reply_threads_to_chair(self) -> None:
        note = notes_store.create_note(5, "Пилот", "Нужен пилот на 2 недели.")
        first = await run_note_paei(
            user_id=5,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
        )
        root_id = first["comment"]["id"]
        share_comments.add_comment(
            5,
            "local",
            note["id"],
            author_user_id=5,
            author_name="Анна",
            body="Можно сразу на месяц?",
            parent_id=root_id,
        )
        second = await run_note_paei(
            user_id=5,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
            reply="Можно сразу на месяц?",
            parent_id=root_id,
        )
        comments = share_comments.list_comments(5, "local", note["id"])
        self.assertEqual(len(comments), 3)
        follow = second["comment"]
        self.assertEqual(follow["parent_id"], root_id)
        self.assertIn("ответ CHAIR", follow["body"])
        self.assertEqual(share_comments.paie_thread_root_id(comments), root_id)
        self.assertEqual(len(share_comments.paie_thread(comments)), 3)

    async def test_followup_reply_to_marks_target_but_chairs_root(self) -> None:
        note = notes_store.create_note(5, "Пилот", "Нужен пилот на 2 недели.")
        first = await run_note_paei(
            user_id=5,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
        )
        root_id = first["comment"]["id"]
        second = await run_note_paei(
            user_id=5,
            kind="local",
            item_id=note["id"],
            provider=_ScriptedProvider(),
            reply="Можно сразу на месяц?",
            parent_id=root_id,
        )
        later_id = second["comment"]["id"]
        cap = _CaptureProvider()
        third = await run_note_paei(
            user_id=5,
            kind="local",
            item_id=note["id"],
            provider=cap,
            reply="Имею в виду KPI из второго ответа",
            parent_id=root_id,
            reply_to_id=later_id,
            quote="пилот на 2 недели",
        )
        self.assertEqual(third["comment"]["parent_id"], root_id)
        blob = "\n".join(cap.users)
        self.assertIn("отвечает именно на это сообщение", blob)
        self.assertIn("Имею в виду KPI из второго ответа", blob)
