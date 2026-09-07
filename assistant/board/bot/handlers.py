"""Хендлеры Telegram-бота Executive Board."""

from __future__ import annotations

import os
import re
from typing import Any

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from assistant.board import store
from assistant.board.decision import parse_chair_decision
from assistant.board.docs import detect_doc_kind, extract_document_text, kind_label
from assistant.board.followup import review_decision
from assistant.board.memory import find_related_decisions, history_mentions_past
from assistant.board.meeting import MeetingService, chat_has_running
from assistant.board.renderer import (
    format_company_overview,
    format_decision,
    format_history,
    format_risks,
    format_status,
    format_transcript,
    format_why,
)
from assistant.board.share_source import (
    fetch_share_document,
    looks_like_share_source,
    normalize_share_url,
    resolve_company_share,
)
from assistant.bot.access_gate import ensure_access
from assistant.bot.group_gate import is_bot_mentioned, is_group_chat_type, is_reply_to_bot
from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
from assistant.lib.message_context import strip_bot_mention
from assistant.stores import telegram_registry

_AGENT_RE = re.compile(r"(?<!\w)@([PAEIpaei])\b")
_service: MeetingService | None = None


def _close_stale_meeting(chat_id: int, thread_id: int | None) -> None:
    meeting = store.get_active_meeting(chat_id, thread_id)
    if not meeting:
        return
    if chat_has_running(_chat_key(chat_id, thread_id)):
        return
    store.set_meeting_status(
        str(meeting["id"]), "ERROR", error_message="interrupted (bot restart or LLM failure)"
    )


def _live_meeting(chat_id: int, thread_id: int | None) -> dict[str, Any] | None:
    meeting = store.get_active_meeting(chat_id, thread_id)
    if not meeting:
        return None
    if chat_has_running(_chat_key(chat_id, thread_id)):
        return meeting
    store.set_meeting_status(
        str(meeting["id"]), "ERROR", error_message="interrupted (bot restart or LLM failure)"
    )
    return None


def bind_service(service: MeetingService) -> None:
    global _service
    _service = service


def _svc() -> MeetingService:
    if _service is None:
        raise RuntimeError("MeetingService не инициализирован")
    return _service


def _chat_key(chat_id: int, thread_id: int | None) -> str:
    return f"{chat_id}:{thread_id or 0}"


def _thread_id(update: Update) -> int | None:
    msg = update.effective_message
    if not msg:
        return None
    tid = getattr(msg, "message_thread_id", None)
    return int(tid) if tid else None


def _username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return (
        context.bot.username
        or os.getenv("TG_DONATELLO_BOT_USERNAME")
        or os.getenv("TELEGRAM_BOT_USERNAME")
        or ""
    ).lstrip("@").lower()


def _extract_agent(text: str) -> str | None:
    m = _AGENT_RE.search(text or "")
    if not m:
        return None
    return m.group(1).upper()


