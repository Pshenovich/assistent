"""Форматирование сообщений совета."""

from __future__ import annotations

import unittest

from assistant.board.models import AgentResponse, ChairDecision
from assistant.board.renderer import (
    format_agent_message,
    format_board_status,
    format_company_overview,
    format_decision,
    format_history,
    format_start_banner,
    format_status,
)


class BoardRendererTest(unittest.TestCase):
    def test_agent_visual(self) -> None:
        html = format_agent_message(
            "E",
            AgentResponse(
                agent="E",
                position="Не начинать с 50 или 80",
                disagreement=["Рамка цены слишком узкая"],
                proposal="Сделать Pro-тариф",
                changed_position=True,
            ),
        )
        self.assertIn("E — STRATEGY", html)
        self.assertIn("Предлагаю", html)
        self.assertIn("Позиция изменена", html)

    def test_decision_compact(self) -> None:
        text = format_decision(
            ChairDecision(
                problem="Цена",
                decision="Не повышать сразу",
                why=["мало данных"],
                actions=[{"action": "тест 20/20/60"}],
                kpis=["MRR/100 лидов"],
                risks=["менеджеры исказят тест"],
                do_not_do=["менять всю базу"],
                confidence=0.84,
            ),
            meeting_no=42,
        )
        self.assertIn("EXECUTIVE DECISION", text)
        self.assertIn("84%", text)
        self.assertIn("Не повышать", text)

    def test_history_empty(self) -> None:
        self.assertIn("пуста", format_history([]))

    def test_status(self) -> None:
        t = format_status({"status": "DISCUSSION", "current_round": 2, "max_rounds": 4, "title": "X"})
        self.assertIn("DISCUSSION", t)

    def test_banner(self) -> None:
        self.assertIn("MEETING #3", format_start_banner(3, "Цена"))

    def test_board_status_single_message(self) -> None:
        text = format_board_status(
            phase="DISCUSSION",
            speaking="P",
            unavailable=["A"],
            spoken=["E"],
        )
        self.assertIn("P готовит", text)
        self.assertIn("Недоступны: A", text)
        self.assertIn("E", text)

    def test_company_overview(self) -> None:
        text = format_company_overview(
            "SaaS B2B",
            [{"filename": "team.csv", "kind": "team", "text": "Ann,CEO"}],
        )
        self.assertIn("team.csv", text)
        self.assertIn("SaaS B2B", text)
