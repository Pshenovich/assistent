"""Ответ из Telegram в обсуждение задачи (как из карточки в Орбите): кнопка «Ответить» под
уведомлением о сообщении по задаче (следующий текст или голосовое) или свайп-ответ на само уведомление."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

CANCEL_CALLBACK = "tsk:rcancel"
_PENDING_KEY = "leo_task_reply"
_PENDING_TTL_SEC = 30 * 60
_CALLBACK_RE = re.compile(r"^tsk:reply:(\d+)$")


def _get_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> dict[str, Any] | None:
    st = context.user_data.get(_PENDING_KEY) if context.user_data is not None else None
    if not isinstance(st, dict) or int(st.get("user_id") or 0) != int(user_id):
        return None
    if time.time() - float(st.get("at") or 0) > _PENDING_TTL_SEC:
        context.user_data.pop(_PENDING_KEY, None)
        return None
    return st


_TASK_URL_RE = re.compile(r"[?&#]task=(\d+)")


def _task_id_from_replied(replied: Any, bot_id: int | None) -> int | None:
    """Свайп-ответ на уведомление «X по задаче …»: id из кнопки «Ответить», иначе из ссылки на задачу."""
    if replied is None:
        return None
    author = getattr(replied, "from_user", None)
    if not author or not getattr(author, "is_bot", False) or (bot_id and int(author.id) != int(bot_id)):
        return None
    markup = getattr(replied, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or ():
        for btn in row:
            m = _CALLBACK_RE.match(str(getattr(btn, "callback_data", "") or ""))
            if m:
                return int(m.group(1))
    if " по задаче " not in str(getattr(replied, "text", "") or ""):
        return None
    for ent in getattr(replied, "entities", None) or ():
        m = _TASK_URL_RE.search(str(getattr(ent, "url", "") or ""))
        if m:
            return int(m.group(1))
    return None


def reply_target(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[int, bool] | None:
    """(task_id, из режима «Ответить») — куда уйдёт это сообщение; None — это не ответ по задаче."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat or chat.type != "private":
        return None
    try:
        bot_id = int(context.bot.id)
    except Exception:  # бот ещё не инициализирован (тесты) — сверяем только is_bot
        bot_id = None
    tid = _task_id_from_replied(getattr(msg, "reply_to_message", None), bot_id)
    if tid:
        return tid, False
    st = _get_pending(context, int(user.id))
    return (int(st["task_id"]), True) if st else None


def clear_pending_for_user(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    if _get_pending(context, user_id):
        context.user_data.pop(_PENDING_KEY, None)
        return True
    return False


def _actor_name(user_id: int, fallback: str) -> str:
    from assistant.lib.task_notify import owner_display_name

    name = owner_display_name(user_id)
    return fallback if name == "Коллега" and fallback else name


def post_reply(user_id: int, task_id: int, text: str, *, fallback_name: str = "") -> dict[str, Any] | None:
    """Сообщение в обсуждение задачи + пуш второй стороне — как POST /calendar/tasks/{id}/messages."""
    from assistant.lib.task_notify import notify_task_message
    from assistant.stores import calendar_tasks as calendar_tasks_store

    name = _actor_name(user_id, fallback_name)
    row = calendar_tasks_store.post_message(user_id, task_id, text, actor_name=name)
    if not row:
        return None
    notify_task_message(task=row, sender_id=user_id, sender_name=name, text=text)
    try:
        from assistant.services import semantic_memory

        for who in {str(row.get("owner_user_id") or ""), str(row.get("assignee_user_id") or "")} - {""}:
            semantic_memory.note_changed(who)
    except Exception as e:
        print(f"[task_reply] memory task={task_id} err={e!r}")
    return row


async def handle_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.stores import calendar_tasks as calendar_tasks_store
    from assistant.stores.calendar_tasks import task_is_delegated

    q = update.callback_query
    user = update.effective_user
    if not q or not q.data or not user:
        return
    uid = int(user.id)
    if q.data == CANCEL_CALLBACK:
        clear_pending_for_user(context, uid)
        await q.answer("Отменено")
        try:
            await q.edit_message_text("Ответ отменён.")
        except Exception:
            pass
        return
    m = _CALLBACK_RE.match(q.data)
    if not m:
        await q.answer()
        return
    task_id = int(m.group(1))
    task = await asyncio.to_thread(calendar_tasks_store.get_task_for_user, uid, task_id)
    if not task or not task_is_delegated(task):
        await q.answer("Задача недоступна — возможно, её удалили", show_alert=True)
        return
    context.user_data[_PENDING_KEY] = {"user_id": uid, "task_id": task_id, "at": time.time()}
    await q.answer()
    from assistant.lib.task_notify import task_title_link_html

    link = task_title_link_html(str(task.get("title") or "Задача"), task_id)
    chat_id = q.message.chat_id if q.message else uid
    await context.bot.send_message(
        chat_id,
        f"Напишите или надиктуйте голосовым ответ по задаче {link} — добавлю его в обсуждение.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена", callback_data=CANCEL_CALLBACK)]]),
    )


async def try_consume_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """Ждём ответ по задаче — отправляем текст в её обсуждение. True — сообщение обработано."""
    from assistant.lib.task_notify import task_title_link_html

    target = reply_target(update, context)
    body = (text or "").strip()
    if not target or not body:
        return False
    msg = update.message
    user = update.effective_user
    uid = int(user.id)
    task_id, from_pending = target
    if from_pending:
        context.user_data.pop(_PENDING_KEY, None)
    try:
        row = await asyncio.to_thread(
            post_reply, uid, task_id, body, fallback_name=str(user.full_name or "").strip()
        )
    except ValueError as e:
        await msg.reply_text(str(e))
        return True
    except Exception as e:
        print(f"[task_reply] post uid={uid} task={task_id} err={e!r}")
        await msg.reply_text("Не получилось добавить ответ в задачу. Попробуйте ещё раз.")
        return True
    if not row:
        await msg.reply_text("Задача недоступна — возможно, её удалили.")
        return True
    link = task_title_link_html(str(row.get("title") or "Задача"), task_id)
    await msg.reply_text(
        f"✅ Ответ добавлен в задачу {link}.",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )
    return True

