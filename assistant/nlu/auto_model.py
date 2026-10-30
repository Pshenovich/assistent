"""Режим «Авто» для Лео: модель под вопрос.

Простой общий вопрос — дешёвая модель; всё, где нужны данные пользователя, действия, заметка, документ,
ссылка или рассуждение, — сильная. Правила без вызова ИИ: быстро и бесплатно. Сомнение — в пользу сильной.

Проверено scripts/eval_ask.py (2026-10-30, вопросы пользователя, слепая оценка): Haiku на простых вопросах
не хуже в 14 из 17; действия с календарём дешёвые модели путают (1 ошибка на 7–8 вопросов) — они идут сильной.
"""

from __future__ import annotations

import os
import re
from typing import Any

AUTO_MODEL_ID = "leo/auto"
AUTO_LABEL = "Авто"
AUTO_NOTE = "Сам выбирает модель под вопрос — помогает экономить"

CHEAP_HINT = (
    "Общие вопросы (что такое термин, как сделать что-то в сервисе, приложении, у врача, в банке) отвечай "
    "сразу из общих знаний. В заметках и памяти ищи, только если вопрос явно про проект, продукт, встречу "
    "или людей пользователя. Не отказывайся отвечать на обычные вопросы."
)


def strong_model() -> str:
    return os.getenv("OPENROUTER_MODEL_AUTO_STRONG", "").strip() or "openai/gpt-5.5"


def cheap_model() -> str:
    return os.getenv("OPENROUTER_MODEL_AUTO_CHEAP", "").strip() or "anthropic/claude-haiku-5.5"


def is_auto(model: str | None) -> bool:
    return str(model or "").strip() == AUTO_MODEL_ID


_EDIT_RE = re.compile(
    r"(исправ|перепиш|допиш|добав|удали|убери|замени|сократи|переформулир|оформи|структурир|заполни|"
    r"таблиц|внеси|встав|перенеси\s+в\s+замет|поправь|отредакт|раздел|пункт|абзац|в\s+заметк|в\s+документ)",
    re.I,
)
_CANVAS_RE = re.compile(r"(презентац|слайд|\bдек\b|питч|канвас|canvas|deck)", re.I)
_RESEARCH_RE = re.compile(
    r"(проанализ|анализ|сравни|исслед|стратег|разбер|оцени|обоснуй|посчитай|рассчитай|прогноз|"
    r"бизнес-?план|составь\s+план|юридич|договор|\bкод\b|скрипт|sql|формул)",
    re.I,
)
_TOOLISH_RE = re.compile(
    r"(встреч|задач|напомин|календар|созвон|что\s+решили|обещал|договорил|найди|найти|поищи|ссылк|"
    r"почт|письм|расписан|свобод|занят|перенеси|поставь|запланируй|отмени)",
    re.I,
)
_DOC_RE = re.compile(
    r"(подготов|собер|собра|состав|сдела|напиш|напис|оформ|распис|опиш).{0,40}"
    r"(документ|описани|план|текст|письм|отч[её]т|стать|резюме|\bТЗ\b|спецификац|концепц)",
    re.I,
)


def route(
    question: str,
    *,
    in_note: bool = False,
    history: list[dict[str, Any]] | None = None,
    quote: str = "",
    has_attachments: bool = False,
) -> tuple[str, str]:
    """(cheap|strong, причина)."""
    from assistant.lib import board_ai, chat_links
    from assistant.services import gpt_calendar_tools as g, mail_tools

    q = str(question or "")
    if in_note:
        return "strong", "вопрос в заметке"
    if has_attachments:
        return "strong", "вложение"
    hist = history or []
    if hist:
        last_bot = next((str(m.get("content") or "") for m in reversed(hist) if m.get("role") == "assistant"), "")
        if "[Уже выполнено" in last_bot or "[В чате показаны" in last_bot:
            return "strong", "продолжение действий в календаре"
        if len(q) < 60:  # «Мыздриков», «Пятница 14:00» — смысл в предыдущем вопросе
            prev = next((str(m.get("content") or "") for m in reversed(hist) if m.get("role") == "user"), "")
            if prev and route(prev, quote=quote)[0] == "strong":
                return "strong", "продолжение сложного вопроса"
    if _DOC_RE.search(q):
        return "strong", "создать документ"
    if chat_links.find_links(q):
        return "strong", "ссылка"
    if g.looks_like_calendar_question(q) or mail_tools.looks_like_mail_question(q):
        return "strong", "календарь/почта"
    if _TOOLISH_RE.search(q):
        return "strong", "нужны инструменты"
    if board_ai.looks_like_board_request(q) or _CANVAS_RE.search(q):
        return "strong", "доска/презентация"
    if _RESEARCH_RE.search(q):
        return "strong", "анализ"
    if len(q) > 350 or len(quote or "") > 1500:
        return "strong", "длинный запрос"
    return "cheap", "простой вопрос"


def resolve(model: str | None, question: str, **kw: Any) -> tuple[str, str]:
    """Выбранная модель → модель для запроса и добавка к системной подсказке («Авто» → дешёвая/сильная)."""
    if not is_auto(model):
        return str(model or ""), ""
    kind, why = route(question, **kw)
    picked = cheap_model() if kind == "cheap" else strong_model()
    print(f"[auto_model] {kind} ({why}) → {picked}")
    return picked, (CHEAP_HINT if kind == "cheap" else "")
