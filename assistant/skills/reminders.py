"""Напоминания в Telegram."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from assistant.config import CALENDAR_TZ
from assistant.lib import calendar_pending_store as cps
from assistant.lib.message_context import reply_context_text
from assistant.nlu import llm as nlu_llm
from assistant.lib.slot_time import parse_datetime_from_user_text
from assistant.stores import reminders_store

_JOB_PREFIX = "remind-"
_PENDING_KEY = "leo_reminder_await_when"
_SNOOZE_MINUTES = {
    "15": 15,
    "30": 30,
    "60": 60,
}
_REMIND_PREFIX_RE = re.compile(
    r"^(?:напомни|напоминание|не забудь|уведоми)(?:\s+мне)?\s*",
    re.IGNORECASE,
)


def _job_name(reminder_id: str) -> str:
    return f"{_JOB_PREFIX}{reminder_id}"


def reminder_actions_keyboard(reminder_id: str) -> InlineKeyboardMarkup:
    rid = (reminder_id or "").strip()
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Выполнено", callback_data=f"rem:done:{rid}")],
            [
                InlineKeyboardButton(
                    "Отложить на 15 мин", callback_data=f"rem:snooze:15:{rid}"
                ),
                InlineKeyboardButton(
                    "Отложить на 30 мин", callback_data=f"rem:snooze:30:{rid}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Отложить на час", callback_data=f"rem:snooze:60:{rid}"
                ),
            ],
        ]
    )


def cancel_reminder_jobs(context: ContextTypes.DEFAULT_TYPE, reminder_id: str) -> None:
    jq = context.application.job_queue if context.application else context.job_queue
    if not jq:
        return
    for job in jq.get_jobs_by_name(_job_name(reminder_id)):
        job.schedule_removal()


def schedule_reminder_job(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reminder_id: str,
    chat_id: int,
    user_id: int,
    task: str,
    when: datetime,
    now: datetime,
) -> None:
    if not context.job_queue:
        return
    cancel_reminder_jobs(context, reminder_id)
    delay = max(1, int((when - now).total_seconds()))
    context.job_queue.run_once(
        _fire_reminder,
        when=delay,
        data={
            "reminder_id": reminder_id,
            "chat_id": int(chat_id),
            "user_id": int(user_id),
            "task": task,
        },
        name=_job_name(reminder_id),
    )


def _strip_remind_prefix(text: str) -> str:
    s = (text or "").strip()
    return _REMIND_PREFIX_RE.sub("", s).strip() or s


def _resolve_reminder_task(
    user_text: str,
    parsed: dict | None,
    reply_text: str,
) -> str:
    """Текст напоминания: при реплае — содержимое исходного сообщения."""
    reply_text = (reply_text or "").strip()
    if reply_text:
        return reply_text
    if parsed:
        task = str(parsed.get("task") or "").strip()
        if task:
            return task
    return _strip_remind_prefix(user_text)


def _get_await_when_pending(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> dict | None:
    st = context.user_data.get(_PENDING_KEY)
    if isinstance(st, dict) and int(st.get("user_id") or 0) == int(user_id):
        return st
    return None


def _set_await_when_pending(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    user_id: int,
    chat_id: int,
    task: str,
) -> None:
    context.user_data[_PENDING_KEY] = {
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "task": (task or "").strip(),
    }
    cps.clear_pending(int(chat_id), user_id=int(user_id))


def _clear_await_when_pending(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(_PENDING_KEY, None)


def clear_await_when_for_user(
    context: ContextTypes.DEFAULT_TYPE, user_id: int
) -> bool:
    if _get_await_when_pending(context, user_id):
        _clear_await_when_pending(context)
        return True
    return False


async def _commit_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    user_id: int,
    task: str,
    when: datetime,
    now: datetime,
) -> None:
    msg = update.message
    if not msg:
        return
    rid = reminders_store.add_reminder(
        user_id=user_id,
        chat_id=chat_id,
        task=task,
        when_iso=when.isoformat(),
    )
    schedule_reminder_job(
        context,
        reminder_id=rid,
        chat_id=chat_id,
        user_id=user_id,
        task=task,
        when=when,
        now=now,
    )
    await msg.reply_text(f"Напомню {when.strftime('%d.%m %H:%M')}: {task}")


async def try_continue_pending(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Ответ на «Когда напомнить?» — не уходить в календарь."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat
    if not msg or not user or not chat:
        return False
    uid = int(user.id)
    st = _get_await_when_pending(context, uid)
    if not st:
        return False
    text = (msg.text or msg.caption or "").strip()
    if not text:
        return False
    tz = ZoneInfo(CALENDAR_TZ)
    now = datetime.now(tz)
    when = parse_datetime_from_user_text(text, now=now)
    if when is None:
        parsed = await asyncio.to_thread(nlu_llm.parse_reminder, text, now=now)
        if parsed and str(parsed.get("when_iso") or "").strip():
            when = datetime.fromisoformat(str(parsed["when_iso"]))
            if when.tzinfo is None:
                when = when.replace(tzinfo=tz)
    if when is None:
        await msg.reply_text(
            "Не понял время. Например: «сегодня в 22:00» или «завтра в 10:30»."
        )
        return True
    task = str(st.get("task") or "").strip() or text
    _clear_await_when_pending(context)
    await _commit_reminder(
        update,
        context,
        chat_id=int(st.get("chat_id") or chat.id),
        user_id=uid,
        task=task,
        when=when,
        now=now,
    )
    return True


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    msg = update.message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat or not user:
        return
    tz = ZoneInfo(CALENDAR_TZ)
    now = datetime.now(tz)
    uid = int(user.id)
    chat_id = int(chat.id)

    reply_txt = reply_context_text(msg)
    parsed = await asyncio.to_thread(
        nlu_llm.parse_reminder, text, now=now, reply_context=reply_txt
    )
    when: datetime | None = None
    if parsed and str(parsed.get("when_iso") or "").strip():
        when = datetime.fromisoformat(str(parsed["when_iso"]))
        if when.tzinfo is None:
            when = when.replace(tzinfo=tz)
    if when is None:
        when = parse_datetime_from_user_text(text, now=now)

    if not parsed and when is None:
        await msg.reply_text("Не смог разобрать напоминание.")
        return

    if when is None or (parsed and parsed.get("need_more_info")):
        task = _resolve_reminder_task(text, parsed, reply_txt)
        if not task:
            await msg.reply_text(
                "Не понял, о чём напомнить. Ответьте реплаем на сообщение или укажите текст."
            )
            return
        _set_await_when_pending(
            context, user_id=uid, chat_id=chat_id, task=task
        )
        q = ""
        if parsed:
            q = str(parsed.get("question") or "").strip()
        await msg.reply_text(q or "Когда напомнить? Укажите дату и время.")
        return

    task = _resolve_reminder_task(text, parsed, reply_txt)
    if not task:
        await msg.reply_text(
            "Не понял, о чём напомнить. Ответьте реплаем на сообщение или укажите текст."
        )
        return
    _clear_await_when_pending(context)
    await _commit_reminder(
        update,
        context,
        chat_id=chat_id,
        user_id=uid,
        task=task,
        when=when,
        now=now,
    )


async def deliver_reminder(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    reminder_id: str,
    chat_id: int,
    task: str,
) -> None:
    rid = (reminder_id or "").strip()
    if not chat_id:
        return
    rec = reminders_store.get_reminder_by_id(rid) if rid else None
    if rec and rec.get("done"):
        return
    text = f"⏰ Напоминание: {(task or '').strip()}"
    kb = reminder_actions_keyboard(rid) if rid else None
    sent = await context.bot.send_message(
        chat_id=int(chat_id),
        text=text,
        reply_markup=kb,
    )
    if rid and sent and sent.message_id:
        reminders_store.set_reminder_message_id(rid, int(sent.message_id))


async def _fire_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data or {}
    await deliver_reminder(
        context,
        reminder_id=str(data.get("reminder_id") or ""),
        chat_id=int(data.get("chat_id") or 0),
        task=str(data.get("task") or ""),
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    user = update.effective_user
    if not q or not user:
        return
    data = (q.data or "").strip()
    if not data.startswith("rem:"):
        return
    parts = data.split(":")
    if len(parts) < 3:
        await q.answer("Некорректная кнопка", show_alert=True)
        return
    action = parts[1]
    if action == "done":
        rid = parts[2] if len(parts) > 2 else ""
    elif action == "snooze" and len(parts) >= 4:
        rid = parts[3]
    else:
        await q.answer("Некорректная кнопка", show_alert=True)
        return
    rid = (rid or "").strip()
    rec = reminders_store.get_reminder_by_id(rid)
    if not rec:
        await q.answer("Напоминание не найдено", show_alert=True)
        return
    if int(rec.get("user_id") or 0) != int(user.id):
        await q.answer("Это напоминание другого пользователя", show_alert=True)
        return
    await q.answer()

    task = str(rec.get("task") or "").strip()
    chat_id = int(rec.get("chat_id") or q.message.chat_id if q.message else 0)

    if action == "done":
        cancel_reminder_jobs(context, rid)
        reminders_store.patch_reminder(rid, done=True)
        try:
            await q.edit_message_text(f"✅ Выполнено: {task}")
        except Exception:
            pass
        return

    if action == "snooze":
        mins_key = parts[2] if len(parts) > 2 else ""
        mins = _SNOOZE_MINUTES.get(mins_key)
        if not mins:
            await q.answer("Неизвестная отсрочка", show_alert=True)
            return
        tz = ZoneInfo(CALENDAR_TZ)
        now = datetime.now(tz)
        new_when = now + timedelta(minutes=mins)
        reminders_store.update_reminder_when(rid, new_when.isoformat())
        schedule_reminder_job(
            context,
            reminder_id=rid,
            chat_id=chat_id,
            user_id=int(user.id),
            task=task,
            when=new_when,
            now=now,
        )
        label = (
            "15 минут"
            if mins == 15
            else "30 минут"
            if mins == 30
            else "час"
        )
        try:
            await q.edit_message_text(
                f"⏰ Напоминание: {task}\n\nОтложено на {label} "
                f"({new_when.strftime('%d.%m %H:%M')})",
                reply_markup=reminder_actions_keyboard(rid),
            )
        except Exception:
            await q.answer(f"Отложено на {label}", show_alert=False)
        return
