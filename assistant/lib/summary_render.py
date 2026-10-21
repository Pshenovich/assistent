"""Рендер саммари v2: JSON фактов → HTML для Rich Message / заметки.

Разделы и задачи собираются кодом, поэтому HTML и meta.tasks не расходятся.
"""

from __future__ import annotations

import html
from typing import Any

from assistant.lib.telegram_rich import tasks_checklist_html

_TYPE_BRIEF_TITLE = {
    "meeting": "📌 Кратко",
    "interview": "📌 Краткое описание",
    "lecture": "📌 О чем запись",
    "brainstorm": "📌 Цель обсуждения",
    "voice_note": "📝 Краткое содержание",
}

_SECTIONS_ORDER = {
    "interview": ["👤 Информация о кандидате", "💪 Сильные стороны", "⚠️ Слабые стороны", "🛠 Навыки и опыт", "❓ Вопросы без ответа", "📝 Итоговая оценка"],
    "lecture": ["🧠 Основные идеи", "📚 Ключевые тезисы", "🔧 Практические рекомендации"],
    "brainstorm": ["💡 Предложенные идеи", "⭐ Самые перспективные идеи", "⚠️ Риски"],
    "voice_note": ["💡 Основные мысли", "🎯 Главная идея", "📌 Важные факты"],
}


def _e(s: Any) -> str:
    return html.escape(str(s or "").strip())


def _ul(items: list[str]) -> str:
    lis = "".join(f"<li>{_e(x)}</li>" for x in items if str(x or "").strip())
    return f"<ul>{lis}</ul>" if lis else ""


def _section(title: str, body: str) -> str:
    return f"<h2>{_e(title)}</h2>{body}" if body else ""


def _brief(text: str) -> str:
    paras = [p.strip() for p in str(text or "").split("\n") if p.strip()]
    return "".join(f"<p>{_e(p)}</p>" for p in paras)


def _discussion(items: Any) -> str:
    out: list[str] = []
    for d in items or []:
        if not isinstance(d, dict):
            continue
        topic = str(d.get("topic") or "").strip()
        points = [str(p).strip() for p in d.get("points") or [] if str(p).strip()]
        if not topic and not points:
            continue
        if topic:
            out.append(f"<h3>{_e(topic)}</h3>")
        out.append(_ul(points))
    return "".join(out)


def _tasks(tasks: list[dict[str, Any]]) -> str:
    # data-task — открытый пункт: html_to_rich_markdown превращает его в «- [ ]»
    todo = tasks_checklist_html(tasks, completed=False).replace("<li>", "<li data-task>")
    done = tasks_checklist_html(tasks, completed=True)
    return "\n".join(x for x in (done, todo) if x)


def _speakers(speaker_map: dict[str, dict[str, Any]]) -> str:
    rows = []
    for label, info in sorted(speaker_map.items()):
        name = str(info.get("name") or "").strip()
        if not name:
            continue
        sure = info.get("confirmed") or float(info.get("confidence") or 0) >= 0.75
        rows.append(f"{label} — {name}" + ("" if sure else " (предположительно)"))
    if not rows:
        return ""
    return f"<p><tg-spoiler>🗣 {_e('; '.join(rows))}</tg-spoiler></p>"


def render_summary_html(data: dict[str, Any]) -> str:
    ctype = str(data.get("content_type") or "meeting")
    parts: list[str] = []
    parts.append(_section(_TYPE_BRIEF_TITLE.get(ctype, "📌 Кратко"), _brief(data.get("brief") or "")))
    tasks = [t for t in data.get("tasks") or [] if isinstance(t, dict)]
    if ctype == "meeting":
        parts.append(_section("🎯 Что обсуждали", _discussion(data.get("discussion"))))
        parts.append(_section("✅ Принятые решения", _ul([d.get("text", "") for d in data.get("decisions") or [] if isinstance(d, dict)])))
        parts.append(_tasks(tasks))
        parts.append(_section("❓ Открытые вопросы", _ul(data.get("open_questions") or [])))
        parts.append(_section("⚠️ Риски и блокеры", _ul(data.get("risks") or [])))
        parts.append(_section("🔜 Следующие шаги", _ul(data.get("next_steps") or [])))
    else:
        sections = {
            str(s.get("title") or "").strip(): [str(i).strip() for i in s.get("items") or [] if str(i).strip()]
            for s in data.get("sections") or []
            if isinstance(s, dict)
        }
        order = _SECTIONS_ORDER.get(ctype, [])
        for title in order + [t for t in sections if t not in order]:
            parts.append(_section(title, _ul(sections.get(title) or [])))
        if tasks:
            parts.append(_tasks(tasks))
        if not sections:
            parts.append(_section("🎯 Что обсуждали", _discussion(data.get("discussion"))))
    parts.append(_speakers(data.get("speaker_map") or {}))
    return "\n\n".join(p for p in parts if p).strip()
