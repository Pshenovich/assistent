"""B4: кнопка «✉️ Фоллоу-ап участникам» под саммари встречи → черновик сообщением."""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from assistant.services import meeting_followup as mf


async def on_followup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data or not q.from_user:
        return
    try:
        note_id = int(q.data[len(mf.CALLBACK_PREFIX):])
    except ValueError:
        await q.answer()
        return
    await q.answer("Готовлю черновик…")
    text = await asyncio.to_thread(mf.build_followup, int(q.from_user.id), note_id)
    chat_id = q.message.chat_id if q.message else int(q.from_user.id)
    if not text:
        await context.bot.send_message(chat_id, "В саммари нет решений и задач — фоллоу-ап собрать не из чего.")
        return
    await context.bot.send_message(chat_id, "✉️ Черновик фоллоу-апа — скопируйте или перешлите участникам:")
    await context.bot.send_message(chat_id, text, disable_web_page_preview=True)


def register_meeting_followup_handlers(app) -> None:
    app.add_handler(CallbackQueryHandler(on_followup, pattern=rf"^{mf.CALLBACK_PREFIX}\d+$"), group=-1)
