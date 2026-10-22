"""B6: фоновая индексация семантической памяти (SEMANTIC_SEARCH_ENABLED, SEMANTIC_INDEX_SEC)."""

from __future__ import annotations

import asyncio
import os

from telegram.ext import ContextTypes

from assistant.lib import embed_client


def _user_ids() -> list[int]:
    from assistant.stores import notes as notes_store

    with notes_store._LOCK:
        rows = notes_store._conn().execute("SELECT DISTINCT user_id FROM local_notes").fetchall()
    out = []
    for r in rows:
        try:
            out.append(int(r[0]))
        except (TypeError, ValueError):
            continue
    return out


def index_all() -> None:
    from assistant.services import semantic_memory

    for uid in _user_ids():
        try:
            res = semantic_memory.index_user(uid)
            if res["indexed"] or res["removed"]:
                print(f"[semantic] uid={uid} {res}")
        except Exception as e:
            print(f"[semantic] uid={uid} err={e!r}")


async def semantic_index_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    if not embed_client.enabled():
        return
    await asyncio.to_thread(index_all)


def register_semantic_index_jobs(app) -> None:
    if app.job_queue is None or not embed_client.enabled():
        return
    try:
        interval = max(120.0, float(os.getenv("SEMANTIC_INDEX_SEC", "900") or "900"))
    except ValueError:
        interval = 900.0
    app.job_queue.run_repeating(semantic_index_tick, interval=interval, first=90, name="semantic_index")
    print(f"[semantic] index every {interval}s")
