"""Оркестратор, валидация ответов и сценарий §68 с mock LLM."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from assistant.board import store
from assistant.board.engine import is_shallow_response, parse_agent_response
from assistant.board.meeting import MeetingService
from assistant.board.models import AgentResponse
from assistant.board.orchestrator import (
    heuristic_next_agent,
    limits_hit,
    needs_challenge,
    parse_analysis,
    parse_orchestrator,
)


class _ScriptedProvider:
    def __init__(self) -> None:
        self.ops: list[str] = []

    def generate_json(self, *, system: str, user: str, operation: str, temperature: float = 0.4, **_kwargs):
        del system, temperature
        self.ops.append(operation)
        if operation == "board_analyze":
            return {
                "problem": "Сократить отдел продаж с 12 до 8 при падении продаж на 20%",
                "decision_required": "Решить, сокращать ли headcount и как проверить причину падения",
                "known_facts": ["продажи упали на 20%"],
                "assumptions": ["меньше продавцов = меньше затрат"],
                "missing_information": ["воронка по этапам"],
                "decision_type": "org",
                "severity": "HIGH",
                "reversibility": "PARTIALLY_REVERSIBLE",
                "title": "Сокращение продаж",
            }
        if operation.startswith("board_agent_"):
            agent = operation.split("_")[2].upper()
            if agent not in {"P", "A", "E", "I"}:
                agent = "P"
            mention = "P" if "P:" in user else "исходный запрос"
            return {
                "agent": agent,
                "position": (
                    f"{agent} реагирует на {mention}: нельзя решать headcount в вакууме. "
                    f"Нужны цифры выручки на продавца и качество воронки. Ссылаюсь на коллег."
                ),
                "responding_to": [mention],
                "agreement": ["данные о падении продаж на 20% — факт"],
                "disagreement": ["автоматически резать команду — ошибка постановки"],
                "new_arguments": ["сначала диагностика конверсии"],
                "changed_position": "YOUR PREVIOUS POSITION" in user and "первый ход" not in user,
                "proposal": "2 недели диагностики + эксперимент с квотами, не сокращение сразу",
                "confidence": 0.7,
                "needs_user_input": False,
                "claims": [{"kind": "FACT", "text": "продажи упали на 20%"}],
            }
        if operation == "board_orchestrator":
            agent_turns = sum(1 for o in self.ops if o.startswith("board_agent_") and "retry" not in o)
            if agent_turns < 8:
                return {
                    "continue_debate": True,
                    "next_agent": "P",
                    "reason": "нужен challenge",
                    "mode": "CHALLENGE",
                    "consensus_score": 0.85,
                    "agreement_rate": 0.86,
                    "unresolved": ["причина падения продаж"],
                    "unique_arguments": 4,
                    "position_changes": 1,
                    "scores": {"argument_quality": 0.7, "cross_agent_engagement": 0.8},
                }
            return {
                "continue_debate": False,
                "next_agent": "P",
                "reason": "достаточно",
                "mode": "SYNTHESIS",
                "consensus_score": 0.7,
                "agreement_rate": 0.6,
                "unresolved": [],
                "scores": {"argument_quality": 0.75},
            }
        if operation == "board_round_summary":
            return {
                "summary": "Спор про диагностику vs сокращение",
                "consensus": "данные недостаточны",
                "disagreements": "скорость сокращения",
                "open_questions": "качество лидов",
                "assumptions": "продавцы взаимозаменяемы",
                "agreement_rate": 0.45,
            }
        if operation == "board_chair":
            return {
                "problem": "Падение продаж и идея сократить отдел с 12 до 8",
                "decision": "Не сокращать сразу. 2 недели диагностики воронки, затем решение по headcount.",
                "why": [
                    "нет данных, что проблема в избытке людей",
                    "сокращение необратимо бьёт по покрытию",
                ],
                "actions": [
                    {
                        "action": "Разобрать воронку по этапам",
                        "owner": "P",
                        "deadline": "2026-09-21",
                        "success_metric": "конверсия этап-этап",
                    }
                ],
                "kpis": ["выручка / продавец", "конверсия лид→сделка"],
                "risks": ["пока ищем причину, отток может продолжиться"],
                "assumptions": ["падение связано с воронкой, не только с людьми"],
                "open_questions": ["качество входящих лидов"],
                "do_not_do": ["увольнять четырёх человек на этой неделе"],
                "confidence": 0.81,
                "unavailable_agents": [],
            }
        raise AssertionError(f"unexpected operation {operation}")


class _FakePublisher:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.statuses: list[str] = []

    async def before_agent(self, meeting, agent) -> None:
        return None

    async def publish_text(self, meeting, text, *, markup=None):
        self.texts.append(text)
        return len(self.texts)

    async def update_status(self, meeting, text) -> None:
        self.statuses.append(text)

    async def clear_status(self, meeting) -> None:
        return None


class OrchestratorUnitTest(unittest.TestCase):
    def test_heuristic_and_challenge(self) -> None:
        self.assertEqual(heuristic_next_agent("E", "новая бизнес-модель", []), "P")
        self.assertTrue(needs_challenge(severity="HIGH", agreement_rate=0.2, round_no=1, challenge_done=False))
        self.assertTrue(needs_challenge(severity="LOW", agreement_rate=0.9, round_no=2, challenge_done=False))
        self.assertFalse(needs_challenge(severity="LOW", agreement_rate=0.9, round_no=2, challenge_done=True))

    def test_limits(self) -> None:
        self.assertTrue(limits_hit({"message_count": 16, "current_round": 1, "max_rounds": 4}))

    def test_rounds_for_low_severity(self) -> None:
        from assistant.board.orchestrator import rounds_for_severity

        old = os.environ.get("BOARD_MAX_ROUNDS")
        old_low = os.environ.get("BOARD_MAX_ROUNDS_LOW")
        os.environ["BOARD_MAX_ROUNDS"] = "4"
        os.environ["BOARD_MAX_ROUNDS_LOW"] = "2"
        try:
            self.assertEqual(rounds_for_severity("LOW"), 2)
            self.assertEqual(rounds_for_severity("HIGH"), 4)
        finally:
            if old is None:
                os.environ.pop("BOARD_MAX_ROUNDS", None)
            else:
                os.environ["BOARD_MAX_ROUNDS"] = old
            if old_low is None:
                os.environ.pop("BOARD_MAX_ROUNDS_LOW", None)
            else:
                os.environ["BOARD_MAX_ROUNDS_LOW"] = old_low

    def test_debate_should_stop_on_high_confidence(self) -> None:
        from assistant.board.models import OrchestratorDecision
        from assistant.board.orchestrator import debate_should_stop

        keep = OrchestratorDecision(
            continue_debate=True,
            next_agent="P",
            reason="",
            mode="DISCUSSION",
            consensus_score=0.5,
        )
        self.assertFalse(
            debate_should_stop(keep, {"current_round": 2, "message_count": 8}, challenge_done=True)
        )
        done = OrchestratorDecision(
            continue_debate=True,
            next_agent="P",
            reason="",
            mode="DISCUSSION",
            consensus_score=0.9,
        )
        self.assertTrue(
            debate_should_stop(done, {"current_round": 2, "message_count": 8}, challenge_done=True)
        )
        self.assertFalse(
            debate_should_stop(done, {"current_round": 2, "message_count": 8}, challenge_done=False)
        )

    def test_analysis_defaults(self) -> None:
        a = parse_analysis({}, "вопрос")
        self.assertEqual(a.severity, "MEDIUM")
        self.assertEqual(a.problem, "вопрос")

    def test_orchestrator_null_scores(self) -> None:
        d = parse_orchestrator(
            {
                "continue_debate": True,
                "next_agent": "E",
                "mode": "DISCUSSION",
                "consensus_score": "null",
                "agreement_rate": "null",
                "unique_arguments": "null",
                "position_changes": None,
                "scores": {"argument_quality": "null", "x": ""},
            },
            fallback_agent="P",
        )
        self.assertEqual(d.next_agent, "E")
        self.assertEqual(d.consensus_score, 0.5)
        self.assertEqual(d.agreement_rate, 0.5)
        self.assertEqual(d.unique_arguments, 0)
        self.assertEqual(d.scores.get("argument_quality"), 0.0)

    def test_shallow(self) -> None:
        self.assertTrue(is_shallow_response(parse_agent_response({"position": "Согласен."}, agent="P")))
        self.assertFalse(
            is_shallow_response(
                AgentResponse(agent="P", position="Считаю, что без теста экономики решение преждевременно.", proposal="A/B")
            )
        )


class BoardScenarioTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(self._tmp.name) / "board.sqlite")
        os.environ["BOARD_DELAY_MIN_SEC"] = "0"
        os.environ["BOARD_DELAY_MAX_SEC"] = "0"
        os.environ["BOARD_MAX_MEETING_SEC"] = "120"
        os.environ["BOARD_EXTRA_ROUNDS"] = "0"
        os.environ["BOARD_HIGH_CONFIDENCE"] = "0.75"
        os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
        store.reset_connection()
        store.init_db()

    async def asyncTearDown(self) -> None:
        store.reset_connection()
        self._tmp.cleanup()

    async def test_section_68_autonomous_meeting(self) -> None:
        provider = _ScriptedProvider()
        pub = _FakePublisher()
        svc = MeetingService(publisher=pub, provider=provider)
        meeting = svc.start_meeting(
            user_id=1,
            chat_id=42,
            question="Думаю сократить отдел продаж с 12 до 8 человек. Продажи упали на 20%. Что делать?",
        )
        saved = await svc.run(meeting["id"])
        self.assertIsNotNone(saved)
        msgs = store.list_messages(meeting["id"])
        agents = [m["agent"] for m in msgs]
        for a in ("P", "A", "E", "I"):
            self.assertIn(a, agents)
        self.assertGreaterEqual(sum(1 for a in agents if a in {"P", "A", "E", "I"}), 8)
        self.assertIn("CHAIR", agents)
        later = [m for m in msgs if m["agent"] in {"P", "E", "A", "I"}][1]
        self.assertTrue(
            any(x in (later.get("content") or "") for x in ("P", "коллег", "аргумент")),
            later.get("content"),
        )
        self.assertTrue(any("Challenge" in t or "challenge" in t.lower() for t in pub.texts))
        self.assertIn("Не сокращать", saved["decision"])
        self.assertTrue(saved["kpis"])
        self.assertTrue(saved["risks"])
        self.assertTrue(saved["actions"] or saved.get("actions_json"))
        self.assertTrue(any("EXECUTIVE DECISION" in t for t in pub.texts))
        self.assertIn("board_analyze", provider.ops)
        self.assertTrue(any(o.startswith("board_agent_") for o in provider.ops))

    async def test_low_confidence_runs_extra_round(self) -> None:
        os.environ["BOARD_EXTRA_ROUNDS"] = "1"
        os.environ["BOARD_HIGH_CONFIDENCE"] = "0.8"

        class _LowThenHigh(_ScriptedProvider):
            def __init__(self) -> None:
                super().__init__()
                self.chairs = 0

            def generate_json(self, *, system: str, user: str, operation: str, temperature: float = 0.4, **_kwargs):
                data = super().generate_json(
                    system=system, user=user, operation=operation, temperature=temperature
                )
                if operation == "board_chair":
                    self.chairs += 1
                    data = dict(data)
                    data["confidence"] = 0.4 if self.chairs == 1 else 0.91
                return data

        provider = _LowThenHigh()
        pub = _FakePublisher()
        svc = MeetingService(publisher=pub, provider=provider)
        meeting = svc.start_meeting(user_id=1, chat_id=11, question="Масштабировать отдел?")
        saved = await svc.run(meeting["id"])
        self.assertIsNotNone(saved)
        self.assertEqual(provider.chairs, 2)
        self.assertGreaterEqual(saved.get("confidence") or 0, 0.8)
        agents = [
            m["agent"]
            for m in store.list_messages(meeting["id"])
            if m["agent"] in {"P", "A", "E", "I"}
        ]
        self.assertGreaterEqual(len(agents), 8)

    async def test_user_inject_during_meeting(self) -> None:
        provider = _ScriptedProvider()
        pub = _FakePublisher()
        svc = MeetingService(publisher=pub, provider=provider)
        meeting = svc.start_meeting(user_id=1, chat_id=7, question="Нанять двух разработчиков?")
        svc.inject_user(meeting["id"], "У нас 12 клиентов на 50к", priority_agent="E")
        msgs = store.list_messages(meeting["id"])
        self.assertEqual(msgs[-1]["agent"], "USER")
        self.assertEqual(msgs[-1]["structured"].get("priority_agent"), "E")
        from assistant.board.meeting import runtime_for

        self.assertEqual(runtime_for(meeting["id"]).priority_agent, "E")

    async def test_all_agents_fail_aborts(self) -> None:
        class _Fail:
            def generate_json(self, **kwargs):
                raise RuntimeError("402 Client Error: Payment Required")

        pub = _FakePublisher()
        svc = MeetingService(publisher=pub, provider=_Fail())
        meeting = svc.start_meeting(user_id=1, chat_id=9, question="KPI для менеджеров")
        saved = await svc.run(meeting["id"])
        self.assertIsNone(saved)
        row = store.get_meeting(meeting["id"])
        self.assertEqual(row["status"], "ERROR")
        self.assertTrue(any("кредит" in t.lower() for t in pub.texts))
        self.assertFalse(any("недоступен" in t.lower() for t in pub.texts))
        self.assertTrue(any("Недоступны" in s for s in pub.statuses))
