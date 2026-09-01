"""Zoom: запись встречи по ссылке через meeting bot."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from assistant.lib.urls import extract_urls
from assistant.lib import zoom_link
from assistant.services import meeting_record_schedule as sched


def _uid(update: Update) -> int:
    user = update.effective_user
    return int(user.id) if user else 0


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    msg = update.message
    if not msg:
        return
    uid = _uid(update)
    if not sched.service_available():
        await msg.reply_text(
            "Запись Zoom-встреч через бота пока не настроена на сервере."
        )
        return
    urls = extract_urls(text or "")
    link = zoom_link.find_zoom_url(urls)
    if link is None:
        await msg.reply_text("Пришлите ссылку на Zoom-встречу.")
        return
    topic = "Встреча Zoom"
    try:
        job = sched.schedule_zoom_recording(
            uid,
            meeting_url=link.url,
            topic=topic,
            source="zoom_link",
            dispatch_immediately=True,
        )
    except Exception as e:
        await msg.reply_text(f"Zoom-запись: {e}")
        return
    if not job:
        await msg.reply_text("Не удалось создать задачу записи.")
        return
    if job.get("_joined_existing"):
        return


