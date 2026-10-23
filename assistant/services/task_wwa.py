"""«Описать по WWA»: описание задачи для трекера — Зачем / Что сделать / Критерии приёмки.

Контекст — название, черновик описания, заметка (вокруг выделенного фрагмента) и пункт Фокуса.
Факты Лео не придумывает: чего нет в контексте — «уточнить: …». Результат пользователь правит до отправки.
"""

from __future__ import annotations

import re
from typing import Any

NOTE_CONTEXT_CHARS = 6000

SYSTEM = (
    "Ты помогаешь менеджеру поставить задачу команде разработки. Опиши задачу по методологии WWA.\n"
    "Формат ответа — простой текст, ровно три раздела, без вступления и заключения. "
    "Заголовок раздела — отдельной строкой, как ниже, без звёздочек, решёток и другой разметки; "
    "между разделами пустая строка:\n"
    "Зачем\n"
    "1–3 предложения: какую проблему решаем или какую ценность даём и для кого (клиент, пользователи, команда).\n\n"
    "Что сделать\n"
    "- маркированный список конкретных работ (что должно появиться или измениться)\n\n"
    "Критерии приёмки\n"
    "- маркированный список проверяемых условий: по каждому можно ответить «да/нет»\n\n"
    "Правила: пиши по-русски, коротко и конкретно, без воды и канцелярита. Опирайся только на контекст — "
    "не придумывай цифры, сроки, имена, системы и требования. Если чего-то важного не хватает, добавь пункт "
    "«Уточнить: …» в подходящий раздел. Не повторяй название задачи заголовком. До 1500 символов."
)


def _note_text(uid: int, note_id: Any) -> tuple[str, str]:
    from assistant.lib import note_items
    from assistant.stores import notes as notes_store

    try:
        note = notes_store.get_note(uid, int(note_id))
    except (TypeError, ValueError):
        return "", ""
    if not note:
        return "", ""
    body = str(note.get("body") or "")
    if "<" in body and ">" in body:
        text = "\n".join(r["text"] for r in note_items.lines(body))
    else:
        text = body
    return str(note.get("title") or "").strip(), text.strip()


def _window(text: str, anchor: str, size: int = NOTE_CONTEXT_CHARS) -> str:
    """Кусок заметки вокруг выделенного фрагмента (или начало, если фрагмент не нашёлся)."""
    if len(text) <= size:
        return text
    pos = text.find(anchor.strip()[:80]) if anchor.strip() else -1
    if pos < 0:
        return text[:size]
    start = max(0, pos - size // 2)
    return text[start:start + size]


def _focus_text(uid: int, focus_key: str) -> str:
    from assistant.services import focus_items

    row = focus_items.get(uid, focus_key)
    if not row:
        return ""
    item = focus_items.public(row)
    src = item.get("source") or {}
    parts = [
        f"Пункт: {item.get('text') or ''}",
        f"Исполнитель: {item['assignee']}" if item.get("assignee") else "",
        f"Срок: {item['deadline']}" if item.get("deadline") else "",
        f"Комментарий: {item['comment']}" if item.get("comment") else "",
        f"Источник: {src.get('title') or src.get('label') or ''}" if (src.get("title") or src.get("label")) else "",
        f"Фрагмент источника: {src['item']}" if src.get("item") else "",
    ]
    return "\n".join(p for p in parts if p)


def build_prompt(
    uid: int, *, title: str, description: str = "", note_id: Any = None, focus_key: str = "", context: str = ""
) -> str:
    blocks = [f"Задача: {title.strip()}"]
    if description.strip():
        blocks.append(f"Черновик описания от менеджера (учти всё, что в нём есть):\n{description.strip()[:4000]}")
    if focus_key:
        focus = _focus_text(uid, focus_key)
        if focus:
            blocks.append(f"Откуда задача (пункт Фокуса):\n{focus}")
    if note_id:
        note_title, note_text = _note_text(uid, note_id)
        if note_text:
            anchor = context or title
            blocks.append(f"Заметка «{note_title or 'без названия'}» (контекст):\n{_window(note_text, anchor)}")
    if context.strip():
        blocks.append(f"Выделенный фрагмент:\n{context.strip()[:2000]}")
    return "\n\n".join(blocks)


def describe(
    uid: int, *, title: str, description: str = "", note_id: Any = None, focus_key: str = "", context: str = ""
) -> str:
    from assistant.services.mail_composer import _llm

    if not str(title or "").strip() and not str(description or "").strip():
        raise ValueError("Сначала напишите, что нужно сделать")
    user = build_prompt(
        uid, title=title or description[:200], description=description, note_id=note_id,
        focus_key=focus_key, context=context,
    )
    return clean(_llm(uid, SYSTEM, user, "task_wwa", max_tokens=1200))


def clean(text: str) -> str:
    """Без разметки: менеджер правит обычный текст, жирные заголовки делает конвертер для трекера."""
    lines = []
    for raw in str(text or "").replace("\r\n", "\n").split("\n"):
        line = re.sub(r"^\s*#{1,6}\s+", "", raw)
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"^\s*[*•]\s+", "- ", line)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
