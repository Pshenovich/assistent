"""Follow-up по KPI и post-decision review."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from assistant.board import events, store
from assistant.board import llm as board_llm
from assistant.board.context import load_prompt


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_deadline(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    iso = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        try:
            dt = datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    low = text.lower()
    weeks = 2
    if "недел" in low:
        digits = "".join(ch for ch in low if ch.isdigit())
        weeks = int(digits or "2")
        return _now() + timedelta(weeks=max(1, weeks))
    if "день" in low or "дня" in low or "дней" in low:
        digits = "".join(ch for ch in low if ch.isdigit())
        days = int(digits or "7")
        return _now() + timedelta(days=max(1, days))
    return None


def schedule_followups(decision_id: str) -> dict[str, Any] | None:
    decision = store.get_decision(decision_id)
    if not decision:
        return None
    kpis = decision.get("kpis") or []
    actions = store.list_actions(decision_id)
    if not kpis and not any(a.get("success_metric") for a in actions):
        return None
    due: datetime | None = None
    for act in actions:
        parsed = parse_deadline(str(act.get("deadline") or ""))
        if parsed and (due is None or parsed < due):
            due = parsed
    if due is None:
        due = _now() + timedelta(days=14)
    if due <= _now():
        due = _now() + timedelta(days=1)
    prompt = "Нужны результаты по KPI принятого решения."
    created = store.create_followup(
        decision_id=decision_id,
        chat_id=str(decision.get("chat_id") or ""),
        user_id=decision.get("user_id"),
        due_at=due.isoformat(),
        prompt=prompt,
    )
    events.emit(events.FOLLOWUP_DUE, decision_id=decision_id, followup_id=created.get("id"))
    return created


def collect_due_followups() -> list[dict[str, Any]]:
    return store.due_followups()


def review_decision(
    *,
    decision_id: str,
    user_results: str,
    provider: Any | None = None,
) -> dict[str, Any]:
    decision = store.get_decision(decision_id) or {}
    system = (
        load_prompt("CHAIR")
        + "\n\nЭто POST_DECISION_REVIEW. Оцени: правильно ли приняли решение? "
        "Что сработалo / нет. Предложи корректировку."
    )
    user = (
        f"Исходная проблема: {decision.get('problem')}\n"
        f"Решение: {decision.get('decision')}\n"
        f"KPI: {decision.get('kpis')}\n"
        f"Результаты пользователя:\n{user_results}"
    )
    raw = board_llm.generate_json(
        system=system,
        user=user,
        operation="board_decision_review",
        temperature=0.2,
        provider=provider,
    )
    store.save_review(decision_id=decision_id, user_results=user_results, review=raw)
    for act in store.list_actions(decision_id):
        store.update_action_status(str(act["id"]), "reviewed")
    return raw
