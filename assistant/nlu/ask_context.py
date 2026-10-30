"""Сборка стабильного префикса для GPT в заметке: БЗ + заметка, бюджеты."""

from __future__ import annotations

NOTE_BUDGET = 8000
QUOTE_BUDGET = 2000
KB_BUDGET = 3500
ATTACHED_NOTE_BUDGET = 8000
ATTACHED_NOTE_EACH = 2500
ATTACHED_NOTE_MAX = 5
_EMPTY_BRIEFS = {
    "",
    "(пусто)",
    "(не задан — живой документ компании недоступен)",
}


def same_note_text(note_text: str, view: str) -> bool:
    """Текст заметки уже целиком есть в открытом листе (слова совпадают на 90%) — второй раз не нужен."""
    import re

    words = re.findall(r"\w{3,}", (note_text or "").lower())
    if not words:
        return True
    have = set(re.findall(r"\w{3,}", (view or "").lower()))
    return sum(1 for w in words if w in have) >= 0.9 * len(words)


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


def clean_note_ids(raw: list[str] | None, *, limit: int = ATTACHED_NOTE_MAX) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        sid = str(item or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _note_chat_transcript(user_id: int | str, sid: str) -> tuple[str, str]:
    """«chat:<note_id>:<chat_id>» → (заголовок, переписка) — только видимый пользователю чат."""
    from assistant.stores import note_chats
    from assistant.stores import notes as notes_store

    parts = sid.split(":")
    if len(parts) != 3:
        return "", ""
    try:
        note_id, chat_id = int(parts[1]), int(parts[2])
    except (TypeError, ValueError):
        return "", ""
    note = notes_store.get_accessible_note(user_id, note_id)
    if not note:
        return "", ""
    owner = str(note.get("owner_user_id") or note.get("user_id") or "")
    viewer = str(int(user_id))
    if chat_id == note_chats.COMMENTS_CHAT_ID:
        title = "Комментарии к заметке «" + str(note.get("title") or "Заметка") + "»"
    else:
        chat = note_chats.chat_for_item(chat_id, owner, "local", str(note_id))
        if not note_chats.can_view(chat, viewer):
            return "", ""
        assert chat is not None
        title = "Чат заметки «" + str(note.get("title") or "Заметка") + "»"
    rows = [
        r
        for r in note_chats.visible_comments(owner, "local", str(note_id), viewer)
        if r.get("chat_id") == chat_id
    ]
    if not rows:
        return "", ""
    lines = []
    for r in rows:
        who = str(r.get("author_name") or "Участник").strip()
        text = " ".join(str(r.get("body") or "").split())
        if text:
            lines.append(f"{who}: {text}")
    return title, "\n".join(lines)


def pack_attached_notes(
    user_id: int | str,
    note_ids: list[str] | None,
    *,
    budget: int = ATTACHED_NOTE_BUDGET,
    each: int = ATTACHED_NOTE_EACH,
) -> str:
    """Тексты обычных заметок и journal-записей (транскрипции/саммари), к которым есть доступ."""
    from assistant.lib.telegram_html import html_to_plain, uses_html_markup
    from assistant.lib.usage_store import get_user_usage_event, journal_text_from_raw
    from assistant.stores import notes as notes_store

    ids = clean_note_ids(note_ids)
    if not ids:
        return ""
    chunks: list[str] = []
    remain = max(200, int(budget))
    for sid in ids:
        title = ""
        body = ""
        if sid.startswith("chat:"):
            title, body = _note_chat_transcript(user_id, sid)
            if not title:
                continue
        elif sid.startswith("journal:"):
            raw_id = sid.split(":", 1)[1].strip()
            try:
                eid = int(raw_id)
            except (TypeError, ValueError):
                continue
            row = get_user_usage_event(str(int(user_id)), eid)
            if not row:
                continue
            raw = row.get("raw_usage_json")
            body = journal_text_from_raw(raw if isinstance(raw, str) else None)
            op = str(row.get("operation") or "")
            title = "Саммари" if op == "summarize" else "Транскрипция"
            # Prefer topic from raw JSON if present
            try:
                import json

                parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
                if isinstance(parsed, dict):
                    topic = (
                        str(parsed.get("main_topic") or "").strip()
                        or str(parsed.get("meeting_topic") or "").strip()
                        or str(parsed.get("title") or "").strip()
                    )
                    if topic:
                        title = topic[:120]
            except Exception:
                pass
        else:
            try:
                nid = int(sid)
            except (TypeError, ValueError):
                continue
            row = notes_store.get_accessible_note(user_id, nid)
            if not row or row.get("is_knowledge"):
                continue
            title = str(row.get("title") or "Заметка").strip() or "Заметка"
            body = str(row.get("body") or row.get("description") or "")
        if uses_html_markup(body):
            body = html_to_plain(body)
        size = min(max(80, int(each)), remain)
        piece = clip_text(body, size)
        block = f"[{title}]\n{piece}" if piece else f"[{title}]"
        chunks.append(block)
        remain -= len(block) + 2
        if remain < 80:
            break
    return clip_text("\n\n".join(chunks), budget)


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
    attached_notes: str = "",
    open_note: str = "",
) -> str:
    """open_note — открытый лист целиком (<open-note>, для правок): в кэшируемой части, а не в вопросе."""
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
    opened = (open_note or "").strip()
    if opened:
        parts.append(f"ОТКРЫТЫЙ ЛИСТ ЗАМЕТКИ:\n{opened}")
    extra = (attached_notes or "").strip()
    if extra:
        parts.append(f"ПРИКРЕПЛЁННЫЕ ЗАМЕТКИ:\n{extra}")
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
    attached_notes: str = "",
    open_note: str = "",
) -> tuple[str, str]:
    """Стабильный system-префикс (БЗ+заметка) и user (цитата + вопрос)."""
    prefix = build_context_prefix(
        note_title=note_title,
        note_text=note_text,
        knowledge_brief=knowledge_brief,
        attached_notes=attached_notes,
        open_note=open_note,
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
