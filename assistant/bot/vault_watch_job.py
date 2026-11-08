"""Сейфы: раз в 5 минут заметить уходы участников и новые запросы доступа; разослать уведомления
(assistant/services/vault_notify.py).

Работает, только когда сейфы включены (VAULTS_ENABLED). Содержимое сейфов здесь не трогается —
только факты: кто ушёл, кто просит доступ, кто ответил.
"""

from __future__ import annotations

import asyncio
import os

from telegram.ext import ContextTypes

INTERVAL_SEC = 300


def _enabled() -> bool:
    return os.environ.get("VAULTS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _tick_sync() -> int:
    from assistant.services import vault_notify
    from assistant.stores import vaults

    sent = 0
    for ev in vaults.watch():
        if ev.get("to"):
            sent += vault_notify.send_event(ev)
    return sent


async def vault_watch_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _enabled():
        return
    try:
        await asyncio.to_thread(_tick_sync)
    except Exception as e:
        print(f"[vault_watch] tick failed err={e!r}")


def register_vault_watch_jobs(app) -> None:
    if app.job_queue is None:
        return
    app.job_queue.run_repeating(vault_watch_tick, interval=INTERVAL_SEC, first=120, name="vault_watch")
