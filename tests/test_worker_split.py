"""Фоновые задачи: в боте без флага, в воркере с JOBS_IN_WORKER=1; кнопки алертов всегда в боте."""

from __future__ import annotations

import asyncio

import pytest
from telegram.ext import CallbackQueryHandler

from assistant.bot import main as bot_main
from assistant.bot import worker


def _job_names(app) -> set[str]:
    return {j.name for j in app.job_queue.jobs()}


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST")
    monkeypatch.delenv("TELEGRAM_BOT_API_BASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_RATE_LIMITER", "0")
    # Сборка приложения на Python 3.9 берёт текущий цикл событий; соседние тесты его закрывают.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    asyncio.set_event_loop(None)
    loop.close()


def _has_alert_button(app) -> bool:
    return any(
        isinstance(h, CallbackQueryHandler) and "calert" in str(getattr(h.pattern, "pattern", ""))
        for hs in app.handlers.values() for h in hs
    )


def test_default_jobs_stay_in_bot(monkeypatch):
    monkeypatch.delenv("JOBS_IN_WORKER", raising=False)
    app = bot_main.build_application()
    names = _job_names(app)
    assert "bot_heartbeat" in names and "reminder_poll" in names
    assert "meeting_reminder" in names or any("meeting" in n for n in names)
    assert _has_alert_button(app)
    w = worker.build_worker()
    assert _job_names(w) == {"worker_heartbeat"}


def test_jobs_move_to_worker(monkeypatch):
    monkeypatch.setenv("JOBS_IN_WORKER", "1")
    bot = bot_main.build_application()
    bot_names = _job_names(bot)
    w_names = _job_names(worker.build_worker())
    assert "reminder_poll" in bot_names and "bot_heartbeat" in bot_names
    moved = w_names - {"worker_heartbeat"}
    assert moved and not (moved & bot_names)  # каждая фоновая задача ровно в одном процессе
    assert _has_alert_button(bot)


def test_worker_run_starts_and_stops(monkeypatch):
    calls = []

    class FakeApp:
        post_init = None

        async def initialize(self):
            calls.append("init")

        async def start(self):
            calls.append("start")

        async def stop(self):
            calls.append("stop")

        async def shutdown(self):
            calls.append("shutdown")

    async def go():
        task = asyncio.create_task(worker._run(FakeApp()))
        await asyncio.sleep(0.05)
        import os
        import signal

        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(task, 2)

    asyncio.run(go())
    assert calls == ["init", "start", "stop", "shutdown"]
