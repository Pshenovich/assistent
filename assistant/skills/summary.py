"""Саммари: транскрибация + LLM-выжимка с сохранением в журнал."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from assistant.integrations import transcribe as obu
from assistant.integrations.transcribe import TranscribeResult
from assistant.lib.media_download import download_url_bytes
from assistant.lib.message_context import telegram_message_link
from assistant.lib.tg_media_fetch import MediaTooLargeError
from assistant.lib.telegram_html import html_to_plain, uses_html_markup
from assistant.lib.telegram_markdown import prepare_summary_markdown
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.urls import extract_urls
from assistant.lib.usage_store import get_latest_summary, insert_usage_event, search_summaries
from assistant.nlu import llm as llm_mod
from assistant.nlu.regex import parse_summary_intent
from assistant.lib.telegram_status import post_status, set_status, take_work_status
from assistant.skills.transcribe import _bytes_from_tg_message

_DONE_FLAG = "_summary_done"
def mark_summary_handled(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_DONE_FLAG] = True


def consume_summary_handled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.pop(_DONE_FLAG, False))


def is_group_with_summary_intent(msg: Message) -> bool:
    """В группе обрабатываем медиа только с явной командой саммари."""
    caption = (msg.caption or "").strip()
    return parse_summary_intent(caption)


def _summary_uses_html(text: str) -> bool:
    return uses_html_markup(text)


def _first_line_plain(text: str) -> str:
    body = (text or "").strip()
    if not body:
        return ""
    if _summary_uses_html(body):
        return html_to_plain(body).split("\n", 1)[0].strip()
    return body.split("\n", 1)[0].strip()


def _resolve_source(
    *,
    msg: Message | None,
    source_message: Message | None,
    url: str | None,
    bot_username: str | None = None,
    bot_id: int | None = None,
) -> tuple[str | None, str | None]:
    source_url = (url or "").strip() or None
    src_msg = source_message or msg
    telegram_link = telegram_message_link(
        src_msg,
        bot_username=bot_username,
        bot_id=bot_id,
    )
    if not source_url and src_msg:
        urls = extract_urls((src_msg.text or src_msg.caption or ""))
        if urls:
            source_url = urls[0]
    return source_url, telegram_link


async def _reply_text_safe(
    msg: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await reply_formatted(
        msg,
        text,
        reply_markup=reply_markup,
        rich_markdown=True,
        disable_web_page_preview=True,
    )


def _summary_item_markdown(item: dict) -> str:
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    tasks = meta.get("tasks") if isinstance(meta.get("tasks"), list) else None
    return prepare_summary_markdown(
        str(item.get("text") or ""),
        tasks=tasks,
        source_url=item.get("source_url"),
        telegram_link=item.get("telegram_link"),
        headline=str(item.get("headline") or "Саммари"),
        ts=str(item.get("ts_utc") or ""),
    )


async def _reply_error(
    msg: Message,
    status: Message | None,
    text: str,
    *,
    context: ContextTypes.DEFAULT_TYPE | None = None,
) -> None:
    err = (text or "").strip()
    if not err:
        return
    if status:
        updated = await set_status(status, err, anchor=msg, context=context)
        if updated:
            return
    await post_status(msg, err, context=context)


def _speakers_detected(result: TranscribeResult) -> bool:
    formatted = (result.formatted_text or "").strip()
    plain = (result.plain_text or "").strip()
    if formatted != plain and "Спикер" in formatted:
        return True
    return "Спикер" in formatted


def _build_meta(result: dict, *, speakers_detected: bool) -> dict:
    participants = result.get("participants") or []
    return {
        "content_type": result.get("content_type") or "meeting",
        "confidence": result.get("confidence") or 0.0,
        "main_topic": result.get("main_topic") or "",
        "participants_count": len(participants) if isinstance(participants, list) else 0,
        "speakers_detected": speakers_detected,
        "participants": participants,
        "tasks": result.get("tasks") or [],
        "decisions": result.get("decisions") or [],
        "deadlines": result.get("deadlines") or [],
        "topics": result.get("topics") or [],
        "open_questions": result.get("open_questions") or [],
        "risks": result.get("risks") or [],
    }


def _save_summary_journal(
    *,
    user_id: int,
    username: str | None,
    transcript: str,
    summary_text: str,
    meta: dict,
    result: TranscribeResult | None = None,
    filename: str | None = None,
    source_url: str | None = None,
    telegram_link: str | None = None,
    model: str | None = None,
) -> int:
    usage: dict = {
        "text": summary_text,
        "transcript": transcript,
        "source": "telegram",
        "meta": meta,
    }
    if filename:
        usage["filename"] = filename
    if source_url:
        usage["source_url"] = source_url
    if telegram_link:
        usage["telegram_link"] = telegram_link
    return insert_usage_event(
        operation="summarize",
        model=model,
        generation_id=result.job_id if result else None,
        usage=usage,
        telegram_user_id=str(user_id) if user_id else None,
        telegram_username=username,
    )


def _format_summary_message(item: dict) -> str:
    return _summary_item_markdown(item)


async def handle_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    item = await asyncio.to_thread(get_latest_summary, str(user.id))
    if not item:
        await msg.reply_text("Сохранённых саммари пока нет.")
        return
    from assistant.skills.journal_pdf import pdf_download_keyboard

    event_id = int(item.get("id") or 0)
    keyboard = pdf_download_keyboard(event_id) if event_id else None
    await _reply_text_safe(msg, _summary_item_markdown(item), reply_markup=keyboard)


async def handle_search(
    update: Update, context: ContextTypes.DEFAULT_TYPE, query: str
) -> None:
    msg = update.message
    user = update.effective_user
    if not msg or not user:
        return
    q = (query or "").strip()
    if not q:
        await msg.reply_text(
            "Напишите, что искать, например: «найди саммари про мобильное приложение» "
            "или «что решили по интеграции Zoom»."
        )
        return
    uid = int(user.id)
    parsed = await asyncio.to_thread(llm_mod.parse_summary_search_query, q)
    field = None
    assignee = None
    search_q = q
    if parsed:
        field = parsed.get("field")
        assignee = parsed.get("assignee") or None
        search_q = str(parsed.get("query") or q).strip() or q
    matches = await asyncio.to_thread(
        search_summaries,
        str(uid),
        search_q,
        field=field,
        assignee=assignee,
        limit=5,
    )
    if not matches:
        await msg.reply_text(f"Саммари по запросу «{q}» не найдено.")
        return
    best = matches[0]
    from assistant.skills.journal_pdf import pdf_download_keyboard

    event_id = int(best.get("id") or 0)
    keyboard = pdf_download_keyboard(event_id) if event_id else None
    await _reply_text_safe(msg, _format_summary_message(best), reply_markup=keyboard)
    if len(matches) > 1:
        others = ", ".join(
            (m.get("headline") or "Саммари")[:60] for m in matches[1:3]
        )
        await msg.reply_text(
            f"Ещё похожие ({len(matches) - 1}): {others}"
            + ("…" if len(matches) > 3 else "")
        )


async def run_explicit_summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    source_message: Message | None = None,
    url: str | None = None,
) -> None:
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    uid = int(user.id) if user else 0
    username = user.username if user else None

    status = take_work_status(context)
    if status is None:
        status = await post_status(msg, "Скачиваю файл…", context=context)
    else:
        status = await set_status(
            status, "Скачиваю файл…", anchor=msg, context=context
        )
    fname: str | None = None
    link = (url or "").strip() or None
    try:
        if link:
            data, fname = await asyncio.to_thread(download_url_bytes, link)
        else:
            src = source_message or msg
            data, fname = await _bytes_from_tg_message(update, context, src)

        status = await set_status(
            status, "Транскрибирую…", anchor=msg, context=context
        )
        result = await asyncio.to_thread(obu.transcribe_bytes, data, fname or "audio.mp3")
        transcript = (result.formatted_text or result.plain_text or "").strip()
        if not transcript:
            await set_status(status, "(пустая транскрипция)", anchor=msg, context=context)
            return

        speakers = _speakers_detected(result)
        status = await set_status(
            status, "Делаю саммари…", anchor=msg, context=context
        )
        summary_result = await asyncio.to_thread(
            llm_mod.summarize_recording, transcript, speakers_detected=speakers
        )
        if not summary_result:
            await set_status(status, "Не удалось сделать саммари.", anchor=msg, context=context)
            return

        summary_text = str(summary_result.get("summary") or "").strip()
        bot = context.bot
        source_url, telegram_link = _resolve_source(
            msg=msg,
            source_message=source_message,
            url=link,
            bot_username=getattr(bot, "username", None),
            bot_id=getattr(bot, "id", None),
        )
        main_topic = str(summary_result.get("main_topic") or "").strip()
        headline = f"📝 Саммари: {main_topic}" if main_topic else "📝 Саммари"
        ts = datetime.now(timezone.utc).isoformat()
        display_text = prepare_summary_markdown(
            summary_text,
            tasks=summary_result.get("tasks"),
            source_url=source_url,
            telegram_link=telegram_link,
            headline=headline,
            ts=ts,
        )
        meta = _build_meta(summary_result, speakers_detected=speakers)
        from assistant.skills.journal_pdf import pdf_download_keyboard

        event_id = _save_summary_journal(
            user_id=uid,
            username=username,
            transcript=transcript,
            summary_text=display_text,
            meta=meta,
            result=result,
            filename=fname,
            source_url=source_url,
            telegram_link=telegram_link,
            model=llm_mod.summary_model_name(),
        )
        try:
            await status.delete()
        except Exception:
            pass
        keyboard = pdf_download_keyboard(event_id) if event_id else None
        try:
            await _reply_text_safe(msg, display_text, reply_markup=keyboard)
        except Exception as e:
            await _reply_error(
                msg,
                None,
                f"Саммари сохранено в приложении, но не удалось отправить в чат: {e}",
            )
    except MediaTooLargeError as e:
        await _reply_error(msg, status, str(e), context=context)
    except Exception as e:
        await _reply_error(msg, status, f"Саммари: {e}", context=context)


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    user_id: int = 0,
) -> None:
    del user_id
    await run_explicit_summary(update, context, url=url)


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    rep = msg.reply_to_message if msg else None
    if not msg or not rep:
        return
    urls = extract_urls((rep.text or rep.caption or ""))
    if urls:
        await run_explicit_summary(update, context, url=urls[0], source_message=rep)
        return
    if rep.voice or rep.audio or rep.video or rep.document or rep.video_note:
        await run_explicit_summary(update, context, source_message=rep)
        return
    await msg.reply_text("В ответе нет файла, голосового или ссылки.")
