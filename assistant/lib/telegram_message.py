"""Отправка форматированных сообщений в Telegram (HTML и Rich Message API)."""

from __future__ import annotations

import asyncio
import io
import os
from typing import Any

from telegram import Message
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from assistant.lib.telegram_html import (
    html_to_plain,
    prepare_classic_html,
    prepare_rich_html,
    sanitize_telegram_html,
    split_telegram_html,
    uses_html_markup,
)

_CLASSIC_CHUNK = int(os.getenv("TELEGRAM_HTML_CHUNK_SIZE", "3800") or "3800")
_RICH_CHUNK = int(os.getenv("TELEGRAM_RICH_CHUNK_SIZE", "30000") or "30000")
_ATTACH_MIN = int(os.getenv("TELEGRAM_ATTACH_TEXT_MIN_LEN", "18000") or "18000")


def rich_messages_enabled() -> bool:
    raw = (os.getenv("TELEGRAM_RICH_MESSAGES", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def attach_long_text_enabled() -> bool:
    raw = (os.getenv("TELEGRAM_ATTACH_LONG_TEXT", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def _send_rich_via_bot(
    _bot: Any,
    chat_id: int,
    body: str,
    *,
    markdown: bool = False,
    reply_to_message_id: int | None = None,
    reply_markup: Any = None,
    disable_web_page_preview: bool = True,
) -> int | None:
    """sendRichMessage через облачный API. Возвращает message_id или None."""
    from assistant.lib import telegram_notify as tn

    markup_dict: dict | None = None
    if reply_markup is not None:
        markup_dict = (
            reply_markup.to_dict()
            if hasattr(reply_markup, "to_dict")
            else reply_markup
        )
    return await asyncio.to_thread(
        tn.send_rich_message_return_id,
        int(chat_id),
        body,
        markdown=markdown,
        reply_to_message_id=reply_to_message_id,
        reply_markup=markup_dict,
        disable_web_page_preview=disable_web_page_preview,
    )


def _message_stub(chat_id: int, message_id: int) -> Message:
    """Минимальный Message для регистрации refs после Rich Message API."""
    from types import SimpleNamespace

    return SimpleNamespace(  # type: ignore[return-value]
        message_id=int(message_id),
        chat=SimpleNamespace(id=int(chat_id)),
        chat_id=int(chat_id),
    )


async def reply_formatted(
    msg: Message,
    text: str,
    *,
    reply_markup: Any = None,
    disable_web_page_preview: bool = True,
    attach_plain_if_long: bool = True,
    fallback_html: str | None = None,
    prefer_classic: bool = False,
    rich_markdown: bool = False,
) -> Message | None:
    """Ответ в чат с Rich/HTML форматированием."""
    body = (text or "").strip()
    if not body:
        return await msg.reply_text("(пусто)")

    bot = msg.get_bot()
    chat_id = int(msg.chat_id)
    rich_body = body if rich_markdown else prepare_rich_html(body)
    last_sent: Message | None = None

    if rich_messages_enabled() and not prefer_classic:
        chunks = split_telegram_html(rich_body, _RICH_CHUNK) or [rich_body]
        ok = True
        last_mid: int | None = None
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            mid = await _send_rich_via_bot(
                bot,
                chat_id,
                chunk,
                markdown=rich_markdown,
                reply_to_message_id=int(msg.message_id),
                reply_markup=reply_markup if is_last and reply_markup else None,
                disable_web_page_preview=disable_web_page_preview,
            )
            if mid is None:
                ok = False
                break
            if mid > 0:
                last_mid = mid
        if ok:
            if attach_plain_if_long and attach_long_text_enabled() and len(body) >= _ATTACH_MIN:
                await _maybe_attach_document(msg, body)
            if last_mid is not None and last_mid > 0:
                return _message_stub(chat_id, last_mid)
            # Rich delivered but without message_id — do not fall back (would duplicate).
            return None

    if rich_markdown:
        print("[telegram_message] rich markdown send failed, skip plain fallback")
        await msg.reply_text("Не удалось отправить таблицу. Попробуйте ещё раз через минуту.")
        return None

    classic_source = (fallback_html or body).strip()
    if prefer_classic or fallback_html:
        sanitized = sanitize_telegram_html(classic_source)
    else:
        sanitized = prepare_classic_html(body)
    if not sanitized:
        return await msg.reply_text("(пусто)", reply_markup=reply_markup)
    try:
        chunks = split_telegram_html(sanitized, _CLASSIC_CHUNK)
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            last_sent = await msg.reply_text(
                chunk,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_web_page_preview,
                reply_markup=reply_markup if is_last and reply_markup else None,
            )
    except BadRequest as e:
        print(f"[telegram_message] HTML send failed: {e!r} chunk={sanitized[:240]!r}")
        plain = html_to_plain(sanitized)
        chunks = [plain[i : i + _CLASSIC_CHUNK] for i in range(0, len(plain), _CLASSIC_CHUNK)]
        for idx, chunk in enumerate(chunks):
            is_last = idx == len(chunks) - 1
            last_sent = await msg.reply_text(
                chunk,
                reply_markup=reply_markup if is_last and reply_markup else None,
            )
    if attach_plain_if_long and attach_long_text_enabled() and len(body) >= _ATTACH_MIN:
        await _maybe_attach_document(msg, body)
    return last_sent


async def _maybe_attach_document(msg: Message, plain_source: str) -> None:
    plain = html_to_plain(plain_source) if uses_html_markup(plain_source) else plain_source
    plain = (plain or "").strip()
    if len(plain) < _ATTACH_MIN:
        return
    data = plain.encode("utf-8")
    try:
        await msg.reply_document(
            document=io.BytesIO(data),
            filename="transcript.txt",
            caption="Полный текст файлом (удобно для длинных записей).",
        )
    except Exception as e:
        print(f"[telegram_message] attach_document failed: {e!r}")


async def send_user_formatted(
    context: ContextTypes.DEFAULT_TYPE | None,
    chat_id: int,
    text: str,
    *,
    reply_markup: Any = None,
    disable_web_page_preview: bool = True,
    attach_plain_if_long: bool = True,
) -> bool:
    """Отправка форматированного текста по chat_id (для notify/webhook)."""
    from assistant.lib import telegram_notify as tn

    body = (text or "").strip()
    if not body:
        return False

    if rich_messages_enabled():
        if tn.send_rich_message(
            int(chat_id),
            prepare_rich_html(body),
            reply_markup=reply_markup,
            disable_web_page_preview=disable_web_page_preview,
        ):
            if attach_plain_if_long and attach_long_text_enabled() and len(body) >= _ATTACH_MIN:
                plain = html_to_plain(body) if uses_html_markup(body) else body
                tn.send_user_document(
                    int(chat_id),
                    plain.strip().encode("utf-8"),
                    filename="transcript.txt",
                    caption="Полный текст файлом.",
                )
            return True
        bot = getattr(context, "bot", None) if context else None
        if bot is not None:
            sent = await _send_rich_via_bot(
                bot,
                int(chat_id),
                prepare_rich_html(body),
                reply_markup=reply_markup,
                disable_web_page_preview=disable_web_page_preview,
            )
            if sent:
                return True

    ok = tn.send_user_html_long_text(
        int(chat_id),
        body,
        disable_web_page_preview=disable_web_page_preview,
        reply_markup=reply_markup,
    )
    if ok and attach_plain_if_long and attach_long_text_enabled() and len(body) >= _ATTACH_MIN:
        plain = html_to_plain(body) if uses_html_markup(body) else body
        tn.send_user_document(
            int(chat_id),
            plain.strip().encode("utf-8"),
            filename="transcript.txt",
            caption="Полный текст файлом.",
        )
    return ok
