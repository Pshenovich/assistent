"""Форматирование сообщений Executive Board для Telegram."""

from __future__ import annotations

import html
import os
import random
from typing import Any

from assistant.board.models import AGENT_META, AgentResponse, ChairDecision
from assistant.lib.telegram_html import split_telegram_html

_CHUNK = 3800


def _e(text: str) -> str:
    return html.escape((text or "").strip())


def format_agent_message(agent: str, resp: AgentResponse) -> str:
    meta = AGENT_META.get(agent, {"emoji": "•", "title": agent, "focus": ""})
    lines = [f"{meta['emoji']} <b>{_e(meta['title'])}</b>", "", _e(resp.position)]
    if resp.disagreement:
        lines.append("")
        lines.append("<i>Возражение</i>")
        for item in resp.disagreement[:3]:
            lines.append(f"• {_e(item)}")
    if resp.proposal:
        label = {
            "P": "🎯 KPI / действие",
            "A": "⚠️ Риск / процесс",
            "E": "💡 Предлагаю",
            "I": "👥 Команда",
        }.get(agent, "Предлагаю")
        lines.append("")
        lines.append(f"<b>{label}</b>")
        lines.append(_e(resp.proposal))
    if resp.changed_position:
        lines.append("")
        lines.append("<i>Позиция изменена после аргументов коллег.</i>")
    return "\n".join(lines).strip()


def format_start_banner(meeting_no: int, title: str) -> str:
    label = _e(title or "Новое совещание")
    return (
        "━━━━━━━━━━━━━━\n"
        f"<b>MEETING #{int(meeting_no)}</b>\n"
        f"{label}\n"
        "━━━━━━━━━━━━━━\n\n"
        "🧠 Запускаю управленческое совещание.\n\n"
        "P • A • E • I анализируют ситуацию.\n\n"
        "Я вернусь с решением."
    )


def format_agent_unavailable(agent: str) -> str:
    return f"⚠️ {_e(agent)} временно недоступен. Продолжаем обсуждение без него."


def format_board_status(
    *,
    phase: str,
    speaking: str | None = None,
    unavailable: list[str] | None = None,
    spoken: list[str] | None = None,
) -> str:
    phase_label = {
        "ANALYZING": "Анализ",
        "DISCUSSION": "Обсуждение",
        "CHALLENGE": "Challenge",
        "SYNTHESIS": "Итог",
    }.get(phase, phase)
    lines = [f"🧠 <b>Совещание</b> · {_e(phase_label)}"]
    if speaking:
        meta = AGENT_META.get(speaking, {"emoji": "•"})
        lines.append(f"{meta.get('emoji', '•')} {_e(speaking)} готовит ответ…")
    if spoken:
        marks = []
        for agent in spoken:
            meta = AGENT_META.get(agent, {"emoji": "•"})
            marks.append(f"{meta.get('emoji', '')}{_e(agent)}")
        lines.append("Ответили: " + " ".join(marks))
    if unavailable:
        lines.append(
            "⚠️ Недоступны: "
            + ", ".join(_e(a) for a in unavailable)
            + " — продолжаем без них"
        )
    return "\n".join(lines)


