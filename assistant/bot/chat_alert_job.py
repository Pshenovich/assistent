"""Срочные пуши о критичных проблемах в чатах (CHAT_ALERTS_ENABLED, CHAT_ALERTS_POLL_SEC)."""

from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from assistant.bot.access_gate import is_user_allowed
from assistant.services import chat_alerts


def _keyboard(alert_id: int, url: str) -> InlineKeyboardMarkup:
    row = []
    if url:
        row.append(InlineKeyboardButton("Открыть в чате", url=url))
    row.append(InlineKeyboardButton("🔕 Не критично", callback_data=f"calert:mute:{int(alert_id)}"))
    return InlineKeyboardMarkup([row])


def _tick_bounds(uids: list[int]) -> dict[int, int]:
    """Общая граница пачки на этот тик по каждому чату — у всех, кто следит за чатом, одна пачка."""
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    chats = {int(c) for u in uids for c in user_prefs.digest_chat_ids(u)}
    return {c: digest_store.max_rowid(c) for c in chats}


async def chat_alert_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    from assistant.stores import user_prefs

    uids = [
        u for u in await asyncio.to_thread(user_prefs.iter_user_ids_with_digest)
        if is_user_allowed(u, None) and chat_alerts.user_enabled(u)
    ]
    upto = await asyncio.to_thread(_tick_bounds, uids) if uids else {}
    for uid in uids:
        try:
            found = await asyncio.to_thread(chat_alerts.scan_user, uid, upto=upto)
        except Exception as e:
            print(f"[chat_alerts] uid={uid} scan err={e!r}")
            continue
        for a in found:
            # отмечаем до отправки: при сбое сети не пришлём тот же алерт повторно
            alert_id = await asyncio.to_thread(chat_alerts.record_alert, uid, a)
            url = chat_alerts.message_url(a["chat_id"], a["thread_id"] or None, a["message_id"])
            try:
                await context.bot.send_message(
                    chat_id=int(uid),
                    text=chat_alerts.message_html(uid, a),
                    parse_mode=ParseMode.HTML,
                    reply_markup=_keyboard(alert_id, url),
                    disable_web_page_preview=True,
                )
                print(f"[chat_alerts] sent uid={uid} chat={a['chat_id']} thread={a['thread_id']} msg={a['message_id']}")
            except Exception as e:
                print(f"[chat_alerts] send uid={uid} err={e!r}")


async def on_alert_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not q or not q.data:
        return
    try:
        alert_id = int(q.data.split(":")[2])
    except (IndexError, ValueError):
        await q.answer()
        return
    row = await asyncio.to_thread(chat_alerts.mute_alert, q.from_user.id, alert_id)
    if not row:
        await q.answer()
        return
    where = "этому топику" if row.get("thread_id") else "этому чату"
    await q.answer(f"Понял. Сутки не пишу срочное по {where}.")
    try:
        url = chat_alerts.message_url(row["chat_id"], row.get("thread_id") or None, row["message_id"])
        await q.edit_message_reply_markup(
            InlineKeyboardMarkup([[InlineKeyboardButton("Открыть в чате", url=url)]]) if url else None
        )
    except Exception:
        pass


def register_chat_alert_handlers(app) -> None:
    """Кнопка «Не критично» под алертом — в боте (алерты может слать воркер)."""
    app.add_handler(CallbackQueryHandler(on_alert_callback, pattern=r"^calert:mute:"))


def register_chat_alert_jobs(app) -> None:
    if not chat_alerts.enabled() or app.job_queue is None:
        return
    interval = chat_alerts.poll_interval_sec()
    app.job_queue.run_repeating(chat_alert_tick, interval=interval, first=60, name="chat_alerts")
    print(f"[chat_alerts] scheduled every {interval}s")