def _clean_question(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    t = strip_bot_mention(text or "", bot_username=_username(context))
    t = _AGENT_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


async def _bind_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user:
        set_openrouter_usage_telegram_user(
            telegram_user_id=user.id, telegram_username=user.username
        )
    else:
        set_openrouter_usage_telegram_user(telegram_user_id=None)


async def _maybe_forum_topic(
    update: Update, context: ContextTypes.DEFAULT_TYPE, title: str
) -> int | None:
    chat = update.effective_chat
    if not chat or not getattr(chat, "is_forum", False):
        return _thread_id(update)
    try:
        topic = await context.bot.create_forum_topic(
            chat_id=chat.id, name=f"🧠 {(title or 'Executive Board')[:40]}"
        )
        return int(topic.message_thread_id)
    except Exception as e:
        print(f"[board] forum_topic_fail err={e!r}")
        return _thread_id(update)


async def _launch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
    *,
    extra: str = "",
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if not chat or not user or not question:
        return
    _close_stale_meeting(int(chat.id), _thread_id(update))
    if chat_has_running(_chat_key(chat.id, _thread_id(update))):
        if update.effective_message:
            await update.effective_message.reply_text(
                "Совещание уже идёт. Напишите уточнение или /stop."
            )
        return
    topic_id = await _maybe_forum_topic(update, context, question[:40])
    meeting = _svc().start_meeting(
        user_id=int(user.id),
        chat_id=int(chat.id),
        question=question,
        thread_id=topic_id,
        extra_instruction=extra,
        title=question[:80],
    )
    if topic_id:
        store.update_meeting(meeting["id"], forum_topic_id=topic_id, thread_id=topic_id)
    from assistant.board.meeting import spawn_meeting

    spawn_meeting(_svc(), meeting["id"], _chat_key(chat.id, topic_id))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    if user:
        telegram_registry.register_user(
            telegram_user_id=int(user.id), telegram_username=user.username
        )
    if update.message:
        await update.message.reply_text(
            "AI Executive Board. Напишите управленческий вопрос — "
            "P • A • E • I проведут совещание и вернут решение.\n\n"
            "Команды: /new /status /stop /summary /debate /decision /history /company /me\n"
            "Контекст компании: /company и share-ссылка из Leo, либо файл .txt/.md/.csv/.docx."
        )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    q = " ".join(context.args or []).strip()
    if not q and update.message and update.message.reply_to_message:
        q = (update.message.reply_to_message.text or "").strip()
    if not q:
        if update.message:
            await update.message.reply_text(
                "Напишите вопрос после /new или просто отправьте его следующим сообщением."
            )
        return
    await _launch(update, context, q)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    meeting = _live_meeting(chat.id, _thread_id(update))
    if not meeting:
        await update.message.reply_text("Активного совещания нет.")
        return
    await update.message.reply_text(format_status(meeting), parse_mode=ParseMode.HTML)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    meeting = _live_meeting(chat.id, _thread_id(update))
    if not meeting:
        await update.message.reply_text("Нечего останавливать.")
        return
    from assistant.board.meeting import runtime_for

    runtime_for(str(meeting["id"])).stop.set()
    store.set_meeting_status(str(meeting["id"]), "STOPPED")
    await update.message.reply_text("Останавливаю совещание.")


async def _latest_meeting(chat_id: int) -> dict[str, Any] | None:
    active = store.get_active_meeting(chat_id)
    if active:
        return active
    items = store.list_meetings_for_chat(chat_id, limit=1)
    return items[0] if items else None


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    meeting = await _latest_meeting(chat.id)
    if not meeting:
        await update.message.reply_text("Совещаний ещё не было.")
        return
    decision = store.get_decision_by_meeting(str(meeting["id"]))
    if decision:
        payload = decision.get("payload") or {}
        chair = parse_chair_decision(payload, unavailable=payload.get("unavailable_agents") or [])
        await update.message.reply_text(
            format_decision(chair), parse_mode=ParseMode.HTML
        )
        return
    rounds = store.list_rounds(str(meeting["id"]))
    if rounds:
        await update.message.reply_text(
            (rounds[-1].get("summary") or "Сводка ещё формируется.")[:3500]
        )
        return
    await update.message.reply_text(format_status(meeting), parse_mode=ParseMode.HTML)


async def cmd_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    meeting = await _latest_meeting(chat.id)
    if not meeting:
        await update.message.reply_text("Решений нет.")
        return
    decision = store.get_decision_by_meeting(str(meeting["id"]))
    if not decision:
        await update.message.reply_text("Решение ещё не принято.")
        return
    import html as html_lib

    await update.message.reply_text(
        f"<b>Решение</b>\n{html_lib.escape(str(decision.get('decision') or '—'))}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_debate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    meeting = await _latest_meeting(chat.id)
    if not meeting:
        await update.message.reply_text("Дискуссии нет.")
        return
    chunks = format_transcript(store.list_messages(str(meeting["id"])))
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    q = " ".join(context.args or []).strip()
    items = (
        find_related_decisions(chat.id, q, limit=12)
        if q
        else store.list_decisions_for_chat(chat.id, limit=12)
    )
    await update.message.reply_text(format_history(items), parse_mode=ParseMode.HTML)


def _company_overview_text(company_id: str) -> str:
    ctx = store.get_company_context(company_id) or {}
    docs = store.list_company_documents(company_id)
    stored = str(ctx.get("source_url") or "").strip()
    url, live, err = resolve_company_share(stored)
    return format_company_overview(
        str(ctx.get("raw_text") or ""),
        docs,
        source_url=url,
        live=live,
        live_error=err,
    )


async def cmd_company(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    text = " ".join(context.args or []).strip()
    company = store.get_or_create_company_for_chat(chat.id)
    cid = str(company["id"])
    if not text:
        await update.message.reply_text(_company_overview_text(cid))
        return
    if looks_like_share_source(text):
        url = normalize_share_url(text)
        store.upsert_company_context(cid, source_url=url)
        try:
            live = fetch_share_document(url)
            await update.message.reply_text(
                "Живой документ компании сохранён. Агенты будут брать актуальную версию "
                "из миниаппа Leo.\n\n"
                + format_company_overview(
                    str((store.get_company_context(cid) or {}).get("raw_text") or ""),
                    store.list_company_documents(cid),
                    source_url=url,
                    live=live,
                )
            )
        except Exception as e:
            await update.message.reply_text(
                f"Ссылку сохранил, но загрузить документ не удалось: {e}\n"
                "Проверьте, что ссылка публичная. Агенты попробуют ещё раз на совещании."
            )
        return
    store.upsert_company_context(cid, text)
    await update.message.reply_text(
        "Заметка сохранена. Агенты будут её учитывать вместе с живым документом.\n\n"
        + _company_overview_text(cid)
    )


async def cmd_company_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if not chat or not update.message:
        return
    company = store.get_or_create_company_for_chat(chat.id)
    n = store.delete_company_documents(str(company["id"]))
    await update.message.reply_text(
        f"Документы компании удалены ({n}). Заметки /company не трогал."
        if n
        else "Документов не было."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user or not msg.document:
        return
    if is_group_chat_type(chat.type):
        caption = (msg.caption or "").lower()
        if "/company" not in caption and not _should_start_in_group(update, context):
            return
    doc = msg.document
    filename = doc.file_name or "document"
    caption = (msg.caption or "").strip()
    if doc.file_size and int(doc.file_size) > 2 * 1024 * 1024:
        await msg.reply_text("Файл слишком большой (макс. 2 МБ). Пришлите .txt/.md/.csv/.docx.")
        return
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:
        print(f"[board] company_doc_download err={e!r}")
        await msg.reply_text("Не удалось скачать файл. Попробуйте ещё раз.")
        return
    text, err = extract_document_text(filename, data, doc.mime_type or "")
    if err or not text:
        await msg.reply_text(err or "Не удалось прочитать файл.")
        return
    kind = detect_doc_kind(filename, caption)
    company = store.get_or_create_company_for_chat(chat.id)
    store.add_company_document(
        company_id=str(company["id"]),
        filename=filename,
        kind=kind,
        text=text,
        mime=doc.mime_type or "",
    )
    docs = store.list_company_documents(str(company["id"]))
    notes = (store.get_company_context(str(company["id"])) or {}).get("raw_text") or ""
    await msg.reply_text(
        f"Документ «{filename}» сохранён как {kind_label(kind)}.\n"
        "Агенты будут учитывать его в каждом совещании.\n\n"
        + format_company_overview(str(notes), docs)
    )


async def cmd_me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    user = update.effective_user
    if not user or not update.message:
        return
    raw = " ".join(context.args or []).strip()
    if not raw:
        ctx = store.get_user_context(user.id) or {}
        await update.message.reply_text(
            "Ваш контекст:\n" + ((ctx.get("raw_text") or "(пусто). Напишите /me роль, цели, стиль решений.").strip())
        )
        return
    store.upsert_user_context(user.id, raw)
    await update.message.reply_text("Личный управленческий контекст сохранён.")


def _should_start_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    chat = update.effective_chat
    if not msg or not chat:
        return False
    if not is_group_chat_type(chat.type):
        return True
    return is_bot_mentioned(
        msg, bot_id=context.bot.id, username=_username(context)
    ) or is_reply_to_bot(msg, bot_id=context.bot.id)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    print(f"[board] handler_error err={err!r}")
    msg = getattr(update, "effective_message", None) if update is not None else None
    if msg is not None:
        try:
            await msg.reply_text("Не удалось обработать сообщение. Напишите ещё раз или /new.")
        except Exception:
            pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await ensure_access(update, context):
        return
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    text = (msg.text or msg.caption or "").strip()
    print(
        f"[board] inbound chat={chat.id} type={chat.type} user={user.id} "
        f"text={(text[:80] + '…') if len(text) > 80 else text!r}"
    )
    if not text:
        return
    telegram_registry.register_user(
        telegram_user_id=int(user.id), telegram_username=user.username
    )

    pending = store.get_awaiting_followup(chat.id)
    meeting = _live_meeting(chat.id, _thread_id(update))
    if meeting and meeting.get("status") in {
        "CREATED",
        "ANALYZING",
        "DISCUSSION",
        "CHALLENGE",
        "SYNTHESIS",
    }:
        if not _should_start_in_group(update, context) and is_group_chat_type(chat.type):
            return
        agent = _extract_agent(text)
        cleaned = _clean_question(text, context) or text
        _svc().inject_user(str(meeting["id"]), cleaned, priority_agent=agent)
        await msg.reply_text("Принял уточнение. Совет продолжит с учётом новой информации.")
        return

    if pending and not meeting:
        if is_group_chat_type(chat.type) and not _should_start_in_group(update, context):
            return
        store.mark_followup_answered(str(pending["id"]))
        from assistant.board.renderer import format_review

        raw = await _run_review(str(pending["decision_id"]), text)
        await msg.reply_text(format_review(raw), parse_mode=ParseMode.HTML)
        return

    if is_group_chat_type(chat.type) and not _should_start_in_group(update, context):
        print(f"[board] skip group message without mention/reply chat={chat.id}")
        return

    question = _clean_question(text, context)
    if not question:
        return
    if history_mentions_past(question):
        found = find_related_decisions(chat.id, question, limit=5)
        if found:
            await msg.reply_text(
                "Нашёл прошлые решения:\n\n" + format_history(found),
                parse_mode=ParseMode.HTML,
            )
            return
    try:
        await _launch(update, context, question)
    except Exception as e:
        print(f"[board] launch_err err={e!r}")
        await msg.reply_text("Не удалось запустить совещание. Попробуйте /new ещё раз.")


async def _run_review(decision_id: str, text: str) -> dict[str, Any]:
    import asyncio

    return await asyncio.to_thread(review_decision, decision_id=decision_id, user_results=text)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    if not await ensure_access(update, context):
        return
    await q.answer()
    parts = (q.data or "").split(":", 2)
    if len(parts) < 3 or parts[0] != "bd":
        return
    action, meeting_id = parts[1], parts[2]
    meeting = store.get_meeting(meeting_id)
    if not meeting:
        await q.edit_message_reply_markup(reply_markup=None)
        return
    chat = update.effective_chat
    if action == "debate":
        chunks = format_transcript(store.list_messages(meeting_id))
        for chunk in chunks:
            await context.bot.send_message(
                chat_id=chat.id if chat else q.message.chat_id,
                text=chunk,
                parse_mode=ParseMode.HTML,
            )
        return
    if action == "why":
        decision = store.get_decision_by_meeting(meeting_id)
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=format_why(decision or {}),
            parse_mode=ParseMode.HTML,
        )
        return
    if action == "risk":
        decision = store.get_decision_by_meeting(meeting_id)
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text=format_risks(decision or {}),
            parse_mode=ParseMode.HTML,
        )
        return
    if action == "redo":
        decision = store.get_decision_by_meeting(meeting_id) or {}
        extra = (
            "Reconsider the previous decision. Assume that one of its core assumptions is false. "
            f"Previous decision: {decision.get('decision') or ''}"
        )
        fake = update
        await _launch(
            fake,
            context,
            str(meeting.get("original_question") or "Пересмотреть решение"),
            extra=extra,
        )


def register_handlers(app: Application) -> None:
    app.add_handler(TypeHandler(Update, _bind_usage), group=-1)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("summary", cmd_summary))
    app.add_handler(CommandHandler("debate", cmd_debate))
    app.add_handler(CommandHandler("decision", cmd_decision))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("company", cmd_company))
    app.add_handler(CommandHandler("company_clear", cmd_company_clear))
    app.add_handler(CommandHandler("me", cmd_me))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^bd:"))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_message))
    app.add_error_handler(handle_error)


async def setup_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("new", "Новое совещание"),
            BotCommand("status", "Статус совещания"),
            BotCommand("stop", "Остановить обсуждение"),
            BotCommand("summary", "Итог"),
            BotCommand("debate", "Полная дискуссия"),
            BotCommand("decision", "Только решение"),
            BotCommand("history", "История совещаний"),
            BotCommand("company", "Контекст компании"),
            BotCommand("me", "Личный контекст"),
        ]
    )
