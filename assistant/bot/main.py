"""Запуск Telegram-бота (long polling)."""

from __future__ import annotations

import os

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application

from assistant.bot.handlers import register_handlers
from assistant.lib.telegram_bot_api import attach_local_bot_api
from assistant.lib.webapp_public import webapp_entry_url
from assistant.bot.meeting_reminder_job import register_meeting_reminder_jobs
from assistant.bot.meeting_bot_scheduler_job import register_meeting_bot_scheduler_jobs
from assistant.bot.telemost_mail_job import register_telemost_mail_jobs
from assistant.bot.reminder_job import register_reminder_jobs
from assistant.bot.knowledge_sync_job import register_knowledge_sync_jobs


def build_application() -> Application:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    builder = Application.builder().token(token)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    builder = attach_local_bot_api(builder, log_prefix="bot")
    # Параллельная обработка апдейтов (напоминания не блокируют голос/текст).
    builder = builder.concurrent_updates(8)

    async def _post_init(application: Application) -> None:
        url = webapp_entry_url()
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Ассистент", web_app=WebAppInfo(url=url)
                )
            )
            print(f"[bot] menu button → {url}")
        except Exception as e:
            print(f"[bot] menu button failed: {e}")

    builder = builder.post_init(_post_init)
    app = builder.build()
    register_handlers(app)
    register_meeting_reminder_jobs(app)
    register_meeting_bot_scheduler_jobs(app)
    register_telemost_mail_jobs(app)
    register_reminder_jobs(app)
    register_knowledge_sync_jobs(app)
    return app


def main() -> None:
    app = build_application()
    print("[bot] polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
