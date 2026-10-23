"""«Рынок»: блок Лео «Что это значит для вас» — один раз на выпуск, только когда пользователь открыл «Рынок»."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

MAX_ISSUE = 8000

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None

SYSTEM = """Ты — помощник руководителя. Тебе дают отраслевой дайджест за день и контекст пользователя (его ближайшие встречи и открытые обязательства).
Напиши блок «Что это значит для вас»: 2–3 коротких пункта — какие новости из выпуска касаются именно его дел и что с этим сделать
(обсудить на конкретной встрече из списка, проверить гипотезу, ответить конкуренту). Называй встречу так, как она называется, с днём.
Не пересказывай новости и не выдумывай фактов. Если ничего не касается пользователя — одной фразой так и скажи.
Ответ — пункты списком через «• », без вступления и заголовка."""


def _conn() -> sqlite3.Connection:
    global _CONN, _CONN_PATH
    from assistant.stores import notes as notes_store

    path = str(notes_store._db_path())
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    notes_store._conn()
    _CONN = sqlite3.connect(path, check_same_thread=False, timeout=30)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS market_insights (owner_user_id TEXT NOT NULL, issue TEXT NOT NULL, text TEXT NOT NULL,"
        " created_at TEXT NOT NULL, PRIMARY KEY (owner_user_id, issue))"
    )
    _CONN.commit()
    return _CONN


def cached(uid: int, issue: str) -> Optional[str]:
    with _LOCK:
        r = _conn().execute("SELECT text FROM market_insights WHERE owner_user_id = ? AND issue = ?", (str(uid), issue)).fetchone()
    return str(r["text"]) if r else None


def _context(uid: int) -> str:
    lines: list[str] = []
    try:
        from assistant.services import calendar_sources
        from assistant.services.calendar import _tz_for
        from assistant.services.gpt_calendar_tools import has_google_calendar

        if has_google_calendar(uid):
            tz = _tz_for(uid)
            now = datetime.now(timezone.utc)
            days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            for ev in calendar_sources.list_events_in_window(uid, now, now + timedelta(days=7))[:20]:
                st = ev.get("start")
                if isinstance(st, datetime):
                    st = st.astimezone(tz)
                    lines.append(f"встреча «{ev.get('summary')}» — {days[st.weekday()]} {st.strftime('%d.%m %H:%M')}")
    except Exception as e:
        print(f"[market_insight] calendar uid={uid} err={e!r}")
    try:
        from assistant.services import focus_items

        for c in focus_items.commitments(uid, direction="mine", status="open", limit=8):
            lines.append(f"обязательство: {c['text']}")
    except Exception as e:
        print(f"[market_insight] commitments uid={uid} err={e!r}")
    return "\n".join(lines) or "контекста нет"


def insight(uid: int, issue: str, issue_text: str, *, force: bool = False) -> str:
    issue = (issue or "").strip()[:40] or datetime.now(timezone.utc).date().isoformat()
    if not force:
        hit = cached(uid, issue)
        if hit:
            return hit
    text = (issue_text or "").strip()[:MAX_ISSUE]
    if len(text) < 80:
        raise ValueError("Выпуск пустой — нечего разбирать.")
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.nlu import llm

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    try:
        out = llm._chat(SYSTEM, f"Выпуск:\n{text}\n\nКонтекст пользователя:\n{_context(uid)}", operation="market_insight",
                        timeout=90, temperature=0.3, max_tokens=500)
    finally:
        set_openrouter_usage_telegram_user(telegram_user_id=None)
    out = str(out or "").strip()
    if not out:
        raise ValueError("Не получилось — попробуйте ещё раз.")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO market_insights (owner_user_id, issue, text, created_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(owner_user_id, issue) DO UPDATE SET text = excluded.text, created_at = excluded.created_at",
            (str(uid), issue, out, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return out
