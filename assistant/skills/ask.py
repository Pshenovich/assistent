"""Свободные вопросы с контекстом."""

from __future__ import annotations

import asyncio

from telegram import Message, Update
from telegram.ext import ContextTypes

from assistant.nlu import llm as nlu_llm


def _reply_context(msg: Message | None) -> str:
    if not msg:
        return ""
    rep = msg.reply_to_message
    if not rep:
        return ""
    if rep.text:
        return rep.text.strip()
    if rep.caption:
        return rep.caption.strip()
    return ""


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    replied: Message | None = None,
) -> None:
    msg = update.message
    if not msg:
        return
    ctx = _reply_context(msg) if replied else _reply_context(msg)
    try:
        answer = await asyncio.to_thread(nlu_llm.answer_with_context, text, ctx)
        await msg.reply_text((answer or "Не удалось ответить.")[:4000])
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")
