"""PDF для транскрипций и саммари: кнопка в чате и callback."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from assistant.lib.journal_pdf import journal_pdf_for_user, note_pdf_for_user
from assistant.lib.telegram_notify import send_user_document, send_user_message

_CALLBACK_PREFIX = "jmpdf:"
_NOTE_CALLBACK_PREFIX = "nmpdf:"


def pdf_download_inline_keyboard(event_id: int) -> dict:
    """JSON reply_markup для Bot API (usage_server / webhooks)."""
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Скачать PDF",
                    "callback_data": f"{_CALLBACK_PREFIX}{int(event_id)}",
                }
            ]
        ]
    }


def pdf_download_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Скачать PDF",
                    callback_data=f"{_CALLBACK_PREFIX}{int(event_id)}",
                )
            ]
        ]
    )


def pdf_download_inline_keyboard_for_note(note_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {
                    "text": "Скачать PDF",
                    "callback_data": f"{_NOTE_CALLBACK_PREFIX}{int(note_id)}",
                }
            ]
        ]
    }


def pdf_download_keyboard_for_note(note_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Скачать PDF",
                    callback_data=f"{_NOTE_CALLBACK_PREFIX}{int(note_id)}",
                )
            ]
        ]
    )


def deliver_journal_pdf_sync(
    *,
    telegram_user_id: int,
    event_id: int,
    notify_on_fail: bool = True,
) -> bool:
    try:
        data, fname, caption = journal_pdf_for_user(str(telegram_user_id), int(event_id))
    except Exception as e:
        if notify_on_fail:
            send_user_message(
                int(telegram_user_id),
                f"Не удалось сделать PDF: {e}",
            )
        return False
    ok = send_user_document(
        int(telegram_user_id),
        data,
        filename=fname,
        caption=caption,
    )
    if not ok and notify_on_fail:
        send_user_message(int(telegram_user_id), "Не удалось отправить PDF в чат.")
    return ok


async def deliver_journal_pdf(
    *,
    telegram_user_id: int,
    event_id: int,
    notify_on_fail: bool = True,
) -> bool:
    return deliver_journal_pdf_sync(
        telegram_user_id=telegram_user_id,
        event_id=event_id,
        notify_on_fail=notify_on_fail,
    )


def deliver_note_pdf_sync(
    *,
    telegram_user_id: int,
    note_id: int,
    notify_on_fail: bool = True,
) -> bool:
    try:
        data, fname, caption = note_pdf_for_user(str(telegram_user_id), int(note_id))
    except Exception as e:
        if notify_on_fail:
            send_user_message(
                int(telegram_user_id),
                f"Не удалось сделать PDF: {e}",
            )
        return False
    ok = send_user_document(
        int(telegram_user_id),
        data,
        filename=fname,
        caption=caption,
    )
    if not ok and notify_on_fail:
        send_user_message(int(telegram_user_id), "Не удалось отправить PDF в чат.")
    return ok


async def deliver_note_pdf(
    *,
    telegram_user_id: int,
    note_id: int,
    notify_on_fail: bool = True,
) -> bool:
    return deliver_note_pdf_sync(
        telegram_user_id=telegram_user_id,
        note_id=note_id,
        notify_on_fail=notify_on_fail,
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = update.effective_user
    if not q or not user:
        return
    data = str(q.data or "")
    if data.startswith(_NOTE_CALLBACK_PREFIX):
        try:
            note_id = int(data[len(_NOTE_CALLBACK_PREFIX) :])
        except ValueError:
            await q.answer("Некорректная ссылка.")
            return
        await q.answer("Генерирую PDF…")
        await deliver_note_pdf(
            telegram_user_id=int(user.id),
            note_id=note_id,
            notify_on_fail=True,
        )
        return
    if not data.startswith(_CALLBACK_PREFIX):
        return
    try:
        event_id = int(data[len(_CALLBACK_PREFIX) :])
    except ValueError:
        await q.answer("Некорректная ссылка.")
        return
    await q.answer("Генерирую PDF…")
    await deliver_journal_pdf(
        telegram_user_id=int(user.id),
        event_id=event_id,
        notify_on_fail=True,
    )
