"""Фоновая синхронизация корпоративных баз знаний."""

from __future__ import annotations

import asyncio
import os

from telegram.ext import ContextTypes

from assistant.services import knowledge_sync


def sync_interval_sec() -> float:
    try:
        hours = float(os.getenv("KNOWLEDGE_SYNC_INTERVAL_SEC", str(6 * 3600)) or str(6 * 3600))
        return max(3600.0, hours)
    except ValueError:
        return 6 * 3600.0


async def knowledge_sync_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    try:
        results = await asyncio.to_thread(knowledge_sync.sync_all_knowledge_bases)
        if results:
            print(f"[knowledge_sync] synced {len(results)} knowledge base(s)")
    except Exception as e:
        print(f"[knowledge_sync] tick failed: {e!r}")


def register_knowledge_sync_jobs(app) -> None:
    if app.job_queue is None:
        print("[knowledge_sync] job_queue unavailable")
        return
    interval = sync_interval_sec()
    app.job_queue.run_repeating(
        knowledge_sync_tick,
        interval=interval,
        first=interval,
        name="knowledge_sync",
    )
    print(f"[knowledge_sync] poll every {interval}s")
