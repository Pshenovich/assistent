"""Отмена незавершённого диалога (календарь, Zoom)."""

from __future__ import annotations

import os

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib import calendar_pending_store as cps
from assistant.lib.operation_cancel import is_cancel_command, pending_kind_label

_PENDING_TTL = float(os.getenv("CALENDAR_PENDING_TTL_SEC", "3600") or "3600")


async def try_cancel_operation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str | None = None,
) -> bool:
    """Сбрасывает pending-состояние пользователя. True — сообщение обработано."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return False
    raw = (
        text
        if text is not None
        else (msg.text or msg.caption or "")
    )
    if not is_cancel_command((raw or "").strip()):
        return False
    chat_id = int(chat.id)
    uid = int(user.id)
    from assistant.skills import reminders as reminders_skill

    if reminders_skill.clear_await_when_for_user(context, uid):
        await msg.reply_text("Операция отменена: уточнение времени напоминания.")
        return True
    st = cps.get_pending(chat_id, ttl_sec=_PENDING_TTL, user_id=uid)
    if not st or int(st.get("user_id") or 0) != uid:
        await msg.reply_text("Нет активной операции для отмены.")
        return True
    kind = str(st.get("kind") or "")
    cps.clear_pending(chat_id, user_id=uid)
    label = pending_kind_label(kind)
    await msg.reply_text(f"Операция отменена: {label}.")
    return True
