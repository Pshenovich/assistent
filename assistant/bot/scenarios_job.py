"""Сценарии пользователя (правила чатов): разбор новых сообщений (SCENARIOS_ENABLED, SCENARIOS_POLL_SEC)."""

from __future__ import annotations

import asyncio

from telegram.ext import ContextTypes

from assistant.services import scenarios


async def scenarios_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.to_thread(scenarios.run_all)
    except Exception as e:
        print(f"[scenarios] tick err={e!r}")


def register_scenarios_jobs(app) -> None:
    if not scenarios.enabled() or app.job_queue is None:
        return
    interval = scenarios.poll_interval_sec()
    app.job_queue.run_repeating(scenarios_tick, interval=interval, first=90, name="scenarios")
    print(f"[scenarios] scheduled every {interval}s")
