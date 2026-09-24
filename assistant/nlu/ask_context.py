"""Сборка стабильного префикса для GPT в заметке: БЗ + заметка, бюджеты."""

from __future__ import annotations

NOTE_BUDGET = 8000
QUOTE_BUDGET = 2000
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


def pack_note_sheets(
    sheets: list[dict[str, str]],
    *,
    selected_ids: list[str] | None = None,
    focus_id: str = "main",
    budget: int = NOTE_BUDGET,
) -> str:
    """Склейка выбранных листов с подписями. Фокусный лист забирает ~70% бюджета."""
    want = [str(x).strip() for x in (selected_ids or []) if str(x).strip()]
    if not want:
        return ""
    by_id = {str(row.get("id") or ""): row for row in sheets if row}
    ordered = [by_id[sid] for sid in want if sid in by_id]
    if not ordered:
        return ""
    lim = max(200, int(budget))
    focus = str(focus_id or "main")
    focus_row = next((row for row in ordered if str(row.get("id")) == focus), None)
    rest = [row for row in ordered if str(row.get("id")) != focus]
    if focus_row is None:
        focus_row = ordered[0]
        rest = ordered[1:]
    focus_budget = int(lim * 0.7) if rest else lim
    rest_budget = max(120, lim - focus_budget)

    def _block(row: dict[str, str], size: int) -> str:
        title = str(row.get("title") or "Лист").strip() or "Лист"
        body = clip_text(str(row.get("body") or ""), size)
        return f"[{title}]\n{body}" if body else f"[{title}]"

    chunks = [_block(focus_row, focus_budget)]
    if rest:
        each = max(80, rest_budget // len(rest))
        chunks.extend(_block(row, each) for row in rest)
    return clip_text("\n\n".join(chunks), lim)


def pack_knowledge_brief(
    user_id: int | str,
    query: str,
    *,
    budget: int = KB_BUDGET,
    note_ids: list[str] | None = None,
) -> tuple[str, str]:
    """Бриф БЗ + knowledge_version. Без дополнительного LLM-вызова."""
    from assistant.board.company_brief import format_company_context
    from assistant.board.context import attach_knowledge_note
    from assistant.stores import notes as notes_store

    version = notes_store.knowledge_version(user_id)
    pack = attach_knowledge_note(None, user_id, note_ids=note_ids)
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


def _quote_fragments(quote: str = "", quotes: list[str] | None = None) -> list[str]:
    items: list[str] = []
    for raw in quotes or []:
        piece = clip_text(str(raw or "").strip(), QUOTE_BUDGET)
        if piece:
            items.append(piece)
        if len(items) >= 5:
            break
    if items:
        return items
    single = clip_text(quote or "", QUOTE_BUDGET)
    return [single] if single else []


def assemble_ask_messages(
    *,
    question: str,
    context: str = "",
    note_title: str = "",
    note_text: str = "",
    quote: str = "",
    quotes: list[str] | None = None,
    knowledge_brief: str = "",
) -> tuple[str, str]:
    """Стабильный system-префикс (БЗ+заметка) и user (цитата + вопрос)."""
    prefix = build_context_prefix(
        note_title=note_title,
        note_text=note_text,
        knowledge_brief=knowledge_brief,
    )
    q = (question or "").strip()
    items = _quote_fragments(quote, quotes)
    user_parts: list[str] = []
    if len(items) == 1:
        user_parts.append(f"ВЫДЕЛЕННЫЙ ТЕКСТ:\n{items[0]}")
    elif items:
        block = clip_text("\n".join(f"«{it}»" for it in items), QUOTE_BUDGET)
        user_parts.append(f"ВЫДЕЛЕННЫЕ ФРАГМЕНТЫ:\n{block}")
    if q:
        user_parts.append(f"Вопрос:\n{q}" if items else q)
    user = "\n\n".join(user_parts)
    if prefix:
        return prefix, user
    ctx = (context or "").strip()
    if ctx:
        return "", f"Контекст:\n{ctx[:12000]}\n\nВопрос:\n{q}"
    return "", user
