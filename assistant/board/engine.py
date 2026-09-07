"""AgentEngine: ход агента + валидация JSON."""

from __future__ import annotations

import re
from typing import Any

from assistant.board import llm as board_llm
from assistant.board.context import build_agent_context, load_prompt
from assistant.board.models import AgentResponse
from assistant.board.numbers import safe_float

_SHALLOW = {
    "согласен",
    "согласна",
    "полностью поддерживаю",
    "хорошая идея",
    "ок",
    "ok",
    "да",
    "верно",
    "согласен.",
    "полностью поддерживаю.",
}

_CLAIM_KINDS = {"FACT", "ASSUMPTION", "HYPOTHESIS", "OPINION"}


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val:
        return [str(val).strip()]
    return []


def parse_agent_response(raw: dict[str, Any], *, agent: str) -> AgentResponse:
    claims = []
    for item in raw.get("claims") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "OPINION").upper()
        if kind not in _CLAIM_KINDS:
            kind = "OPINION"
        text = str(item.get("text") or "").strip()
        if text:
            claims.append({"kind": kind, "text": text})
    conf = safe_float(raw.get("confidence"), 0.5)
    return AgentResponse(
        agent=str(raw.get("agent") or agent),
        position=str(raw.get("position") or "").strip(),
        responding_to=_as_list(raw.get("responding_to")),
        agreement=_as_list(raw.get("agreement")),
        disagreement=_as_list(raw.get("disagreement")),
        new_arguments=_as_list(raw.get("new_arguments")),
        changed_position=bool(raw.get("changed_position")),
        proposal=str(raw.get("proposal") or "").strip(),
        confidence=max(0.0, min(1.0, conf)),
        needs_user_input=bool(raw.get("needs_user_input")),
        claims=claims,
        raw=raw,
    )


def is_shallow_response(resp: AgentResponse) -> bool:
    pos = re.sub(r"\s+", " ", (resp.position or "").strip().lower()).rstrip(".!")
    if not pos:
        return True
    if pos in _SHALLOW:
        return True
    if len(pos) < 40 and not resp.proposal and not resp.disagreement and not resp.new_arguments:
        return True
    return False


class AgentEngine:
    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider

    def respond(
        self,
        agent: str,
        meeting_id: str,
        *,
        mode: str = "DISCUSSION",
        task: str = "",
        past_decisions: list[dict[str, Any]] | None = None,
    ) -> AgentResponse:
        ctx = build_agent_context(
            meeting_id=meeting_id,
            agent=agent,
            mode=mode,
            task=task,
            past_decisions=past_decisions,
        )
        system = load_prompt(agent)
        if mode == "CHALLENGE":
            system += (
                "\n\nCHALLENGE MODE: не защищай консенсус. Найди главный риск, "
                "ложное предположение, second-order effect или катастрофическую ошибку."
            )
        raw = board_llm.generate_json(
            system=system,
            user=ctx["text"],
            operation=f"board_agent_{agent.lower()}",
            temperature=0.55 if mode == "CHALLENGE" else 0.4,
            provider=self.provider,
        )
        resp = parse_agent_response(raw, agent=agent)
        if is_shallow_response(resp):
            raw = board_llm.generate_json(
                system=system
                + "\n\nПредыдущий ответ был слишком поверхностным. Разверни аргумент.",
                user=ctx["text"] + "\n\nRETRY: дай содержательную реакцию, не одно предложение.",
                operation=f"board_agent_{agent.lower()}_retry",
                temperature=0.5,
                provider=self.provider,
            )
            resp = parse_agent_response(raw, agent=agent)
        return resp
