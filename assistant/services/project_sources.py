"""Что можно добавить в проект, кроме документов и задач: встречи и серии, темы встреч,
внешние Telegram-чаты и топики, переписки и почтовые адреса. Только своё — то, что человек и так видит.

Ключи элементов — как в stores/bundles.norm_item:
event / series — "<calendar_id>:<event_id | id серии>", topic — id темы, tgchat — "<chat_id>[:<thread_id>]",
mail — thread_key, mailaddr — адрес.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

PAST_DAYS = 120
FUTURE_DAYS = 60
LIMIT = 40

_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def _day(dt: datetime, tz: Any) -> str:
    try:
        d = dt.astimezone(tz)
    except Exception:
        d = dt
    return f"{d.day} {_MONTHS[d.month - 1]}"


def _tz(uid: int) -> Any:
    try:
        from assistant.services.calendar import _tz_for

        return _tz_for(uid)
    except Exception:
        return timezone.utc


def _match(words: list[str], *texts: Any) -> bool:
    hay = " ".join(str(t or "") for t in texts).lower()
    return all(w in hay for w in words)


def _words(q: str) -> list[str]:
    return [w for w in str(q or "").lower().split() if w]


# ---------- встречи ----------


def _google_rows(uid: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    try:
        from assistant.stores import calendar_mirror

        return [r for r in calendar_mirror.events(uid, start, end) if not r.get("all_day")]
    except Exception as e:
        print(f"[project_sources] mirror uid={uid} err={e!r}")
        return []


def _orbita_rows(uid: int, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Встречи Орбиты: [{calendar_id, event_id, series_id, title, start}]."""
    try:
        from assistant.stores import orbita_events as store
    except Exception:
        return []
    out = []
    try:
        for ev in store.list_for_user(uid, start, end):
            pid = store.public_id(ev["id"])
            for occ in store.occurrences(ev, start, end):
                is_series = bool(ev.get("recurrence"))
                out.append({
                    "calendar_id": store.CALENDAR_ID,
                    "event_id": store.instance_id(ev["id"], occ["occ"]) if is_series and occ.get("occ") else pid,
                    "series_id": pid if is_series else "",
                    "title": str(ev.get("title") or ""),
                    "start": occ["start"],
                })
    except Exception as e:
        print(f"[project_sources] orbita uid={uid} err={e!r}")
    return out


def meetings(uid: int, q: str = "") -> list[dict[str, Any]]:
    """Серии (одной строкой) и отдельные встречи за последние месяцы и ближайшие недели, плюс темы встреч."""
    words = _words(q)
    tz = _tz(uid)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
    rows = []
    for r in _google_rows(uid, start, end):
        rows.append({"calendar_id": r["calendar_id"], "event_id": r["event_id"], "series_id": r.get("series_id") or "",
                     "title": r.get("title") or "", "start": r["start"]})
    rows += _orbita_rows(uid, start, end)
    series: dict[str, dict[str, Any]] = {}
    singles: list[dict[str, Any]] = []
    for r in rows:
        if not _match(words, r["title"]):
            continue
        if r["series_id"]:
            key = f"{r['calendar_id']}:{r['series_id']}"
            s = series.setdefault(key, {"title": r["title"], "past": 0, "next": None, "last": None})
            if r["start"] <= now:
                s["past"] += 1
                s["last"] = max(s["last"] or r["start"], r["start"])
            elif s["next"] is None or r["start"] < s["next"]:
                s["next"] = r["start"]
                s["title"] = r["title"] or s["title"]
        else:
            singles.append(r)
    out: list[dict[str, Any]] = []
    for key, s in series.items():
        bits = ["повторяется"]
        if s["past"]:
            bits.append(f"прошло {s['past']}")
        if s["next"]:
            bits.append("следующая " + _day(s["next"], tz))
        out.append({"kind": "series", "id": key, "title": s["title"] or "Встреча", "subtitle": " · ".join(bits),
                    "series": True, "sort": (s["next"] or s["last"] or now)})
    for r in singles:
        out.append({"kind": "event", "id": f"{r['calendar_id']}:{r['event_id']}", "title": r["title"] or "Встреча",
                    "subtitle": ("будет " if r["start"] > now else "") + _day(r["start"], tz), "sort": r["start"]})
    # ближайшие и недавние — выше
    out.sort(key=lambda x: abs((x["sort"] - now).total_seconds()))
    for x in out:
        x.pop("sort", None)
    topics = []
    try:
        from assistant.stores import meeting_topics

        for t in meeting_topics.topics_for_user(uid):
            if t.get("name") and _match(words, t["name"]):
                topics.append({"kind": "topic", "id": str(t["id"]), "title": f"Тема «{t['name']}»",
                               "subtitle": "все встречи с похожим названием"})
    except Exception as e:
        print(f"[project_sources] topics uid={uid} err={e!r}")
    return (topics[:5] + out)[:LIMIT]


