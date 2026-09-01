"""Когда бот должен реагировать в групповых чатах."""

from __future__ import annotations

import os

from telegram import Message, Update
from telegram.constants import MessageEntityType
from telegram.ext import ContextTypes

_PENDING_TTL = float(os.getenv("CALENDAR_PENDING_TTL_SEC", "3600") or "3600")
_REMINDER_PENDING_KEY = "leo_reminder_await_when"


def _username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return (context.bot.username or os.getenv("TELEGRAM_BOT_USERNAME") or "").lstrip("@").lower()


def is_bot_mentioned(msg: Message, *, bot_id: int, username: str) -> bool:
    uname = (username or "").lstrip("@").lower()
    pairs = (
        (msg.text, msg.entities),
        (msg.caption, msg.caption_entities),
    )
    for text, entities in pairs:
        if not text:
            continue
        if entities:
            for ent in entities:
                if ent.type == MessageEntityType.MENTION and uname:
                    fragment = text[ent.offset : ent.offset + ent.length]
                    if fragment.lstrip("@").lower() == uname:
                        return True
                if ent.type == MessageEntityType.TEXT_MENTION and ent.user:
                    if ent.user.id == bot_id:
                        return True
        if uname and f"@{uname}" in text.lower():
            return True
    return False


def is_reply_to_bot(msg: Message, *, bot_id: int) -> bool:
    rep = msg.reply_to_message
    if not rep or not rep.from_user:
        return False
    return rep.from_user.id == bot_id


def is_group_chat_type(chat_type: str | None) -> bool:
    return chat_type in ("group", "supergroup")


def should_process_in_chat(
    msg: Message,
    *,
    chat_type: str | None,
    bot_id: int,
    bot_username: str,
) -> bool:
    if not is_group_chat_type(chat_type):
        return True
    return is_bot_mentioned(msg, bot_id=bot_id, username=bot_username) or is_reply_to_bot(
        msg, bot_id=bot_id
    )


def user_has_pending_dialog(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Активный многошаговый диалог (календарь, напоминание) в этом групповом чате."""
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return False
    if not is_group_chat_type(chat.type):
        return False
    chat_id = int(chat.id)
    uid = int(user.id)
    from assistant.lib import calendar_pending_store as cps

    st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
    if isinstance(st, dict) and int(st.get("user_id") or 0) == uid:
        return True
    rem = context.user_data.get(_REMINDER_PENDING_KEY)
    if (
        isinstance(rem, dict)
        and int(rem.get("user_id") or 0) == uid
        and int(rem.get("chat_id") or 0) == chat_id
    ):
        return True
    return False


def should_process_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg = update.message
    chat = update.effective_chat
    if not msg:
        return False
    if should_process_in_chat(
        msg,
        chat_type=chat.type if chat else None,
        bot_id=context.bot.id,
        bot_username=_username(context),
    ):
        return True
    return user_has_pending_dialog(update, context)
