"""Реакция в группе: бот увидел сообщение и обрабатывает."""

from __future__ import annotations

import os

from telegram import Update
from telegram.ext import ContextTypes

from assistant.bot.group_gate import is_group_chat_type

GROUP_SEEN_REACTION = "👀"


def group_seen_reaction_enabled() -> bool:
    raw = os.getenv("TELEGRAM_GROUP_SEEN_REACTION_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


async def react_group_message_seen(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Ставит 👀 на сообщение в group/supergroup (ошибки не пробрасывает)."""
    if not group_seen_reaction_enabled():
        return
    msg = update.message
    chat = update.effective_chat
    if not msg or not chat or not is_group_chat_type(chat.type):
        return
    mid = msg.message_id
    if not mid:
        return
    try:
        await context.bot.set_message_reaction(
            chat_id=chat.id,
            message_id=mid,
            reaction=GROUP_SEEN_REACTION,
        )
    except Exception as e:
        print(
            f"[group_reaction] failed chat_id={chat.id} message_id={mid} err={e!r}"
        )
