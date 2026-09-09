"""Сборка стабильного префикса для GPT в заметке: БЗ + заметка, бюджеты."""

from __future__ import annotations

NOTE_BUDGET = 8000
QUOTE_BUDGET = 500
KB_BUDGET = 3500
_EMPTY_BRIEFS = {
    "",
    "(пусто)",
    "(не задан — живой документ компании недоступен)",
}


def clip_text(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def pack_knowledge_brief(
    user_id: int | str,
    query: str,
    *,
    budget: int = KB_BUDGET,
) -> tuple[str, str]:
    """Бриф БЗ + knowledge_version. Без дополнительного LLM-вызова."""
    from assistant.board.company_brief import format_company_context
    from assistant.board.context import attach_knowledge_note
    from assistant.stores import notes as notes_store

    version = notes_store.knowledge_version(user_id)
    pack = attach_knowledge_note(None, user_id)
    if not pack:
        return "", version
    brief = format_company_context(pack, query=query or "", budget=budget).strip()
    if brief in _EMPTY_BRIEFS or brief.startswith("(живой документ недоступен"):
        return "", version
    return brief, version


def build_context_prefix(
    *,
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    knowledge_brief: str = "",
) -> str:
    del quote  # цитата волатильна — в user, не в кэшируемый префикс
    parts: list[str] = []
    kb = (knowledge_brief or "").strip()
    if kb:
        parts.append(f"БАЗА ЗНАНИЙ:\n{kb}")
    title = (note_title or "").strip()
    body = clip_text(note_text or "", NOTE_BUDGET)
    note = "\n\n".join(p for p in (title, body) if p)
    if note:
        parts.append(f"ЗАМЕТКА:\n{note}")
    return "\n\n".join(parts)


def assemble_ask_messages(
    *,
    question: str,
    context: str = "",
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    knowledge_brief: str = "",
) -> tuple[str, str]:
    """Стабильный system-префикс (БЗ+заметка) и user (цитата + вопрос)."""
    prefix = build_context_prefix(
        note_title=note_title,
        note_text=note_text,
        knowledge_brief=knowledge_brief,
    )
    q = (question or "").strip()
    quoted = clip_text(quote or "", QUOTE_BUDGET)
    user_parts: list[str] = []
    if quoted:
        user_parts.append(f"ВЫДЕЛЕННЫЙ ТЕКСТ:\n{quoted}")
    if q:
        user_parts.append(q if not quoted else f"Вопрос:\n{q}")
    user = "\n\n".join(user_parts)
    if prefix:
        return prefix, user
    ctx = (context or "").strip()
    if ctx:
        return "", f"Контекст:\n{ctx[:12000]}\n\nВопрос:\n{q}"
    return "", user