def series_occurrences(uid: int, key: str) -> list[dict[str, Any]]:
    """Встречи серии (для «только выбранные»): [{id, title, date, past, has_summary}] — ключи event."""
    cal, _, sid = str(key or "").partition(":")
    if not sid:
        return []
    tz = _tz(uid)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(days=PAST_DAYS), now + timedelta(days=FUTURE_DAYS)
    rows = [r for r in _google_rows(uid, start, end) if r["calendar_id"] == cal and r.get("series_id") == sid]
    rows = [{"event_id": r["event_id"], "title": r.get("title") or "", "start": r["start"]} for r in rows]
    rows += [r for r in _orbita_rows(uid, start, end) if r["calendar_id"] == cal and r["series_id"] == sid]
    summaries: set[str] = set()
    try:
        from assistant.stores import meeting_links

        with meeting_links._LOCK:  # type: ignore[attr-defined]
            for (eid,) in meeting_links._conn().execute(  # type: ignore[attr-defined]
                "SELECT event_id FROM meeting_links WHERE calendar_id = ? AND (event_id = ? OR substr(event_id, 1, ?) = ?)",
                (cal, sid, len(sid) + 1, sid + "_"),
            ):
                summaries.add(str(eid))
    except Exception:
        pass
    rows.sort(key=lambda r: r["start"], reverse=True)
    return [{"kind": "event", "id": f"{cal}:{r['event_id']}", "title": r["title"] or "Встреча",
             "date": _day(r["start"], tz), "past": r["start"] <= now, "has_summary": r["event_id"] in summaries}
            for r in rows[:60]]


# ---------- внешние чаты ----------


def tgchats(uid: int, q: str = "") -> list[dict[str, Any]]:
    words = _words(q)
    try:
        from assistant.stores import chat_digest

        chats = chat_digest.list_chats_for_user(uid)
    except Exception as e:
        print(f"[project_sources] chats uid={uid} err={e!r}")
        return []
    out = []
    for c in chats:
        if c.get("chat_type") == "private":
            continue
        cid = int(c["chat_id"])
        title = str(c.get("title") or "Чат")
        try:
            topics = chat_digest.topic_names(cid)
        except Exception:
            topics = {}
        if _match(words, title):
            out.append({"kind": "tgchat", "id": str(cid), "title": title,
                        "subtitle": "чат Telegram" + (f" · {len(topics)} топиков" if topics else "") + " · видят только участники чата"})
        for tid, name in sorted(topics.items()):
            if not name or not (words and _match(words, title, name)):
                continue
            out.append({"kind": "tgchat", "id": f"{cid}:{int(tid)}", "title": f"{title} · {name}", "subtitle": "топик чата"})
    return out[:LIMIT]


# ---------- почта ----------


def mail(uid: int, q: str = "") -> list[dict[str, Any]]:
    from assistant.stores import mail_index

    words = _words(q)
    tz = _tz(uid)
    out: list[dict[str, Any]] = []
    raw = str(q or "").strip().lower()
    if "@" in raw and " " not in raw and "." in raw.split("@")[-1]:
        out.append({"kind": "mailaddr", "id": raw, "title": raw,
                    "subtitle": "адрес — Лео будет предлагать новые письма от него"})
    try:
        if words:
            rows = mail_index.search(uid, q, limit=25)
        else:
            rows = mail_index.recent(uid, since=datetime.now(timezone.utc) - timedelta(days=30))
            rows.sort(key=lambda r: str(r.get("date_utc") or ""), reverse=True)
    except Exception as e:
        print(f"[project_sources] mail uid={uid} err={e!r}")
        rows = []
    seen: set[str] = set()
    for r in rows:
        tk = str(r.get("thread_key") or "")
        if not tk or tk in seen or r.get("bulk"):
            continue
        seen.add(tk)
        who = str(r.get("from_name") or r.get("from_addr") or "")
        when = ""
        try:
            when = _day(datetime.fromisoformat(str(r.get("date_utc"))), tz)
        except Exception:
            pass
        out.append({"kind": "mail", "id": tk, "title": str(r.get("subject") or "").strip() or "Без темы",
                    "subtitle": " · ".join(x for x in ("переписка", who, when, "видна только вам") if x)})
        if len(out) >= 20:
            break
    try:
        addrs = sorted(a for a in mail_index.correspondents(uid) if words and _match(words, a))
    except Exception:
        addrs = []
    for a in addrs[:8]:
        if a.lower() != raw:
            out.append({"kind": "mailaddr", "id": a.lower(), "title": a.lower(),
                        "subtitle": "адрес — Лео будет предлагать новые письма от него"})
    return out[:LIMIT]


def for_tab(uid: int, tab: str, q: str = "") -> list[dict[str, Any]]:
    if tab == "meetings":
        return meetings(uid, q)
    if tab == "tgchats":
        return tgchats(uid, q)
    if tab == "mail":
        return mail(uid, q)
    return []

