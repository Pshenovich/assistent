"""Запуск Telegram-бота (long polling).

Фоновые задачи (напоминания о встречах, брифы, почта, дайджесты, алерты…) могут жить
в отдельном процессе assistant-worker (worker.py): при JOBS_IN_WORKER=1 бот только
отвечает людям, задачи выполняет воркер. Без флага всё по-старому — в боте.
"""

from __future__ import annotations

import os

from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application, ApplicationBuilder

from assistant.bot.handlers import register_handlers
from assistant.lib.telegram_bot_api import attach_local_bot_api
from assistant.lib.webapp_public import webapp_entry_url
from assistant.bot.meeting_invite_job import register_meeting_invite_jobs
from assistant.bot.meeting_reminder_job import register_meeting_reminder_jobs
from assistant.bot.meeting_brief_job import register_meeting_brief_jobs
from assistant.bot.meeting_followup_handlers import register_meeting_followup_handlers
from assistant.bot.semantic_index_job import register_semantic_index_jobs
from assistant.bot.task_reminder_job import register_task_reminder_jobs
from assistant.bot.meeting_bot_scheduler_job import register_meeting_bot_scheduler_jobs
from assistant.bot.telemost_mail_job import register_telemost_mail_jobs
from assistant.bot.mail_job import register_mail_jobs
from assistant.bot.reminder_job import register_reminder_jobs
from assistant.bot.knowledge_sync_job import register_knowledge_sync_jobs
from assistant.bot.proactive_brief_job import register_proactive_brief_jobs
from assistant.bot.chat_alert_job import register_chat_alert_handlers, register_chat_alert_jobs
from assistant.bot.digest_auto_job import register_digest_auto_jobs
from assistant.bot.market_digest_job import register_market_digest_jobs
from assistant.bot.gcal_push_job import register_gcal_push_jobs
from assistant.bot.heartbeat_job import register_heartbeat_jobs
from assistant.bot.spend_alert_job import register_spend_alert_jobs


def jobs_in_worker() -> bool:
    return os.getenv("JOBS_IN_WORKER", "").strip().lower() in ("1", "true", "yes", "on")


def base_builder(*, log_prefix: str) -> ApplicationBuilder:
    """Общая сборка для бота и воркера: токен, прокси, локальный Bot API, лимиты."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан в .env")
    proxy = os.getenv("TELEGRAM_PROXY_URL", "").strip()
    builder = Application.builder().token(token)
    if proxy:
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    builder = attach_local_bot_api(builder, log_prefix=log_prefix)
    # Лимиты Telegram (~30 сообщений/с на бота, 20 в минуту в группу): массовые рассылки
    # (брифы, напоминания сотням людей) ждут очереди и повторяются после RetryAfter, а не теряются.
    if os.getenv("TELEGRAM_RATE_LIMITER", "1").strip().lower() not in ("0", "false", "no", "off"):
        try:
            from telegram.ext import AIORateLimiter

            builder = builder.rate_limiter(AIORateLimiter(max_retries=3))
        except (ImportError, RuntimeError) as e:
            print(f"[{log_prefix}] rate limiter unavailable: {e!r}")
    return builder


async def widen_thread_pool() -> None:
    # Фоновые задачи обходят людей пачкой (assistant/lib/fanout.py) и ходят в Google/ИИ
    # через to_thread; стандартных потоков (ядра + 4) на это мало.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    try:
        workers = max(8, int(os.getenv("BOT_THREADS", "32") or "32"))
    except ValueError:
        workers = 32
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers))


def register_background_jobs(app: Application) -> None:
    """Периодические задачи по всем пользователям — в боте или в воркере (JOBS_IN_WORKER)."""
    register_meeting_reminder_jobs(app)
    register_meeting_brief_jobs(app)
    register_semantic_index_jobs(app)
    register_task_reminder_jobs(app)
    register_meeting_invite_jobs(app)
    register_meeting_bot_scheduler_jobs(app)
    register_telemost_mail_jobs(app)
    register_mail_jobs(app)
    register_knowledge_sync_jobs(app)
    register_proactive_brief_jobs(app)
    register_digest_auto_jobs(app)
    register_market_digest_jobs(app)
    register_chat_alert_jobs(app)
    register_spend_alert_jobs(app)
    register_gcal_push_jobs(app)


def build_application() -> Application:
    # Параллельная обработка апдейтов (напоминания не блокируют голос/текст).
    builder = base_builder(log_prefix="bot").concurrent_updates(8)

    async def _post_init(application: Application) -> None:
        await widen_thread_pool()
        url = webapp_entry_url()
        try:
            await application.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Orbita", web_app=WebAppInfo(url=url)
                )
            )
            print(f"[bot] menu button → {url}")
        except Exception as e:
            print(f"[bot] menu button failed: {e}")

    builder = builder.post_init(_post_init)
    app = builder.build()
    register_handlers(app)
    register_meeting_followup_handlers(app)
    register_chat_alert_handlers(app)
    # Напоминания пользователя планирует обработчик сообщения в job_queue бота — остаются здесь.
    register_reminder_jobs(app)
    register_heartbeat_jobs(app, name="bot")
    if jobs_in_worker():
        print("[bot] background jobs → assistant-worker (JOBS_IN_WORKER=1)")
    else:
        register_background_jobs(app)
    return app


def main() -> None:
    from assistant.realtime import notify as realtime_notify
    from assistant.lib.sentry_init import init_sentry

    init_sentry("bot")

    # Правки заметок из бота — открытым экранам мини-приложения через веб-сервер.
    realtime_notify.enable_remote()
    app = build_application()
    print("[bot] polling…")
    # Сообщения, пришедшие пока бот перезапускался (выкатка), не выбрасываем —
    # слишком старые отсекает _claim_update в handlers (is_stale_update).
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
