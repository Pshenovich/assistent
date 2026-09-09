"""CHAIR synthesis → persist + follow-up hooks."""

from __future__ import annotations

from typing import Any

from assistant.board import events, store
from assistant.board import llm as board_llm
from assistant.board.context import build_agent_context, load_prompt
from assistant.board.followup import schedule_followups
from assistant.board.models import ChairDecision
from assistant.board.numbers import safe_float


def parse_chair_decision(raw: dict[str, Any], *, unavailable: list[str]) -> ChairDecision:
    actions = []
    for item in raw.get("actions") or []:
        if isinstance(item, dict) and str(item.get("action") or "").strip():
            actions.append(
                {
                    "action": str(item.get("action") or "").strip(),
                    "owner": str(item.get("owner") or "").strip(),
                    "deadline": str(item.get("deadline") or "").strip(),
                    "success_metric": str(item.get("success_metric") or "").strip(),
                }
            )
        elif str(item).strip():
            actions.append(
                {"action": str(item).strip(), "owner": "", "deadline": "", "success_metric": ""}
            )
    conf = safe_float(raw.get("confidence"), 0.7)
    missing = [str(x) for x in (raw.get("unavailable_agents") or unavailable) if str(x).strip()]
    return ChairDecision(
        problem=str(raw.get("problem") or "").strip(),
        decision=str(raw.get("decision") or "").strip(),
        why=[str(x).strip() for x in (raw.get("why") or []) if str(x).strip()],
        actions=actions,
        kpis=[str(x).strip() for x in (raw.get("kpis") or []) if str(x).strip()],
        risks=[str(x).strip() for x in (raw.get("risks") or []) if str(x).strip()],
        assumptions=[str(x).strip() for x in (raw.get("assumptions") or []) if str(x).strip()],
        open_questions=[str(x).strip() for x in (raw.get("open_questions") or []) if str(x).strip()],
        do_not_do=[str(x).strip() for x in (raw.get("do_not_do") or []) if str(x).strip()],
        confidence=max(0.0, min(1.0, conf)),
        unavailable_agents=missing,
        matrix=list(raw.get("matrix") or []) if isinstance(raw.get("matrix"), list) else [],
        raw=raw,
    )


class DecisionService:
    def __init__(self, provider: Any | None = None) -> None:
        self.provider = provider

    def synthesize(self, meeting_id: str, *, unavailable: list[str] | None = None) -> ChairDecision:
        missing = list(unavailable or [])
        ctx = build_agent_context(
            meeting_id=meeting_id,
            agent="CHAIR",
            mode="SYNTHESIS",
            task=(
                "Прими исполнимое решение. Отдели факты от предположений. "
                "Покажи trade-offs. Предпочитай эксперимент, если решение обратимо."
            ),
        )
        last_err: Exception | None = None
        raw: dict[str, Any] = {}
        for attempt in range(2):
            try:
                raw = board_llm.generate_json(
                    system=load_prompt("CHAIR"),
                    user=ctx["text"],
                    operation="board_chair",
                    temperature=0.2,
                    max_tokens=board_llm.chair_max_tokens(),
                    cache_prefix=str(ctx.get("stable") or ""),
                    provider=self.provider,
                )
                last_err = None
                break
            except Exception as e:
                last_err = e
        if last_err is not None:
            raise last_err
        decision = parse_chair_decision(raw, unavailable=missing)
        if not decision.decision:
            raise RuntimeError("CHAIR вернул пустое решение")
        return decision

    def persist(
        self,
        meeting_id: str,
        decision: ChairDecision,
        *,
        followups: bool = True,
    ) -> dict[str, Any]:
        meeting = store.get_meeting(meeting_id) or {}
        payload = {
            "problem": decision.problem,
            "decision": decision.decision,
            "why": decision.why,
            "actions": decision.actions,
            "kpis": decision.kpis,
            "risks": decision.risks,
            "assumptions": decision.assumptions,
            "open_questions": decision.open_questions,
            "do_not_do": decision.do_not_do,
            "confidence": decision.confidence,
            "unavailable_agents": decision.unavailable_agents,
            "matrix": decision.matrix,
        }
        saved = store.save_decision(
            meeting_id=meeting_id,
            payload=payload,
            company_id=meeting.get("company_id"),
            chat_id=meeting.get("chat_id"),
            user_id=meeting.get("user_id"),
        )
        store.add_message(
            meeting_id=meeting_id,
            agent="CHAIR",
            content=decision.decision,
            round=int(meeting.get("current_round") or 1),
            structured=payload,
            confidence=decision.confidence,
        )
        events.emit(events.DECISION_CREATED, meeting_id=meeting_id, decision_id=saved.get("id"))
        if followups and saved.get("id"):
            schedule_followups(saved["id"])
        return saved
