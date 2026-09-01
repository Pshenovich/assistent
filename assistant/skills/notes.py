"""Заметки: локально на сервере (SQLite)."""

from __future__ import annotations

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib.telegram_message import reply_formatted
from assistant.lib.telegram_rich import promote_section_headings
from assistant.nlu import llm as nlu_llm
from assistant.stores import notes as notes_store


async def _reply_chunks(msg, text: str) -> None:
    await reply_formatted(msg, text)


def _format_note_message(note: dict) -> str:
    title = (note.get("title") or "").strip() or "(без названия)"
    body = (note.get("body") or note.get("description") or "").strip()
    if body and ("<" in body and ">" in body):
        content = promote_section_headings(body)
        return f"<h2>{html.escape(title)}</h2>\n{content}"
    if body:
        return f"<h2>{html.escape(title)}</h2>\n<p>{html.escape(body).replace(chr(10), '<br>')}</p>"
    return f"<h2>{html.escape(title)}</h2>"


async def handle_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    q = (query or "").strip()
    if not q:
        await msg.reply_text("Напишите, что искать, например: «найди заметку про филиалы в Куркино».")
        return
    uid = int(user.id)
    matches = await asyncio.to_thread(notes_store.search_notes, uid, q, limit=5)
    if not matches:
        await msg.reply_text(f"Заметок по запросу «{q}» не найдено.")
        return
    best = matches[0]
    await _reply_chunks(msg, _format_note_message(best))
    if len(matches) > 1:
        others = ", ".join(
            (m.get("title") or "(без названия)")[:60] for m in matches[1:3]
        )
        await msg.reply_text(
            f"Ещё похожие ({len(matches) - 1}): {others}"
            + ("…" if len(matches) > 3 else "")
        )


def _fallback_title(text: str) -> str:
    first = (text or "").strip().split("\n", 1)[0].strip()
    if not first:
        return "Заметка"
    if len(first) <= 80:
        return first
    return first[:77].rstrip() + "…"


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    uid = int(user.id)
    # Текст автора не меняем — ИИ только предлагает название.
    body = (text or "").strip()
    title = _fallback_title(body)
    if body:
        formatted = await asyncio.to_thread(nlu_llm.format_note, body)
        if formatted and (formatted.get("title") or "").strip():
            title = formatted["title"].strip()
    await asyncio.to_thread(notes_store.create_note, uid, title, body)
    await msg.reply_text("Заметка сохранена. Откройте раздел «Заметки» в мини-приложении.")
