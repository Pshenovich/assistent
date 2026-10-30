"""assistant-worker: фоновые задачи отдельно от бота.

Тот же токен Telegram, но без приёма сообщений (не опрашивает Telegram — иначе
конфликт с ботом): только job_queue. При JOBS_IN_WORKER=1 выполняет задачи из
register_background_jobs; без флага ждёт и подаёт пульс — задачи в боте.
"""

from __future__ import annotations

import asyncio
import signal

from telegram.ext import Application

from assistant.bot.heartbeat_job import register_heartbeat_jobs
from assistant.bot.main import base_builder, jobs_in_worker, register_background_jobs, widen_thread_pool


def build_worker() -> Application:
    async def _post_init(application: Application) -> None:
        await widen_thread_pool()

    app = base_builder(log_prefix="worker").post_init(_post_init).build()
    register_heartbeat_jobs(app, name="worker")
    if jobs_in_worker():
        register_background_jobs(app)
        print("[worker] background jobs registered")
    else:
        print("[worker] JOBS_IN_WORKER не включён — задачи выполняет бот, воркер ждёт")
    return app


async def _run(app: Application) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await app.initialize()
    if app.post_init:
        await app.post_init(app)
    await app.start()  # запускает job_queue; опроса Telegram нет
    print("[worker] running")
    try:
        await stop.wait()
    finally:
        await app.stop()
        await app.shutdown()
        print("[worker] stopped")


def main() -> None:
    from assistant.lib.sentry_init import init_sentry
    from assistant.realtime import notify as realtime_notify

    init_sentry("worker")
    # Изменения из фоновых задач — открытым экранам приложения через веб-сервер.
    realtime_notify.enable_remote()
    asyncio.run(_run(build_worker()))


if __name__ == "__main__":
    main()
