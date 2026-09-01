"""Транскрибация: явная команда и голос → NLU."""

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.ext import ContextTypes

from assistant.bot.group_gate import should_process_message
from assistant.bot.group_reaction import react_group_message_seen
from assistant.integrations import transcribe as obu
from assistant.integrations.transcribe import TranscribeResult
from assistant.lib.media_download import download_url_bytes
from assistant.lib.telegram_status import (
    post_status,
    set_status,
    take_work_status,
)
from assistant.lib.tg_media_fetch import MediaTooLargeError, download_telegram_file
from assistant.lib.telegram_html import format_transcription_html
from assistant.lib.telegram_message import reply_formatted
from assistant.lib.urls import extract_urls
from assistant.lib.usage_store import insert_usage_event, search_transcriptions
from assistant.nlu.regex import parse_transcribe_intent

_DONE_FLAG = "_transcribe_done"


async def _reply_chunks(msg, text: str, *, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    await reply_formatted(msg, text, reply_markup=reply_markup)


def _format_transcription_message(item: dict) -> str:
    headline = (item.get("headline") or "").strip() or "Транскрипция"
    text = (item.get("text") or "").strip()
    ts = (item.get("ts_utc") or "").strip()
    header = headline
    if ts:
        header = f"{headline}\n({ts[:16].replace('T', ' ')})"
    body = text
    if text and text.split("\n", 1)[0].strip() != headline:
        body = f"{header}\n\n{text}"
    elif text and text != headline:
        body = f"{header}\n\n{text}"
    elif text == headline:
        body = header
    else:
        body = text or header
    return format_transcription_html(body)


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
            "Напишите, что искать, например: «найди транскрипцию про совещание в понедельник»."
        )
        return
    uid = int(user.id)
    matches = await asyncio.to_thread(search_transcriptions, str(uid), q, limit=5)
    if not matches:
        await msg.reply_text(f"Транскрипций по запросу «{q}» не найдено.")
        return
    best = matches[0]
    await _reply_chunks(msg, _format_transcription_message(best))
    if len(matches) > 1:
        others = ", ".join(
            (m.get("headline") or "Транскрипция")[:60] for m in matches[1:3]
        )
        await msg.reply_text(
            f"Ещё похожие ({len(matches) - 1}): {others}"
            + ("…" if len(matches) > 3 else "")
        )
_DONE_FLAG = "_transcribe_done"


def mark_transcribe_handled(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_DONE_FLAG] = True


def consume_transcribe_handled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.pop(_DONE_FLAG, False))


def is_forwarded(msg: Message | None) -> bool:
    if not msg:
        return False
    if msg.forward_origin is not None:
        return True
    return bool(getattr(msg, "is_automatic_forward", False))


def _media_filename(media, *, default: str) -> str:
    name = getattr(media, "file_name", None)
    if name:
        return name
    if getattr(media, "mime_type", "") or "":
        mt = media.mime_type
        if "ogg" in mt:
            return "audio.ogg"
        if "mpeg" in mt or "mp3" in mt:
            return "audio.mp3"
        if "mp4" in mt:
            return "video.mp4"
    return default


async def _bytes_from_tg_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> tuple[bytes, str]:
    media = (
        message.voice
        or message.audio
        or message.video
        or message.document
        or message.video_note
    )
    if not media:
        raise ValueError("Нет аудио или видео в сообщении.")
    file_size = getattr(media, "file_size", None)
    data, _ = await download_telegram_file(
        context.bot,
        media.file_id,
        file_size=file_size,
    )
    if message.voice:
        fname = "voice.ogg"
    elif message.video_note:
        fname = "video_note.mp4"
    elif message.video:
        fname = _media_filename(media, default="video.mp4")
    elif message.audio:
        fname = _media_filename(media, default="audio.mp3")
    else:
        fname = _media_filename(media, default="media.bin")
    return data, fname


async def _reply_long_text(
    msg: Message,
    text: str,
    *,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    await reply_formatted(
        msg,
        format_transcription_html(text),
        reply_markup=reply_markup,
    )


def _save_transcription_journal(
    *,
    user_id: int,
    username: str | None,
    result: TranscribeResult,
    filename: str | None = None,
    url: str | None = None,
) -> int:
    usage: dict = {
        "text": result.formatted_text,
        "source": "telegram",
    }
    if filename:
        usage["filename"] = filename
    if url:
        usage["url"] = url
    return insert_usage_event(
        operation="obuchat_transcribe",
        model=None,
        generation_id=result.job_id,
        usage=usage,
        telegram_user_id=str(user_id) if user_id else None,
        telegram_username=username,
    )


async def run_explicit_transcribe(
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
            status,
            "Отправляю файл на транскрипцию…",
            anchor=msg,
            context=context,
        )
        result = await asyncio.to_thread(obu.transcribe_bytes, data, fname or "audio.mp3")

        status = await set_status(
            status, "Жду ответ от сервиса…", anchor=msg, context=context
        )
        if not (result.formatted_text or result.plain_text).strip():
            await set_status(status, "(пустая транскрипция)", anchor=msg, context=context)
            return

        from assistant.skills.journal_pdf import pdf_download_keyboard

        event_id = _save_transcription_journal(
            user_id=uid,
            username=username,
            result=result,
            filename=fname,
            url=link,
        )
        try:
            await status.delete()
        except Exception:
            pass
        keyboard = pdf_download_keyboard(event_id) if event_id else None
        await _reply_long_text(
            msg,
            result.formatted_text,
            reply_markup=keyboard,
        )
    except MediaTooLargeError as e:
        await set_status(status, str(e), anchor=msg, context=context)
    except Exception as e:
        await set_status(status, f"Транскрибация: {e}", anchor=msg, context=context)


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    *,
    user_id: int = 0,
) -> None:
    del user_id
    await run_explicit_transcribe(update, context, url=url)


