"""«Рынок»: блок Лео «Что это значит для вас» — один раз на выпуск, только когда пользователь открыл «Рынок»."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.lib import sqlite_conn

MAX_ISSUE = 8000

_LOCK = threading.RLock()
_CONN: Optional[sqlite3.Connection] = None
_CONN_PATH: Optional[str] = None

SYSTEM = """Ты — помощник руководителя. Тебе дают отраслевой дайджест за день и контекст пользователя:
его компанию (чем занимается, конкуренты, темы — если указаны), ближайшие встречи (у каждой пометка: внутренняя
или с внешними участниками и их компании) и открытые обязательства. Новости про названных конкурентов и темы — в приоритете.
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
    _CONN = sqlite_conn.connect(path)
    _CONN_PATH = path
    _CONN.row_factory = sqlite3.Row
    _CONN.execute(
        "CREATE TABLE IF NOT EXISTS market_insights (owner_user_id TEXT NOT NULL, issue TEXT NOT NULL, text TEXT NOT NULL,"
        " created_at TEXT NOT NULL, PRIMARY KEY (owner_user_id, issue))"
    )
    _CONN.commit()
    return _CONN


def _key(uid: int, issue: str) -> str:
    """Ключ разбора: выпуск + версия логики + отпечаток компании (поменяли настройки — разбор новый)."""
    base = (str(issue or "").strip().split(":", 1)[0][:40]) or datetime.now(timezone.utc).date().isoformat()
    fp = _org_fingerprint(uid)
    return f"{base}:{CACHE_VERSION}" + (f":{fp}" if fp else "")


def cached(uid: int, issue: str) -> Optional[str]:
    key = _key(uid, issue)
    with _LOCK:
        r = _conn().execute("SELECT text FROM market_insights WHERE owner_user_id = ? AND issue = ?", (str(uid), key)).fetchone()
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


def _org_lines(uid: int) -> list[str]:
    """Компания пользователя из настроек выпуска «Рынок» его организации."""
    try:
        from assistant.services import market_settings
        from assistant.stores import orgs_store

        org = orgs_store.current_org(uid)
        if not org:
            return []
        st = market_settings.get(org["id"])
    except Exception as e:
        print(f"[market_insight] org uid={uid} err={e!r}")
        return []
    out = [f"компания: {org['name']}" + (f" — {st['about']}" if st.get("about") else "")]
    if st.get("competitors"):
        out.append("конкуренты: " + ", ".join(st["competitors"]))
    if st.get("topics"):
        out.append("важные темы: " + ", ".join(st["topics"]))
    return out


def _org_fingerprint(uid: int) -> str:
    import hashlib

    lines = _org_lines(uid)
    return hashlib.md5("\n".join(lines).encode("utf-8")).hexdigest()[:8] if lines else ""


def _context(uid: int) -> str:
    lines: list[str] = _org_lines(uid)
    try:
        from assistant.services import calendar_sources, orbita_calendar
        from assistant.services.calendar import _tz_for
        from assistant.services.gpt_calendar_tools import has_google_calendar

        tz = _tz_for(uid)
        now = datetime.now(timezone.utc)
        days = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        events: list[dict[str, Any]] = []
        if has_google_calendar(uid):
            events = list(calendar_sources.list_events_in_window(uid, now, now + timedelta(days=7)))
        for ev in orbita_calendar.events_for(uid, now, now + timedelta(days=7)):  # встречи Орбиты
            events.append({"summary": ev.get("summary"), "start": datetime.fromisoformat(ev["start"]["dateTime"]), "raw": ev})
        events.sort(key=lambda e: e["start"] if isinstance(e.get("start"), datetime) else now)
        events = events[:20]
        own = _own_domains(uid, events) if events else set()
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
    key = _key(uid, issue)
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
            (str(uid), key, out, datetime.now(timezone.utc).isoformat()))
        conn.commit()
    return out
