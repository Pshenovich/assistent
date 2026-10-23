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

SYSTEM = """Ты — помощник руководителя. Тебе дают отраслевой дайджест за день и контекст пользователя:
его ближайшие встречи (у каждой пометка: внутренняя или с внешними участниками и их компании) и открытые обязательства.
Напиши блок «Что это значит для вас»: 1–3 коротких пункта — какие новости касаются его продукта и рынка и что с этим сделать
(проверить гипотезу, посмотреть на конкурента, учесть в продукте или цене).
Встречу упоминай, только если связь прямая и очевидная:
- внутренняя встреча, у которой в названии та же тема, что у новости (продукт, стратегия, конкуренты, цены, маркетинг, планёрка по продукту);
- встреча с внешней компанией — только если новость прямо про эту компанию или её рынок. Встречу с клиентом НЕ предлагай как место,
  чтобы «обсудить новости рынка».
Если подходящей встречи нет — не упоминай встречи вообще. Каждый пункт начинай с самой новости (одна-две фразы, на что она влияет).
Не пересказывай весь выпуск, не выдумывай фактов. Если ничего не касается пользователя — одной фразой так и скажи.
Ответ — пункты списком через «• », без вступления и заголовка."""

CACHE_VERSION = "v2"  # меняется вместе с логикой — старые разборы пересобираются


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
    if not issue.endswith(":" + CACHE_VERSION):
        issue = ((issue or "").strip()[:40] or datetime.now(timezone.utc).date().isoformat()) + ":" + CACHE_VERSION
    with _LOCK:
        r = _conn().execute("SELECT text FROM market_insights WHERE owner_user_id = ? AND issue = ?", (str(uid), issue)).fetchone()
    return str(r["text"]) if r else None


_PUBLIC_DOMAINS = {"gmail.com", "googlemail.com", "yandex.ru", "ya.ru", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
                   "outlook.com", "hotmail.com", "icloud.com", "me.com", "rambler.ru"}


def meeting_kind(attendees: list[dict[str, Any]], own_domains: set[str]) -> str:
    """«внутренняя» / «с внешними участниками: компании» — по доменам почты гостей."""
    ext: list[str] = []
    for a in attendees or []:
        if not isinstance(a, dict) or a.get("self") or a.get("resource"):
            continue
        dom = str(a.get("email") or "").rsplit("@", 1)[-1].lower()
        if dom and dom not in own_domains and dom not in ext:
            ext.append(dom)
    if not ext:
        return "внутренняя"
    names = [d for d in ext if d not in _PUBLIC_DOMAINS] or ext
    return "с внешними участниками: " + ", ".join(names[:4])


def _own_domains(uid: int, events: list[dict[str, Any]]) -> set[str]:
    own: set[str] = set()
    for ev in events:
        for a in (ev.get("raw") or {}).get("attendees") or []:
            if isinstance(a, dict) and a.get("self") and a.get("email"):
                own.add(str(a["email"]).rsplit("@", 1)[-1].lower())
    try:
        from assistant.stores import mail_accounts

        own |= {str(x["email"]).rsplit("@", 1)[-1].lower() for x in mail_accounts.list_accounts(uid)}
    except Exception:
        pass
    return {d for d in own if d not in _PUBLIC_DOMAINS} or own


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
            events = calendar_sources.list_events_in_window(uid, now, now + timedelta(days=7))[:20]
            own = _own_domains(uid, events)
            for ev in events:
                st = ev.get("start")
                if isinstance(st, datetime):
                    st = st.astimezone(tz)
                    kind = meeting_kind((ev.get("raw") or {}).get("attendees") or [], own)
                    lines.append(f"встреча «{ev.get('summary')}» — {days[st.weekday()]} {st.strftime('%d.%m %H:%M')} ({kind})")
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
    issue = ((issue or "").strip()[:40] or datetime.now(timezone.utc).date().isoformat()) + ":" + CACHE_VERSION
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