async def handle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    rep = msg.reply_to_message if msg else None
    if not msg or not rep:
        return
    urls = extract_urls((rep.text or rep.caption or ""))
    if urls:
        await run_explicit_transcribe(update, context, url=urls[0])
        return
    if rep.voice or rep.audio or rep.video or rep.document or rep.video_note:
        await run_explicit_transcribe(update, context, source_message=rep)
        return
    await msg.reply_text("В ответе нет файла, голосового или ссылки.")


async def _transcribe_and_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.nlu.dispatch import RouteExtras, route_text

    msg = update.message
    if not msg or not msg.voice:
        return
    status = await post_status(msg, "Слушаю голосовое…", context=context)
    try:
        voice = msg.voice
        status = await set_status(
            status, "Скачиваю аудио…", anchor=msg, context=context
        )
        audio, _ = await download_telegram_file(
            context.bot,
            voice.file_id,
            file_size=getattr(voice, "file_size", None),
        )
        status = await set_status(
            status, "Транскрибирую…", anchor=msg, context=context
        )
        result = await asyncio.to_thread(obu.transcribe_bytes, audio, "voice.ogg")
        text = (result.plain_text or "").strip()
        if not text:
            await set_status(status, "(пустая транскрипция)", anchor=msg, context=context)
            return
        status = await set_status(
            status, "Разбираю запрос…", anchor=msg, context=context
        )
        chat = update.effective_chat
        is_group = chat and chat.type in ("group", "supergroup")
        extras = RouteExtras(
            source="group" if is_group else "voice",
            replied=msg.reply_to_message,
        )
        handled = await route_text(
            update,
            context,
            text,
            extras,
            chat_type="group" if is_group else "private",
        )
        try:
            await status.delete()
        except Exception:
            pass
        if not handled and not is_group:
            await msg.reply_text(
                "Не понял запрос. Примеры: «встреча завтра в 15:00», "
                "«какие встречи завтра»."
            )
    except MediaTooLargeError as e:
        await set_status(status, str(e), anchor=msg, context=context)
    except Exception as e:
        await set_status(status, f"Транскрибация: {e}", anchor=msg, context=context)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.bot.access_gate import ensure_access

    if not should_process_message(update, context):
        return
    if not await ensure_access(update, context):
        return
    msg = update.message
    if not msg:
        return
    if is_forwarded(msg):
        return
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        await react_group_message_seen(update, context)
        return
    await _transcribe_and_route(update, context)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.bot.access_gate import ensure_access

    msg = update.message
    if not msg or not should_process_message(update, context):
        return
    if not await ensure_access(update, context):
        return
    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup"):
        await react_group_message_seen(update, context)
        from assistant.skills.summary import (
            is_group_with_summary_intent,
            mark_summary_handled,
            run_explicit_summary,
        )

        if is_group_with_summary_intent(msg):
            mark_summary_handled(context)
            await run_explicit_summary(update, context, source_message=msg)
            return
        if not is_group_with_transcribe_intent(msg):
            return

    media = msg.document or msg.audio or msg.video or msg.voice
    if not media:
        return

    caption = (msg.caption or "").strip()
    from assistant.nlu.regex import parse_summary_intent
    from assistant.skills.summary import mark_summary_handled, run_explicit_summary

    if parse_summary_intent(caption):
        mark_summary_handled(context)
        await run_explicit_summary(update, context, source_message=msg)
        return
    if parse_transcribe_intent(caption):
        mark_transcribe_handled(context)
        await run_explicit_transcribe(update, context, source_message=msg)
        return

    if is_forwarded(msg):
        return

    try:
        data, _ = await download_telegram_file(
            context.bot,
            media.file_id,
            file_size=getattr(media, "file_size", None),
        )
        fname = _media_filename(media, default="audio.mp3")
        result = await asyncio.to_thread(obu.transcribe_bytes, data, fname)
        text = (result.plain_text or "").strip()
        if text:
            from assistant.nlu.dispatch import RouteExtras, route_text

            is_group = chat and chat.type in ("group", "supergroup")
            extras = RouteExtras(source="group" if is_group else "private")
            await route_text(
                update,
                context,
                text,
                extras,
                chat_type="group" if is_group else "private",
            )
        else:
            await msg.reply_text("(пустая транскрипция)")
    except MediaTooLargeError as e:
        await msg.reply_text(str(e))
    except Exception as e:
        await msg.reply_text(f"Транскрибация: {e}")


def is_group_with_transcribe_intent(msg: Message) -> bool:
    """В группе обрабатываем медиа только с явной командой транскрипции."""
    caption = (msg.caption or "").strip()
    return parse_transcribe_intent(caption)
