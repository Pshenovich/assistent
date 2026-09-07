"""MeetingService: жизненный цикл совещания и автономный цикл дискуссии."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from assistant.board import events, store
from assistant.board.decision import DecisionService
from assistant.board.engine import is_shallow_response
from assistant.board.memory import find_related_decisions
from assistant.board.models import (
    PAEI_AGENTS,
    ROUND1_ORDER,
    SEVERITY_ROUNDS,
    AgentResponse,
    ChairDecision,
    ProblemAnalysis,
)
from assistant.board.llm import public_llm_error
from assistant.board.orchestrator import DebateOrchestrator, limits_hit, max_rounds, needs_challenge
from assistant.board.renderer import (
    decision_keyboard_rows,
    format_agent_message,
    format_board_status,
    format_decision,
    format_start_banner,
)


@dataclass
class MeetingRuntime:
    meeting_id: str
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    priority_agent: str | None = None
    user_injected: asyncio.Event = field(default_factory=asyncio.Event)


_runtimes: dict[str, MeetingRuntime] = {}
_chat_tasks: dict[str, asyncio.Task[Any]] = {}


def runtime_for(meeting_id: str) -> MeetingRuntime:
    rt = _runtimes.get(meeting_id)
    if rt is None:
        rt = MeetingRuntime(meeting_id=meeting_id)
        _runtimes[meeting_id] = rt
    return rt


def drop_runtime(meeting_id: str) -> None:
    _runtimes.pop(meeting_id, None)


class MeetingService:
    def __init__(
        self,
        *,
        publisher: Any,
        provider: Any | None = None,
    ) -> None:
        self.publisher = publisher
        self.provider = provider
        self.orch = DebateOrchestrator(provider=provider)
        self.engine = self.orch.engine
        self.decisions = DecisionService(provider=provider)
        self._last_turn_error: BaseException | None = None
        self.followups = True

    async def _set_status(self, meeting: dict[str, Any], text: str) -> None:
        fn = getattr(self.publisher, "update_status", None)
        if callable(fn):
            await fn(meeting, text)

    async def _finish_status(self, meeting: dict[str, Any]) -> None:
        fn = getattr(self.publisher, "clear_status", None)
        if callable(fn):
            await fn(meeting)

    def start_meeting(
        self,
        *,
        user_id: int,
        chat_id: int,
        question: str,
        thread_id: int | None = None,
        extra_instruction: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        company = store.get_or_create_company_for_chat(chat_id)
        meeting = store.create_meeting(
            user_id=user_id,
            chat_id=chat_id,
            question=question,
            company_id=company.get("id"),
            thread_id=thread_id,
            title=title,
            max_rounds=max_rounds(),
            extra_instruction=extra_instruction,
        )
        events.emit(events.MEETING_CREATED, meeting_id=meeting["id"], chat_id=str(chat_id))
        return meeting

    def inject_user(self, meeting_id: str, text: str, *, priority_agent: str | None = None) -> None:
        meeting = store.get_meeting(meeting_id) or {}
        store.add_message(
            meeting_id=meeting_id,
            agent="USER",
            content=text,
            round=int(meeting.get("current_round") or 1),
            structured={"kind": "USER_INPUT", "priority_agent": priority_agent},
        )
        events.emit(events.USER_INPUT_RECEIVED, meeting_id=meeting_id)
        rt = runtime_for(meeting_id)
        if priority_agent in PAEI_AGENTS:
            rt.priority_agent = priority_agent
        rt.user_injected.set()

    def request_stop(self, meeting_id: str) -> None:
        runtime_for(meeting_id).stop.set()

    async def run(self, meeting_id: str) -> dict[str, Any] | None:
        rt = runtime_for(meeting_id)
        unavailable: list[str] = []
        spoken: list[str] = []
        challenge_done = False
        self._last_turn_error = None
        try:
            meeting = store.get_meeting(meeting_id)
            if not meeting:
                return None
            no = store.meeting_number(meeting_id)
            await self.publisher.publish_text(
                meeting, format_start_banner(no, str(meeting.get("title") or ""))
            )
            await self._set_status(
                meeting,
                format_board_status(phase="ANALYZING", unavailable=unavailable, spoken=spoken),
            )

            store.set_meeting_status(meeting_id, "ANALYZING")
            try:
                analysis = await asyncio.to_thread(
                    self.orch.analyze, str(meeting.get("original_question") or "")
                )
            except Exception as e:
                print(f"[board] analyze_err={e!r}")
                analysis = ProblemAnalysis(
                    problem=str(meeting.get("original_question") or ""),
                    decision_required=str(meeting.get("original_question") or ""),
                    title=str(meeting.get("original_question") or "")[:80],
                )
            cap = min(max_rounds(), SEVERITY_ROUNDS.get(analysis.severity, 3))
            store.set_analysis(
                meeting_id,
                {
                    **analysis.raw,
                    "problem": analysis.problem,
                    "decision_required": analysis.decision_required,
                    "known_facts": analysis.known_facts,
                    "assumptions": analysis.assumptions,
                    "missing_information": analysis.missing_information,
                    "decision_type": analysis.decision_type,
                    "severity": analysis.severity,
                    "reversibility": analysis.reversibility,
                    "title": analysis.title,
                },
            )
            store.update_meeting(meeting_id, max_rounds=cap, title=analysis.title, status="DISCUSSION")

            past = find_related_decisions(
                str(meeting.get("chat_id")), str(meeting.get("original_question") or "")
            )

            for agent in ROUND1_ORDER:
                if rt.stop.is_set() or limits_hit(store.get_meeting(meeting_id) or {}):
                    break
                ok = await self._turn(
                    meeting_id,
                    agent,
                    mode="DISCUSSION",
                    past=past,
                    unavailable=unavailable,
                    spoken=spoken,
                )
                if not ok and agent not in unavailable:
                    unavailable.append(agent)

            if len(unavailable) >= 4:
                store.set_meeting_status(
                    meeting_id,
                    "ERROR",
                    error_message=str(self._last_turn_error or "all agents failed")[:500],
                )
                meeting = store.get_meeting(meeting_id) or meeting
                await self._finish_status(meeting)
                await self.publisher.publish_text(
                    meeting,
                    public_llm_error(
                        self._last_turn_error or RuntimeError("все агенты недоступны")
                    ),
                )
                return None

            store.update_meeting(meeting_id, current_round=2)
            self.orch.summarize_round(meeting_id, round_no=1, mode="DISCUSSION")

            while not rt.stop.is_set():
                meeting = store.get_meeting(meeting_id) or {}
                if limits_hit(meeting):
                    break
                if len(unavailable) >= 4:
                    break
                if rt.priority_agent:
                    nxt = rt.priority_agent
                    rt.priority_agent = None
                    if nxt in unavailable:
                        continue
                    await self._turn(
                        meeting_id,
                        nxt,
                        mode="DISCUSSION",
                        past=past,
                        unavailable=unavailable,
                        spoken=spoken,
                    )
                    continue
                orch = await asyncio.to_thread(self.orch.decide_next, meeting_id)
                if orch.mode == "CHALLENGE" or needs_challenge(
                    severity=str((meeting.get("analysis") or {}).get("severity") or "MEDIUM"),
                    agreement_rate=orch.agreement_rate,
                    round_no=int(meeting.get("current_round") or 1),
                    challenge_done=challenge_done,
                ):
                    if not challenge_done:
                        await self._run_challenge(
                            meeting_id, past, unavailable, rt, spoken=spoken
                        )
                        challenge_done = True
                    if orch.mode == "SYNTHESIS" or not orch.continue_debate:
                        break
                    continue
                if orch.mode == "SYNTHESIS" or not orch.continue_debate:
                    break
                nxt = orch.next_agent or "P"
                if nxt in unavailable:
                    nxt = next((a for a in ROUND1_ORDER if a not in unavailable), None)
                    if nxt is None:
                        break
                ok = await self._turn(
                    meeting_id,
                    nxt,
                    mode="DISCUSSION",
                    past=past,
                    unavailable=unavailable,
                    spoken=spoken,
                )
                if not ok and nxt not in unavailable:
                    unavailable.append(nxt)
                meeting = store.get_meeting(meeting_id) or {}
                if int(meeting.get("message_count") or 0) >= 8:
                    rnd = int(meeting.get("current_round") or 1)
                    if rnd >= 2:
                        self.orch.summarize_round(meeting_id, round_no=rnd, mode="DISCUSSION")
                        store.update_meeting(meeting_id, current_round=rnd + 1)

            meeting = store.get_meeting(meeting_id) or {}
            severity = str((meeting.get("analysis") or {}).get("severity") or "MEDIUM")
            if not challenge_done and needs_challenge(
                severity=severity,
                agreement_rate=0.9,
                round_no=2,
                challenge_done=False,
            ):
                if not rt.stop.is_set():
                    await self._run_challenge(
                        meeting_id, past, unavailable, rt, spoken=spoken
                    )

            if rt.stop.is_set():
                store.set_meeting_status(meeting_id, "STOPPED")
                events.emit(events.MEETING_STOPPED, meeting_id=meeting_id)
                await self._finish_status(meeting)
                await self.publisher.publish_text(meeting, "Совещание остановлено.")
                return None

            return await self._synthesize(meeting_id, unavailable)
        except Exception as e:
            print(f"[board] meeting_err id={meeting_id} err={e!r}")
            store.set_meeting_status(meeting_id, "ERROR", error_message=str(e)[:500])
            meeting = store.get_meeting(meeting_id) or {}
            try:
                await self._finish_status(meeting)
                await self.publisher.publish_text(
                    meeting, f"Не удалось завершить совещание: {e}"
                )
            except Exception:
                pass
            return None
        finally:
            drop_runtime(meeting_id)

    async def _run_challenge(
        self,
        meeting_id: str,
        past: list[dict[str, Any]],
        unavailable: list[str],
        rt: MeetingRuntime,
        *,
        spoken: list[str] | None = None,
    ) -> None:
        spoken = spoken if spoken is not None else []
        store.set_meeting_status(meeting_id, "CHALLENGE")
        events.emit(events.CHALLENGE_STARTED, meeting_id=meeting_id)
        meeting = store.get_meeting(meeting_id) or {}
        rnd = int(meeting.get("current_round") or 1)
        await self._set_status(
            meeting,
            format_board_status(
                phase="CHALLENGE", unavailable=unavailable, spoken=spoken
            ),
        )
        await self.publisher.publish_text(
            meeting,
            "⚔️ Challenge: считаем текущий консенсус возможно ошибочным.",
        )
        for agent in ROUND1_ORDER:
            if rt.stop.is_set() or limits_hit(store.get_meeting(meeting_id) or {}):
                break
            if agent in unavailable:
                continue
            await self._turn(
                meeting_id,
                agent,
                mode="CHALLENGE",
                past=past,
                unavailable=unavailable,
                spoken=spoken,
            )
        self.orch.summarize_round(meeting_id, round_no=rnd, mode="CHALLENGE")
        store.update_meeting(meeting_id, current_round=rnd + 1, status="DISCUSSION")

    async def _turn(
        self,
        meeting_id: str,
        agent: str,
        *,
        mode: str,
        past: list[dict[str, Any]],
        unavailable: list[str],
        spoken: list[str] | None = None,
    ) -> bool:
        spoken = spoken if spoken is not None else []
        meeting = store.get_meeting(meeting_id) or {}
        await self._set_status(
            meeting,
            format_board_status(
                phase=mode,
                speaking=agent,
                unavailable=unavailable,
                spoken=spoken,
            ),
        )
        await self.publisher.before_agent(meeting, agent)
        try:
            resp: AgentResponse = await asyncio.to_thread(
                self.engine.respond,
                agent,
                meeting_id,
                mode=mode,
                past_decisions=past,
            )
        except Exception as e:
            print(f"[board] agent_fail agent={agent} err={e!r}")
            self._last_turn_error = e
            if agent not in unavailable:
                unavailable.append(agent)
            await self._set_status(
                meeting,
                format_board_status(
                    phase=mode,
                    unavailable=unavailable,
                    spoken=spoken,
                ),
            )
            return False
        if is_shallow_response(resp):
            print(f"[board] shallow_skip agent={agent}")
        text = format_agent_message(agent, resp)
        mid = store.add_message(
            meeting_id=meeting_id,
            agent=agent,
            content=resp.display_text(),
            round=int(meeting.get("current_round") or 1),
            structured=resp.raw,
            confidence=resp.confidence,
        )
        events.emit(events.AGENT_RESPONSE_CREATED, meeting_id=meeting_id, agent=agent)
        tg_id = await self.publisher.publish_text(meeting, text)
        if tg_id and mid.get("id"):
            store.set_message_telegram_id(str(mid["id"]), int(tg_id))
        events.emit(events.AGENT_RESPONSE_PUBLISHED, meeting_id=meeting_id, agent=agent)
        if agent not in spoken:
            spoken.append(agent)
        await self._set_status(
            meeting,
            format_board_status(
                phase=mode,
                unavailable=unavailable,
                spoken=spoken,
            ),
        )
        return True

    async def _synthesize(self, meeting_id: str, unavailable: list[str]) -> dict[str, Any] | None:
        store.set_meeting_status(meeting_id, "SYNTHESIS")
        events.emit(events.MEETING_READY_FOR_SYNTHESIS, meeting_id=meeting_id)
        meeting = store.get_meeting(meeting_id) or {}
        await self._set_status(
            meeting,
            format_board_status(phase="SYNTHESIS", unavailable=unavailable),
        )
        try:
            decision: ChairDecision = await asyncio.to_thread(
                self.decisions.synthesize, meeting_id, unavailable=unavailable
            )
        except Exception as e:
            print(f"[board] chair_retry err={e!r}")
            await asyncio.sleep(1)
            try:
                decision = await asyncio.to_thread(
                    self.decisions.synthesize, meeting_id, unavailable=unavailable
                )
            except Exception as e2:
                store.set_meeting_status(meeting_id, "ERROR", error_message=str(e2)[:500])
                await self._finish_status(meeting)
                await self.publisher.publish_text(
                    meeting, "Не удалось сформировать итоговое решение. Попробуйте /new."
                )
                return None
        saved = self.decisions.persist(meeting_id, decision, followups=self.followups)
        store.set_meeting_status(meeting_id, "COMPLETED")
        no = store.meeting_number(meeting_id)
        markup = decision_keyboard_rows(meeting_id)
        await self._finish_status(meeting)
        await self.publisher.publish_text(
            meeting, format_decision(decision, meeting_no=no), markup=markup
        )
        return saved


def spawn_meeting(service: MeetingService, meeting_id: str, chat_key: str) -> asyncio.Task[Any]:
    task = asyncio.create_task(service.run(meeting_id), name=f"board-meeting-{meeting_id[:8]}")
    _chat_tasks[chat_key] = task

    def _done(t: asyncio.Task[Any]) -> None:
        _chat_tasks.pop(chat_key, None)
        drop_runtime(meeting_id)
        if t.cancelled():
            return
        err = t.exception()
        if err:
            print(f"[board] task_err meeting={meeting_id} err={err!r}")

    task.add_done_callback(_done)
    return task


def chat_has_running(chat_key: str) -> bool:
    t = _chat_tasks.get(chat_key)
    return bool(t and not t.done())
