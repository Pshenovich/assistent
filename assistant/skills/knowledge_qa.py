"""Q&A по корпоративной базе знаний."""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib.knowledge_retrieval import (
    describe_kb_index_issues,
    format_kb_direct_answer,
    retrieve_kb_context,
    should_quote_kb_context_directly,
)
from assistant.lib.knowledge_urls import format_kb_sources_footer_html, strip_kb_source_line
from assistant.lib.telegram_markdown import prepare_journal_qa_markdown
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.telegram_status import post_status, take_work_status
from assistant.nlu import llm as nlu_llm
from assistant.stores import knowledge_base_store as kb_store


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
            "Задайте вопрос по базе знаний, например: "
            "«найди в базе инфу про интеграции»."
        )
        return

    uid = int(user.id)
    if not kb_store.user_has_accessible_kbs(uid):
        await msg.reply_text(
            "У вас нет подключённой базы знаний. "
            "Подключите её в мини-приложении: Профиль → База знаний."
        )
        return

    status = await post_status(msg, "Ищу в базе знаний…")
    try:
        kbs = kb_store.list_knowledge_bases_for_user(uid)
        kb_titles = [
            {"id": str(kb["id"]), "title": str(kb.get("title") or "")} for kb in kbs
        ]
        parsed = await asyncio.to_thread(
            nlu_llm.parse_kb_qa_query,
            question,
            knowledge_bases=kb_titles,
        )
        if not parsed or not str(parsed.get("search_query") or "").strip():
            from assistant.lib.kb_search import extract_kb_search_query

            local = extract_kb_search_query(question)
            parsed = dict(parsed or {})
            if local:
                parsed["search_query"] = local
        fragments, kb_context = await asyncio.to_thread(
            retrieve_kb_context,
            uid,
            parsed or {"search_query": question},
            original_question=question,
        )
        if not fragments or not kb_context.strip():
            index_issue = describe_kb_index_issues(kbs)
            if index_issue:
                await msg.reply_text(index_issue)
            else:
                await msg.reply_text(
                    "В базе знаний ничего подходящего не нашёл. "
                    "Попробуйте уточнить запрос или обновите базу в мини-приложении."
                )
            return
        if should_quote_kb_context_directly(question):
            body = prepare_journal_qa_markdown(format_kb_direct_answer(fragments))
        else:
            answer = await asyncio.to_thread(
                nlu_llm.answer_from_kb_context,
                question,
                kb_context,
            )
            answer = strip_kb_source_line(answer or "")
            from assistant.lib.kb_html_format import normalize_kb_answer_markdown

            body = prepare_journal_qa_markdown(
                normalize_kb_answer_markdown(answer or "Не удалось сформировать ответ.")
            )
        footer = format_kb_sources_footer_html(fragments)
        if footer:
            body = f"{body.rstrip()}\n\n{footer}"
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
