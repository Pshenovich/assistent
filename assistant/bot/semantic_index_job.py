"""B6: фоновая индексация семантической памяти (SEMANTIC_SEARCH_ENABLED, SEMANTIC_INDEX_SEC)."""

from __future__ import annotations

import asyncio
import os

from telegram.ext import ContextTypes

from assistant.lib import embed_client


def _user_ids() -> list[int]:
    """Чьи личные разделы обходить: авторы заметок и все, у кого личный раздел уже есть (задачи, почта)."""
    from assistant.stores import notes as notes_store
    from assistant.stores import semantic_index as idx

    with notes_store._LOCK:
        rows = notes_store._conn().execute("SELECT DISTINCT user_id FROM local_notes").fetchall()
    raw = [r[0] for r in rows]
    with idx._LOCK:
        raw += [r[0] for r in idx._conn().execute("SELECT DISTINCT owner_user_id FROM sem_sources").fetchall()]
    out: list[int] = []
    for v in raw:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue  # разделы документов "doc:<id>" и старые "org:<id>"
    return sorted(set(out))


def index_all() -> None:
    from assistant.services import semantic_memory

    # Сначала документы: каждый в своём разделе, один раз для всех. Тот же проход переносит готовые
    # векторы из старых копий в личных разделах и разделах организаций и убирает эти копии.
    try:
        res = semantic_memory.index_docs()
        if res["indexed"] or res["copied"] or res["removed"] or res["purged"]:
            print(f"[semantic] docs {res}")
    except Exception as e:
        print(f"[semantic] docs err={e!r}")
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
    # Полный проход по всем — страховка: правки в вебе, саммари, почта и задачи сами ставят
    # переиндексацию через минуту (semantic_memory.doc_changed / note_changed). Раньше — каждые 15 минут.
    try:
        interval = max(120.0, float(os.getenv("SEMANTIC_INDEX_SEC", "3600") or "3600"))
    except ValueError:
        interval = 3600.0
    app.job_queue.run_repeating(semantic_index_tick, interval=interval, first=90, name="semantic_index")
    print(f"[semantic] index every {interval}s")
