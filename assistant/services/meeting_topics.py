"""Тема встречи: прошлые встречи и чаты, которые к ней относятся, и кандидаты «связать?».

Без LLM — по словам названий (meeting_topics.keys). Уверенно (без вопроса) — только тот же
нормализованный заголовок; остальное — кандидаты для карточки в Фокусе.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import meeting_topics as store

PAST_DAYS = 120
CHAT_ACTIVE_DAYS = 30


def _dt(s: Any) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(s or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def past_meetings(uid: int, before: Optional[datetime], *, days: int = PAST_DAYS) -> list[dict[str, Any]]:
    """Прошлые встречи с заметками: {event_key, title, start, note_ids}. Одна встреча — одна запись."""
    from assistant.stores import meeting_links

    before = before or datetime.now(timezone.utc)
    since = before - timedelta(days=days)
    rows = meeting_links._conn().execute(
        "SELECT note_id, event_key, summary, start_iso FROM meeting_links WHERE owner_user_id = ? "
        "ORDER BY start_iso DESC",
        (str(int(uid)),),
    ).fetchall()
    by_event: dict[str, dict[str, Any]] = {}
    for r in rows:
        st = _dt(r["start_iso"])
        if not st or st >= before or st < since:
            continue
        cur = by_event.setdefault(
            r["event_key"], {"event_key": r["event_key"], "title": r["summary"] or "", "start": st, "note_ids": []}
        )
        cur["note_ids"].append(int(r["note_id"]))
    return sorted(by_event.values(), key=lambda m: m["start"], reverse=True)


def _chats(uid: int) -> list[dict[str, Any]]:
    try:
        from assistant.stores import chat_digest

        return chat_digest.list_chats_for_user(int(uid))
    except Exception as e:
        print(f"[meeting_topics] chats uid={uid} err={e!r}")
        return []


def resolve(uid: int, title: str, *, before: Optional[datetime] = None, assume: bool = False) -> dict[str, Any]:
    """Что относится к теме встречи `title` у пользователя.

    titles — нормализованные названия встреч темы (своё всегда), chats — связанные чаты, где он состоит,
    candidates — похожие по слову, но не связанные и не отклонённые (для «связать?» в Фокусе).
    assume=True (превью) — кандидаты считаются связанными, в базу ничего не пишется.
    """
    own = store.norm_title(title)
    own_keys = store.keys(title)
    titles = {own} if own else set()
    chat_ids: set[int] = set()
    topic = store.topic_for_title(uid, title) if own else None
    if topic:
        for i in topic["items"]:
            if not i["active"]:
                continue
            if i["kind"] == "title":
                titles.add(i["value"])
            else:
                chat_ids.add(int(i["value"]))
    decided = store.decided(uid)
    cand_titles: list[dict[str, Any]] = []
    cand_chats: list[dict[str, Any]] = []
    meetings = past_meetings(uid, before)
    my_chats = _chats(uid)
    seen: set[str] = set()
    if own_keys:
        for m in meetings:
            n = store.norm_title(m["title"])
            if not n or n in titles or n in seen or ("title", n) in decided:
                continue
            common = own_keys & store.keys(m["title"])
            if common:
                seen.add(n)
                cand_titles.append({"kind": "title", "value": n, "label": m["title"], "why": sorted(common),
                                    "date": m["start"].date().isoformat(), "note_ids": m["note_ids"]})
        now = before or datetime.now(timezone.utc)
        for c in my_chats:
            cid = int(c["chat_id"])
            if cid in chat_ids or ("chat", str(cid)) in decided or str(c.get("chat_type") or "") == "private":
                continue
            last = _dt(c.get("last_message_at"))
            if not last or last < now - timedelta(days=CHAT_ACTIVE_DAYS):
                continue
            common = own_keys & store.keys(c.get("title") or "")
            if common:
                cand_chats.append({"kind": "chat", "value": str(cid), "label": c.get("title") or "",
                                   "why": sorted(common), "last_message_at": c.get("last_message_at")})
    if assume:
        titles |= {c["value"] for c in cand_titles}
        chat_ids |= {int(c["value"]) for c in cand_chats}
    return {
        "topic": topic,
        "titles": titles,
        "meetings": [m for m in meetings if store.norm_title(m["title"]) in titles],
        "chats": [c for c in my_chats if int(c["chat_id"]) in chat_ids],
        "candidates": {"titles": cand_titles, "chats": cand_chats},
    }


def _has_summary(uid: int, note_ids: list[int]) -> bool:
    from assistant.stores import notes as notes_store

    for nid in note_ids:
        try:
            note = notes_store.get_note(str(int(uid)), int(nid)) or {}
        except Exception:
            continue
        meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
        if meta.get("main_topic") or meta.get("tasks") or meta.get("decisions"):
            return True
    return False


def _sure(cand: dict[str, Any], strong: set[str]) -> bool:
    """Отмечать галочкой сразу: совпала аббревиатура темы или два слова; одно обычное слово — под вопросом."""
    why = set(cand.get("why") or [])
    return bool(why & strong) or len(why) >= 2


def link_card(uid: int, events: list[dict[str, Any]], now: datetime) -> Optional[dict[str, Any]]:
    """Карточка «связать тему?» для ближайшей встречи с людьми, у которой есть что связать."""
    import hashlib

    upcoming = []
    for ev in events:
        st = _dt(ev.get("start"))
        if not st or st <= now or st > now + timedelta(days=7) or ev.get("entry_type") == "task":
            continue
        if not [a for a in ev.get("attendees") or [] if isinstance(a, dict) and not (a.get("self") or a.get("is_self"))]:
            continue
        upcoming.append((st, ev))
    for st, ev in sorted(upcoming, key=lambda p: p[0]):
        title = str(ev.get("summary") or "").strip()
        if not store.keys(title):
            continue
        r = resolve(uid, title, before=now)
        titles = [c for c in r["candidates"]["titles"] if _has_summary(uid, c["note_ids"])]
        chats = r["candidates"]["chats"]
        if not titles and not chats:
            continue
        strong = store.strong_keys(title)
        items = []
        for c in titles + chats:
            items.append({
                "kind": c["kind"], "value": c["value"], "label": c["label"], "checked": _sure(c, strong),
                "why": c["why"], "date": c.get("date") or "", "last_message_at": c.get("last_message_at") or "",
            })
        if not any(i["checked"] for i in items):
            continue
        sig = hashlib.sha1("|".join(sorted(f"{i['kind']}:{i['value']}" for i in items)).encode()).hexdigest()[:10]
        n_t = sum(1 for i in items if i["kind"] == "title")
        chat_names = [i["label"] for i in items if i["kind"] == "chat" and i["checked"]]
        parts = []
        if n_t:
            parts.append(f"{n_t} {_plural(n_t, 'встреча', 'встречи', 'встреч')}")
        if chat_names:
            parts.append(f"чат «{chat_names[0]}»" if len(chat_names) == 1 else f"{len(chat_names)} чата")
        elif any(i["kind"] == "chat" for i in items):
            parts.append("чат")
        local = st.astimezone(_tz(uid))
        day = "сегодня" if local.date() == now.astimezone(_tz(uid)).date() else local.strftime("%d.%m")
        return {
            "key": f"topic_link:{store.norm_title(title)}:{sig}",
            "type": "topic_link",
            "tag": "🔗 связать тему",
            "urgency": 3 if day == "сегодня" else 2,
            "title": f"«{title}»: {' и '.join(parts)} — одна тема?",
            "source": f"Итоги и переписка попадут в бриф к «{title}» {day} в {local:%H:%M}",
            "topic": {"name": title, "topic_id": (r["topic"] or {}).get("id"), "items": items,
                      "anchor": {"kind": "title", "value": title, "label": title}},
            "actions": [{"label": "Связать", "act": "topic_link", "primary": True}],
        }
    return None


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    return one if n % 10 == 1 else few if 2 <= n % 10 <= 4 else many


def _tz(uid: int):
    from assistant.services.calendar import _tz_for

    return _tz_for(int(uid))
