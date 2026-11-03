"""Ночной снимок «сколько чего у пользователя» для продуктовой аналитики.

Раз в сутки (ANALYTICS_SNAPSHOT_HOUR_UTC, по умолчанию 00:30 UTC = 03:30 МСК)
считаем по базам заметки, транскрипции, саммари, листы по видам (документ /
презентация / доска), чаты, задачи, напоминания, контакты, базы знаний, орги
и пишем в analytics.user_daily_snapshot. В LLM не ходим. Там же чистим старые
сырые события.
"""

from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import datetime, time as dtime, timezone
from typing import Any, Callable

from telegram.ext import ContextTypes

from assistant.stores import analytics_store

Counts = dict[str, dict[str, float]]  # user_id → metric → value


def _add(out: Counts, uid: Any, metric: str, value: float) -> None:
    u = str(uid or "").strip()
    if u:
        out[u][metric] = out[u].get(metric, 0) + float(value or 0)


def _notes(out: Counts) -> None:
    from assistant.stores import notes as notes_store

    with notes_store._LOCK:  # type: ignore[attr-defined]
        rows = notes_store._conn().execute(  # type: ignore[attr-defined]
            "SELECT user_id, role, primary_sheet_title, COUNT(*) AS n FROM local_notes "
            "GROUP BY user_id, role, primary_sheet_title"
        ).fetchall()
    for uid, role, primary, n in rows:
        if role == notes_store.TRANSCRIPTION_ROLE:
            _add(out, uid, "transcriptions", n)
            if primary == notes_store.SUMMARY_SHEET_TITLE:
                _add(out, uid, "summaries", n)
        elif role == notes_store.KNOWLEDGE_ROLE:
            _add(out, uid, "kb_notes", n)
        else:
            _add(out, uid, "notes", n)


def _sheets(out: Counts) -> None:
    from assistant.stores import note_sheets as note_sheets_store

    names = {"doc": "sheets_doc", "web": "presentations", "board": "boards"}
    with note_sheets_store._LOCK:  # type: ignore[attr-defined]
        rows = note_sheets_store._conn().execute(  # type: ignore[attr-defined]
            "SELECT n.user_id, s.kind, COUNT(*) FROM note_sheets s JOIN local_notes n ON n.id = s.note_id "
            "GROUP BY n.user_id, s.kind"
        ).fetchall()
    for uid, kind, n in rows:
        _add(out, uid, names.get(str(kind or "doc"), "sheets_other"), n)


def _chats(out: Counts) -> None:
    from assistant.stores import chat_threads

    with chat_threads._LOCK:  # type: ignore[attr-defined]
        rows = chat_threads._conn().execute(  # type: ignore[attr-defined]
            "SELECT owner_user_id, COUNT(*) FROM chat_threads GROUP BY owner_user_id"
        ).fetchall()
    for uid, n in rows:
        _add(out, uid, "ai_chats", n)


def _tasks(out: Counts) -> None:
    from assistant.stores import calendar_tasks

    with calendar_tasks._LOCK:  # type: ignore[attr-defined]
        rows = calendar_tasks._conn().execute(  # type: ignore[attr-defined]
            "SELECT owner_user_id, COUNT(*) FROM calendar_tasks GROUP BY owner_user_id"
        ).fetchall()
    for uid, n in rows:
        _add(out, uid, "tasks", n)


def _reminders(out: Counts) -> None:
    from assistant.stores import reminders_store

    for r in reminders_store.load_reminders():
        if not r.get("done"):
            _add(out, r.get("user_id"), "reminders_active", 1)


def _kbs(out: Counts) -> None:
    from assistant.stores import knowledge_base_store as kb

    conn = kb._conn()  # type: ignore[attr-defined]
    for uid, n in conn.execute(
        "SELECT owner_telegram_user_id, COUNT(*) FROM knowledge_bases GROUP BY owner_telegram_user_id"
    ).fetchall():
        _add(out, uid, "knowledge_bases", n)
    for uid, n in conn.execute(
        "SELECT b.owner_telegram_user_id, COUNT(*) FROM kb_documents d JOIN knowledge_bases b ON b.id = d.kb_id "
        "GROUP BY b.owner_telegram_user_id"
    ).fetchall():
        _add(out, uid, "kb_documents", n)


def _orgs(out: Counts) -> None:
    from assistant.stores import orgs_store

    with orgs_store._LOCK:  # type: ignore[attr-defined]
        rows = orgs_store._conn().execute(  # type: ignore[attr-defined]
            "SELECT user_id, COUNT(*) FROM org_members WHERE COALESCE(user_id, '') != '' GROUP BY user_id"
        ).fetchall()
    for uid, n in rows:
        _add(out, uid, "orgs", n)


def _contacts(out: Counts) -> None:
    """Контакты лежат JSON-файлом на пользователя — считаем для тех, кто уже есть в снимке."""
    from assistant.stores import contacts_store

    for uid in list(out.keys()):
        try:
            n = len(contacts_store.load_contacts(telegram_user_id=int(uid)))
        except (TypeError, ValueError):
            continue
        out[uid]["contacts"] = float(n)


_COLLECTORS: list[Callable[[Counts], None]] = [_notes, _sheets, _chats, _tasks, _reminders, _kbs, _orgs, _contacts]


def collect() -> Counts:
    out: Counts = defaultdict(dict)
    # Пользователи с активностью тоже попадают в снимок (с нулями), даже без сущностей.
    for r in analytics_store.query("SELECT DISTINCT user_id FROM activity_minutes"):
        out[str(r["user_id"])]
    for fn in _COLLECTORS:
        try:
            fn(out)
        except Exception as e:
            print(f"[analytics_snapshot] {fn.__name__} err={e!r}")
    return out


def run_snapshot(day: str | None = None) -> int:
    day = day or datetime.now(timezone.utc).date().isoformat()
    counts = collect()
    rows = [(uid, metric, val) for uid, metrics in counts.items() for metric, val in metrics.items()]
    analytics_store.save_snapshot(day, rows)
    analytics_store.purge_old()
    return len(counts)


async def analytics_snapshot_tick(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        n = await asyncio.to_thread(run_snapshot)
        print(f"[analytics_snapshot] users={n}")
    except Exception as e:
        print(f"[analytics_snapshot] err={e!r}")


def register_analytics_snapshot_jobs(app) -> None:
    if app.job_queue is None:
        return
    hour = int(os.getenv("ANALYTICS_SNAPSHOT_HOUR_UTC", "0") or 0)
    app.job_queue.run_daily(
        analytics_snapshot_tick, time=dtime(hour=hour, minute=30, tzinfo=timezone.utc), name="analytics_snapshot"
    )