def format_company_overview(
    notes: str,
    documents: list[dict[str, Any]],
    *,
    source_url: str = "",
    live: dict[str, Any] | None = None,
    live_error: str | None = None,
) -> str:
    lines = ["Контекст компании для агентов:"]
    if source_url:
        lines.append("")
        lines.append(f"Живой документ: {source_url}")
        if live:
            title = str(live.get("title") or "Документ")
            updated = str(live.get("updated_at") or "").strip()
            n = len(str(live.get("text") or ""))
            extra = " (кэш, если миниапп недоступен)" if live.get("stale") else ""
            lines.append(f"«{title}»{extra}, {n} симв." + (f", обновлён {updated}" if updated else ""))
            preview = (live.get("text") or "").strip()
            if preview:
                lines.append(preview if len(preview) <= 900 else preview[:899] + "…")
        elif live_error:
            lines.append(f"Не удалось загрузить: {live_error}")
        else:
            lines.append("Будет подтягиваться в каждое совещание.")
    notes = (notes or "").strip()
    if notes:
        preview = notes if len(notes) <= 1200 else notes[:1199] + "…"
        lines.append("")
        lines.append("Заметки:")
        lines.append(preview)
    elif not source_url:
        lines.append("")
        lines.append("Заметок нет. Можно написать /company текст или прислать share-ссылку из Leo.")
    files = [d for d in documents if str(d.get("kind") or "") != "live"]
    if files:
        lines.append("")
        lines.append("Загруженные файлы:")
        for i, doc in enumerate(files, 1):
            name = str(doc.get("filename") or "document")
            kind = str(doc.get("kind") or "general")
            n = len(str(doc.get("text") or ""))
            label = {"products": "продукты", "team": "команда"}.get(kind, "общий")
            lines.append(f"{i}. {name} — {label}, {n} симв.")
    lines.append("")
    lines.append(
        "Живой документ: /company и ссылка из миниаппа Leo (/share/…).\n"
        "Или пришлите файл .txt / .md / .csv / .docx.\n"
        "/company_clear — удалить загруженные файлы (ссылку не трогает)."
    )
    return "\n".join(lines)


def format_decision(decision: ChairDecision, *, meeting_no: int | None = None) -> str:
    why = "\n".join(f"• {_e(x)}" for x in decision.why[:5]) or "• —"
    actions = []
    for i, act in enumerate(decision.actions[:6], 1):
        if isinstance(act, dict):
            actions.append(f"{i}. {_e(str(act.get('action') or act))}")
        else:
            actions.append(f"{i}. {_e(str(act))}")
    kpis = "\n".join(f"• {_e(x)}" for x in decision.kpis[:4]) or "• —"
    risks = "\n".join(f"• {_e(x)}" for x in decision.risks[:3]) or "• —"
    dont = "\n".join(f"• {_e(x)}" for x in decision.do_not_do[:4]) or "• —"
    conf = int(round(float(decision.confidence or 0) * 100))
    head = "🧠 EXECUTIVE DECISION"
    if meeting_no:
        head = f"🧠 EXECUTIVE DECISION · #{int(meeting_no)}"
    extra = ""
    if decision.unavailable_agents:
        extra = (
            "\n\n<i>Недоступны: "
            + ", ".join(_e(a) for a in decision.unavailable_agents)
            + "</i>"
        )
    return (
        "━━━━━━━━━━━━━━━━━━\n"
        f"<b>{head}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Решение</b>\n{_e(decision.decision)}\n\n"
        f"<b>Почему</b>\n{why}\n\n"
        f"<b>Что делаем</b>\n"
        + ("\n".join(actions) or "—")
        + f"\n\n<b>KPI</b>\n{kpis}\n\n"
        f"<b>Риск</b>\n{risks}\n\n"
        f"<b>Не делать</b>\n{dont}\n\n"
        f"Confidence: {conf}%"
        f"{extra}\n"
        "━━━━━━━━━━━━━━━━━━"
    )


def format_why(decision: dict[str, Any]) -> str:
    why = decision.get("why") or []
    payload = decision.get("payload") or {}
    lines = ["<b>🎯 Почему такое решение</b>", ""]
    for item in why:
        lines.append(f"• {_e(str(item))}")
    assumptions = decision.get("assumptions") or payload.get("assumptions") or []
    if assumptions:
        lines.append("")
        lines.append("<b>Предположения</b>")
        for item in assumptions[:6]:
            lines.append(f"• {_e(str(item))}")
    if not why:
        lines.append("Аргументы не сохранены.")
    return "\n".join(lines)


