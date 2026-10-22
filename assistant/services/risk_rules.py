"""B7: риски по реестру обязательств — карточки Фокуса без LLM (только проверяемые правила).

1. Просроченные обязательства (за вами / за другими), которые ещё не стали задачей Orbita
   (просроченные задачи календаря показывает rule_overdue).
2. «Буксует»: одна договорённость всплывала на 3+ разных встречах и всё ещё открыта.
3. «Срок есть, ответственного нет»: исполнитель не ясен, срок ≤ 3 дней или прошёл.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

STALLED_MEETINGS = 3
NO_OWNER_HORIZON = timedelta(days=3)
MAX_LINES = 8


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _line(c: dict[str, Any]) -> str:
    who = f"{c['who']} — " if c.get("who") and c.get("direction") != "mine" else ""
    due = f" (срок {c['deadline'] or c['due_iso']})" if (c.get("deadline") or c.get("due_iso")) else ""
    return f"{who}{c['text']}{due}"


def _card(key: str, *, tag: str, urgency: int, title: str, source: str, items: list[dict[str, Any]], prompt: str) -> dict[str, Any]:
    from assistant.services.proactive import _key

    lines = [_line(c) for c in items[:MAX_LINES]]
    return {
        "key": _key("risk", key, ",".join(sorted(c["id"] for c in items))),
        "type": "risk",
        "tag": tag,
        "urgency": urgency,
        "title": title,
        "source": source,
        "ask": title + ": " + "; ".join(lines),
        # без "items": кнопка «Разобрать» у карточек с пунктами закрывает пункт Фокуса по ключу,
        # а здесь пункты — обязательства реестра; карточка просто скрывается после разбора
        "lines": lines,
        "actions": [{"label": "Разобрать с Лео", "act": "assistant", "primary": True,
                     "payload": {"prompt": prompt + "\n" + "\n".join("— " + x for x in lines)}}],
    }


def _meeting_sources(c: dict[str, Any]) -> int:
    return len({str(s.get("id")) for s in c.get("sources") or [] if s.get("note_kind") == "transcription"})


def risk_cards(uid: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    from assistant.services import focus_items

    now = now or datetime.now(timezone.utc)
    open_ = [c for c in focus_items.commitments(uid, status="open", now=now, limit=500)
             if not (c.get("link") or {}).get("task_id")]
    cards: list[dict[str, Any]] = []

    overdue_mine = [c for c in open_ if c["status"] == "overdue" and c["direction"] == "mine"]
    if overdue_mine:
        n = len(overdue_mine)
        cards.append(_card(
            "overdue_mine", tag="⏰ ваши обещания", urgency=3,
            title=f"{n} {_plural(n, 'ваше обещание просрочено', 'ваших обещания просрочены', 'ваших обещаний просрочено')}",
            source="из встреч, заметок и чатов", items=overdue_mine,
            prompt="Мои просроченные обещания — помоги разобрать: что закрыть, что перенести, кому написать.",
        ))
    overdue_theirs = [c for c in open_ if c["status"] == "overdue" and c["direction"] == "theirs"]
    if overdue_theirs:
        n = len(overdue_theirs)
        names = []
        for c in overdue_theirs:
            if c["who"] and c["who"] not in names:
                names.append(c["who"])
        cards.append(_card(
            "overdue_theirs", tag="⏰ ждёте от других", urgency=2,
            title=f"{n} {_plural(n, 'договорённость', 'договорённости', 'договорённостей')} с другими просрочено",
            source=", ".join(names[:4]), items=overdue_theirs,
            prompt="Другие просрочили договорённости со мной — подготовь короткие напоминания каждому.",
        ))
    stalled = [c for c in open_ if _meeting_sources(c) >= STALLED_MEETINGS]
    if stalled:
        n = len(stalled)
        cards.append(_card(
            "stalled", tag="🔁 буксует", urgency=2,
            title=f"{n} {_plural(n, 'задача обсуждается', 'задачи обсуждаются', 'задач обсуждаются')} на {STALLED_MEETINGS}+ встречах без движения",
            source="повторяется в саммари встреч", items=stalled,
            prompt="Эти задачи всплывают на встречах снова и снова — помоги понять, что их блокирует, и назначить ответственного и срок.",
        ))
    horizon = (now + NO_OWNER_HORIZON).date().isoformat()
    no_owner = [c for c in open_ if c["direction"] == "unknown" and c.get("due_iso") and c["due_iso"] <= horizon]
    if no_owner:
        n = len(no_owner)
        cards.append(_card(
            "no_owner", tag="❓ нет ответственного", urgency=2,
            title=f"Срок близко, а ответственного нет: {n} {_plural(n, 'задача', 'задачи', 'задач')}",
            source="исполнитель не назван на встрече", items=no_owner,
            prompt="У этих задач скоро срок, но не назначен ответственный — предложи, кому их поставить.",
        ))
    return cards
