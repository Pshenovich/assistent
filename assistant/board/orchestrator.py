"""DebateOrchestrator: раунды, next agent, challenge, лимиты."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Protocol

from assistant.board import events, store
from assistant.board import llm as board_llm
from assistant.board.context import load_prompt
from assistant.board.engine import AgentEngine
from assistant.board.numbers import safe_float, safe_int
from assistant.board.models import (
    PAEI_AGENTS,
    ROUND1_ORDER,
    SEVERITY_ROUNDS,
    OrchestratorDecision,
    PAEIAgent,
    ProblemAnalysis,
)


class Publisher(Protocol):
    async def publish_text(self, meeting: dict[str, Any], text: str, *, markup: Any = None) -> int | None: ...
    async def before_agent(self, meeting: dict[str, Any], agent: str) -> None: ...
    async def update_status(self, meeting: dict[str, Any], text: str) -> None: ...
    async def clear_status(self, meeting: dict[str, Any]) -> None: ...


def max_rounds() -> int:
    try:
        return max(1, int(os.getenv("BOARD_MAX_ROUNDS", "4") or "4"))
    except ValueError:
        return 4


def extra_rounds() -> int:
    try:
        return max(0, int(os.getenv("BOARD_EXTRA_ROUNDS", "2") or "2"))
    except ValueError:
        return 2


def max_rounds_low() -> int:
    try:
        return max(1, int(os.getenv("BOARD_MAX_ROUNDS_LOW", "2") or "2"))
    except ValueError:
        return 2


def rounds_for_severity(severity: str | None) -> int:
    cap = max_rounds()
    if str(severity or "").strip().upper() == "LOW":
        return min(cap, max_rounds_low())
    return cap


def high_confidence_threshold() -> float:
    try:
        return min(0.95, max(0.5, float(os.getenv("BOARD_HIGH_CONFIDENCE", "0.75") or "0.75")))
    except ValueError:
        return 0.75


def max_messages() -> int:
    try:
        return max(4, int(os.getenv("BOARD_MAX_MESSAGES", "16") or "16"))
    except ValueError:
        return 16


def max_meeting_sec() -> float:
    try:
        return max(60.0, float(os.getenv("BOARD_MAX_MEETING_SEC", "300") or "300"))
    except ValueError:
        return 300.0


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def limits_hit(meeting: dict[str, Any]) -> bool:
    if int(meeting.get("message_count") or 0) >= max_messages():
        return True
    if int(meeting.get("current_round") or 1) > int(meeting.get("max_rounds") or max_rounds()):
        return True
    started = _parse_dt(str(meeting.get("started_at") or ""))
    if started:
        now = datetime.now(timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if (now - started).total_seconds() >= max_meeting_sec():
            return True
    return False


def parse_analysis(raw: dict[str, Any], question: str) -> ProblemAnalysis:
    sev = str(raw.get("severity") or "MEDIUM").upper()
    if sev not in SEVERITY_ROUNDS:
        sev = "MEDIUM"
    rev = str(raw.get("reversibility") or "PARTIALLY_REVERSIBLE").upper()
    if rev not in {"REVERSIBLE", "PARTIALLY_REVERSIBLE", "IRREVERSIBLE"}:
        rev = "PARTIALLY_REVERSIBLE"
    title = str(raw.get("title") or raw.get("problem") or question)[:80]
    return ProblemAnalysis(
        problem=str(raw.get("problem") or question).strip(),
        decision_required=str(raw.get("decision_required") or "").strip(),
        known_facts=[str(x) for x in (raw.get("known_facts") or []) if str(x).strip()],
        assumptions=[str(x) for x in (raw.get("assumptions") or []) if str(x).strip()],
        missing_information=[str(x) for x in (raw.get("missing_information") or []) if str(x).strip()],
        decision_type=str(raw.get("decision_type") or "general").strip() or "general",
        severity=sev,  # type: ignore[arg-type]
        reversibility=rev,  # type: ignore[arg-type]
        title=title,
        raw=raw,
    )


def heuristic_next_agent(last_agent: str | None, last_text: str, spoken: list[str]) -> PAEIAgent:
    text = (last_text or "").lower()
    if last_agent == "E":
        return "P"
    if last_agent == "P" and any(k in text for k in ("не сходи", "экономик", "mrr", "конверс")):
        return "E"
    if last_agent == "A" and any(k in text for k in ("процесс", "невозмож", "договор")):
        return "I" if "I" not in spoken[-2:] else "P"
    if last_agent == "I" and any(k in text for k in ("сопротив", "команд", "продаж")):
        return "P"
    for cand in ROUND1_ORDER:
        if cand != last_agent:
            return cand
    return "P"


def parse_orchestrator(raw: dict[str, Any], *, fallback_agent: PAEIAgent) -> OrchestratorDecision:
    nxt = str(raw.get("next_agent") or fallback_agent).upper()
    if nxt not in PAEI_AGENTS:
        nxt = fallback_agent
    mode = str(raw.get("mode") or "DISCUSSION").upper()
    if mode not in {"DISCUSSION", "CHALLENGE", "SYNTHESIS"}:
        mode = "DISCUSSION"
    scores = raw.get("scores") if isinstance(raw.get("scores"), dict) else {}
    return OrchestratorDecision(
        continue_debate=bool(raw.get("continue_debate", True)),
        next_agent=nxt,  # type: ignore[arg-type]
        reason=str(raw.get("reason") or ""),
        mode=mode,  # type: ignore[arg-type]
        consensus_score=safe_float(raw.get("consensus_score"), 0.5),
        agreement_rate=safe_float(raw.get("agreement_rate"), 0.5),
        unresolved=[str(x) for x in (raw.get("unresolved") or []) if str(x).strip()],
        unique_arguments=safe_int(raw.get("unique_arguments"), 0),
        position_changes=safe_int(raw.get("position_changes"), 0),
        scores={str(k): safe_float(v, 0.0) for k, v in scores.items() if str(k)},
    )


def debate_should_stop(
    decision: OrchestratorDecision,
    meeting: dict[str, Any],
    *,
    challenge_done: bool,
) -> bool:
    """Ранняя остановка: SYNTHESIS, continue_debate=false или высокий consensus после challenge."""
    if str(decision.mode) == "SYNTHESIS" or not decision.continue_debate:
        return True
    if str(decision.mode) == "CHALLENGE" and not challenge_done:
        return False
    rnd = int((meeting or {}).get("current_round") or 1)
    spoken = int((meeting or {}).get("message_count") or 0)
    if (
        challenge_done
        and rnd >= 2
        and spoken >= 4
        and decision.consensus_score >= high_confidence_threshold()
    ):
        return True
    return False


def needs_challenge(
    *,
    severity: str,
    agreement_rate: float,
    round_no: int,
    challenge_done: bool,
) -> bool:
    if challenge_done:
        return False
    if severity in {"HIGH", "CRITICAL"}:
        return True
    if agreement_rate > 0.8 and round_no < 3:
        return True
    return False


class DebateOrchestrator:
    def __init__(
        self,
        *,
        engine: AgentEngine | None = None,
        provider: Any | None = None,
    ) -> None:
        self.provider = provider
        self.engine = engine or AgentEngine(provider=provider)

    def analyze(self, question: str, *, company: str = "") -> ProblemAnalysis:
        user = question
        brief = (company or "").strip()
        if brief:
            user = (
                f"{question}\n\nCOMPANY CONTEXT (compact catalog, not the full doc):\n{brief}"
            )
        raw = board_llm.generate_json(
            system=(
                "Ты аналитик управленческой проблемы. Верни JSON: "
                "problem, decision_required, known_facts, assumptions, "
                "missing_information, decision_type, severity "
                "(LOW|MEDIUM|HIGH|CRITICAL), reversibility "
                "(REVERSIBLE|PARTIALLY_REVERSIBLE|IRREVERSIBLE), title (до 80 символов). "
                "Факты о продуктах и юните компании бери из COMPANY CONTEXT, не выдумывай."
            ),
            user=user,
            operation="board_analyze",
            temperature=0.2,
            max_tokens=board_llm.aux_max_tokens(),
            model=board_llm.board_aux_model(),
            provider=self.provider,
        )
        return parse_analysis(raw, question)

    def decide_next(self, meeting_id: str) -> OrchestratorDecision:
        meeting = store.get_meeting(meeting_id) or {}
        messages = store.list_messages(meeting_id)
        last = messages[-1] if messages else None
        last_agent = str(last.get("agent") if last else "")
        spoken = [str(m.get("agent")) for m in messages]
        fallback = heuristic_next_agent(
            last_agent if last_agent in PAEI_AGENTS else None,
            str(last.get("content") if last else ""),
            spoken,
        )
        transcript = "\n".join(
            f"{m.get('agent')}: {(m.get('content') or '')[:400]}" for m in messages[-16:]
        )
        try:
            raw = board_llm.generate_json(
                system=load_prompt("ORCHESTRATOR"),
                user=(
                    f"Раунд: {meeting.get('current_round')} / {meeting.get('max_rounds')}\n"
                    f"Сообщений: {meeting.get('message_count')}\n"
                    f"Статус: {meeting.get('status')}\n"
                    f"Порог уверенности: {high_confidence_threshold():.2f}\n"
                    f"Если consensus_score >= порога и все четыре функции высказались — "
                    f"continue_debate=false, mode=SYNTHESIS.\n"
                    f"Анализ: {meeting.get('analysis')}\n\n"
                    f"Транскрипт:\n{transcript}"
                ),
                operation="board_orchestrator",
                temperature=0.2,
                max_tokens=board_llm.aux_max_tokens(),
                model=board_llm.board_aux_model(),
                provider=self.provider,
            )
            decision = parse_orchestrator(raw, fallback_agent=fallback)
        except Exception as e:
            print(f"[board.orch] decide_fallback err={e!r}")
            decision = OrchestratorDecision(
                continue_debate=True,
                next_agent=fallback,
                reason="heuristic fallback",
                mode="DISCUSSION",
            )
        if decision.next_agent == last_agent and last_agent in PAEI_AGENTS:
            decision.next_agent = heuristic_next_agent(last_agent, "", spoken)
        return decision

    def summarize_round(self, meeting_id: str, *, round_no: int, mode: str) -> dict[str, Any]:
        messages = [
            m
            for m in store.list_messages(meeting_id)
            if int(m.get("round") or 0) == int(round_no)
        ]
        positions = []
        disagreements = []
        changes = 0
        for m in messages:
            st = m.get("structured") or {}
            if st.get("changed_position"):
                changes += 1
            disagreements.extend(st.get("disagreement") or [])
            positions.append(f"{m.get('agent')}: {(m.get('content') or '')[:280]}")
        try:
            raw = board_llm.generate_json(
                system="Суммируй раунд PAEI. JSON: summary, consensus, disagreements, open_questions, assumptions, agreement_rate.",
                user="\n".join(positions) or "пустой раунд",
                operation="board_round_summary",
                temperature=0.1,
                max_tokens=board_llm.aux_max_tokens(),
                model=board_llm.board_aux_model(),
                provider=self.provider,
            )
        except Exception:
            raw = {
                "summary": "\n".join(positions)[:800],
                "consensus": "",
                "disagreements": "; ".join(str(x) for x in disagreements[:6]),
                "open_questions": "",
                "assumptions": "",
                "agreement_rate": 0.5,
            }
        store.add_round_summary(
            meeting_id=meeting_id,
            round=round_no,
            mode=mode,
            summary=str(raw.get("summary") or ""),
            consensus=str(raw.get("consensus") or ""),
            disagreements=str(raw.get("disagreements") or "; ".join(map(str, disagreements))),
            open_questions=str(raw.get("open_questions") or ""),
            assumptions=str(raw.get("assumptions") or ""),
            scores={
                "agreement_rate": safe_float(raw.get("agreement_rate"), 0.5),
                "position_changes": changes,
            },
        )
        events.emit(events.ROUND_COMPLETED, meeting_id=meeting_id, round=round_no)
        return raw