def format_risks(decision: dict[str, Any]) -> str:
    risks = decision.get("risks") or []
    dont = decision.get("do_not_do") or []
    lines = ["<b>⚠️ Риски и контраргументы</b>", ""]
    for item in risks:
        lines.append(f"• {_e(str(item))}")
    if dont:
        lines.append("")
        lines.append("<b>Не делать</b>")
        for item in dont:
            lines.append(f"• {_e(str(item))}")
    if not risks and not dont:
        lines.append("Риски не указаны.")
    return "\n".join(lines)


def format_transcript(messages: list[dict[str, Any]]) -> list[str]:
    blocks = ["<b>💬 Полная дискуссия</b>", ""]
    for m in messages:
        agent = str(m.get("agent") or "?")
        meta = AGENT_META.get(agent, {"emoji": "•", "title": agent})
        blocks.append(f"{meta['emoji']} <b>{_e(meta['title'])}</b>")
        blocks.append(_e(str(m.get("content") or "")))
        blocks.append("")
    text = "\n".join(blocks).strip()
    return split_telegram_html(text, _CHUNK) or [text[:_CHUNK]]


def format_history(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "История совещаний пуста."
    lines = ["<b>История решений</b>", ""]
    for i, d in enumerate(decisions[:15], 1):
        day = str(d.get("created_at") or "")[:10]
        problem = _e(str(d.get("problem") or "")[:80])
        dec = _e(str(d.get("decision") or "")[:120])
        lines.append(f"{i}. <b>{day}</b> — {problem}\n<i>{dec}</i>")
    return "\n\n".join(lines)


def format_status(meeting: dict[str, Any]) -> str:
    return (
        f"Статус: <b>{_e(str(meeting.get('status') or ''))}</b>\n"
        f"Раунд: {int(meeting.get('current_round') or 1)} / {int(meeting.get('max_rounds') or 4)}\n"
        f"Сообщений: {int(meeting.get('message_count') or 0)}\n"
        f"Тема: {_e(str(meeting.get('title') or meeting.get('original_question') or '')[:200])}"
    )


def format_followup(decision: dict[str, Any]) -> str:
    day = str(decision.get("created_at") or "")[:10]
    kpis = decision.get("kpis") or []
    kpi_lines = "\n".join(f"• {_e(str(x))}" for x in kpis[:6]) or "• результаты эксперимента"
    return (
        f"📅 Follow-up по решению от {_e(day)}.\n\n"
        f"Мы решили: {_e(str(decision.get('decision') or ''))}\n\n"
        f"Какие результаты?\nНужны:\n{kpi_lines}"
    )


def format_review(review: dict[str, Any]) -> str:
    verdict = _e(str(review.get("verdict") or review.get("summary") or "Обзор готов."))
    lessons = review.get("lessons") or review.get("why") or []
    lines = ["<b>POST-DECISION REVIEW</b>", "", verdict]
    if lessons:
        lines.append("")
        lines.append("<b>Выводы</b>")
        for item in lessons[:6]:
            lines.append(f"• {_e(str(item))}")
    return "\n".join(lines)


def agent_delay_sec() -> float:
    try:
        lo = float(os.getenv("BOARD_DELAY_MIN_SEC", "2") or "2")
        hi = float(os.getenv("BOARD_DELAY_MAX_SEC", "6") or "6")
    except ValueError:
        lo, hi = 2.0, 6.0
    if hi < lo:
        hi = lo
    return random.uniform(lo, hi)


def decision_keyboard_rows(meeting_id: str) -> list[list[dict[str, str]]]:
    mid = meeting_id
    return [
        [
            {"text": "💬 Полная дискуссия", "callback_data": f"bd:debate:{mid}"[:64]},
        ],
        [
            {"text": "🎯 Почему", "callback_data": f"bd:why:{mid}"[:64]},
            {"text": "⚠️ Риски", "callback_data": f"bd:risk:{mid}"[:64]},
        ],
        [
            {"text": "🔄 Пересмотреть", "callback_data": f"bd:redo:{mid}"[:64]},
        ],
    ]
