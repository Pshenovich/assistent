"""Свободный Q&A по заметкам, транскрипциям и саммари."""

from __future__ import annotations

import asyncio
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib.journal_retrieval import retrieve_journal_context
from assistant.lib.telegram_markdown import prepare_journal_qa_markdown
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.telegram_status import post_status, take_work_status
from assistant.lib.user_timezone import resolve_user_tz_name
from assistant.nlu import llm as nlu_llm
from assistant.stores import user_prefs


async def handle(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    question = (text or "").strip()
    if not question:
        await msg.reply_text(
            "Задайте вопрос о ваших заметках, транскрипциях или саммари встреч."
        )
        return

    uid = int(user.id)
    tz_name = resolve_user_tz_name(uid)
    now = datetime.now(user_prefs.get_user_tz(uid))

    status = await post_status(msg, "Ищу в архиве…")
    try:
        parsed = await asyncio.to_thread(
            nlu_llm.parse_journal_qa_query,
            question,
            today_iso=now.date().isoformat(),
            tz_name=tz_name,
        )
        fragments, journal_context = await asyncio.to_thread(
            retrieve_journal_context,
            uid,
            parsed,
            original_question=question,
        )
        if not fragments or not journal_context.strip():
            await msg.reply_text(
                "В заметках, транскрипциях и саммари ничего подходящего не нашёл. "
                "Попробуйте уточнить тему или дату."
            )
            return
        answer = await asyncio.to_thread(
            nlu_llm.answer_from_journal_context,
            question,
            journal_context,
        )
        body = prepare_journal_qa_markdown(answer or "Не удалось сформировать ответ.")
        await reply_formatted(
            msg,
            body,
            rich_markdown=True,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await msg.reply_text(f"Ошибка: {e}")
    finally:
        await take_work_status(status)
