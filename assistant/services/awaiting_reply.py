"""«Ждут вашего ответа»: одна сводная карточка Фокуса в конце дня.

Модель только находит в дайджесте чата вопросы/просьбы (kind=request) — остальное проверяет код:
- вопрос адресован пользователю: ответ на его сообщение, @упоминание, имя в тексте или чат 1:1;
- прошло больше 2 часов, пользователь после него в чате не писал и никто не ответил реплаем;
- чат не отключён (дважды «Не мне» в одном чате — вопросы оттуда больше не поднимаем).
Показывается с AWAITING_HOUR (18:00) по времени пользователя до конца дня.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

MIN_AGE = timedelta(hours=2)
LOOKBACK_DAYS = 3
NOT_ME_MUTE = 2


def awaiting_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("AWAITING_REPLY_HOUR", "18") or "18")))
    except ValueError:
        return 18


def _identity(uid: int) -> tuple[set[str], set[str]]:
    """(@ники в нижнем регистре, канонические основы имени) пользователя."""
    from assistant.lib.summary_normalize import _canon_first
    from assistant.stores import note_members

    prof = note_members.get_profile(uid) or {}
    nicks = {f"@{str(prof.get('username') or '').lower().lstrip('@')}"} - {"@"}
    names = {_canon_first(str(prof.get("first_name") or "").lower())} - {""}
    return nicks, names


def addressed_to_me(uid: int, msg: dict[str, Any], *, nicks: set[str], names: set[str], one_on_one: bool) -> bool:
    from assistant.lib.summary_normalize import _canon_first

    if msg.get("reply_to_user_id") and int(msg["reply_to_user_id"]) == int(uid):
        return True
    mentions = {m for m in str(msg.get("mentions") or "").split(",") if m}
    if f"id:{int(uid)}" in mentions or (nicks & mentions):
        return True
    text = str(msg.get("text") or "").lower()
    if any(n in text for n in nicks):
        return True
    if names and any(_canon_first(w) in names for w in re.findall(r"[а-яёa-z]{3,}", text)):
        return True
    return one_on_one


def answered(uid: int, msg: dict[str, Any], later: list[dict[str, Any]]) -> bool:
    for m in later:
        if m.get("user_id") is not None and int(m["user_id"]) == int(uid):
            return True  # пользователь после вопроса писал в этот чат
        if m.get("reply_to_message_id") and int(m["reply_to_message_id"]) == int(msg["message_id"]):
            return True  # на вопрос ответили реплаем (не обязательно пользователь)
    return False


def muted_chats(uid: int) -> set[int]:
    from assistant.services import focus_items

    counts: dict[int, int] = {}
    for r in focus_items.rows(uid):
        if r.get("reason") != "not_me":
            continue
        try:
            cid = int((r.get("source") or {}).get("id"))
        except (TypeError, ValueError):
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return {c for c, n in counts.items() if n >= NOT_ME_MUTE}


def pending_requests(uid: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Вопросы к пользователю без ответа (после всех проверок кодом)."""
    import json

    from assistant.services import focus_items, proactive_ai
    from assistant.stores import chat_digest

    now = now or datetime.now(timezone.utc)
    since_day = (now - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    with proactive_ai._LOCK:
        rows = proactive_ai._conn().execute(
            "SELECT source_key, title, items_json FROM proactive_insights WHERE owner_user_id = ? AND source_key LIKE 'digest:%'",
            (str(uid),),
        ).fetchall()
    hidden = focus_items.hidden_keys(uid, now)
    muted = muted_chats(uid)
    nicks, names = _identity(uid)
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for r in rows:
        try:
            _, cid_s, day = str(r["source_key"]).split(":", 2)
            cid = int(cid_s)
            items = json.loads(r["items_json"] or "[]")
        except ValueError:
            continue
        if day < since_day or cid in muted:
            continue
        one_on_one = len(chat_digest.chat_author_ids(cid, (now - timedelta(days=30)).isoformat()) - {int(uid)}) <= 1
        for it in items:
            if it.get("kind") != "request":
                continue
            try:
                mid = int(it.get("msg") or 0)
            except (TypeError, ValueError):
                mid = 0
            if not mid or (cid, mid) in seen:
                continue  # без номера сообщения проверить нельзя — не поднимаем
            key = "chat:%s:%s:%s" % (cid, day, proactive_ai._hash("request", str(it.get("text") or ""))[:10])
            if key in hidden:
                continue
            msg = chat_digest.get_message(cid, mid)
            if not msg or (msg.get("user_id") is not None and int(msg["user_id"]) == int(uid)):
                continue
            ts = proactive_ai._parse(msg.get("ts_utc"))
            if not ts or now - ts < MIN_AGE:
                continue
            if not addressed_to_me(uid, msg, nicks=nicks, names=names, one_on_one=one_on_one):
                continue
            if answered(uid, msg, chat_digest.messages_after(cid, str(msg["ts_utc"]))):
                continue
            seen.add((cid, mid))
            out.append({
                "key": key, "chat_id": cid, "chat_title": str(r["title"] or ""), "day": day, "msg": mid,
                "text": str(it.get("text") or ""), "who": str(msg.get("display_name") or it.get("who") or ""),
                "asked_at": ts.isoformat(), "url": proactive_ai.chat_message_url(cid, mid),
            })
    out.sort(key=lambda x: x["asked_at"])
    return out


def awaiting_card(uid: int, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    from assistant.services.calendar import _tz_for

    now = now or datetime.now(timezone.utc)
    local = now.astimezone(_tz_for(uid))
    if local.hour < awaiting_hour():
        return None
    reqs = pending_requests(uid, now=now)
    if not reqs:
        return None
    n = len(reqs)
    word = "вопрос" if n % 10 == 1 and n % 100 != 11 else ("вопроса" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "вопросов")
    items = [{"id": q["key"], "text": f"«{q['chat_title']}» · {q['who']}: {q['text']}", "url": q["url"]} for q in reqs[:12]]
    lines = [f"— {i['text']} ({i['url']})" if i["url"] else f"— {i['text']}" for i in items]
    return {
        "key": f"awaiting:{local.date().isoformat()}",
        "type": "awaiting_reply",
        "tag": "💬 ждут ответа",
        "urgency": 3,
        "title": f"Ждут вашего ответа: {n} {word}",
        "source": "без ответа больше 2 часов",
        "ask": "Ждут моего ответа в чатах:\n" + "\n".join(lines),
        "items": items,
        "actions": [
            {"label": "Разобрать с Лео", "act": "assistant", "primary": True,
             "payload": {"prompt": "Мне задали вопросы в чатах, и я не ответил. Помоги коротко ответить на каждый:\n" + "\n".join(lines)}},
            {"label": "Ответил / не мне…", "act": "mark_done_pick"},
        ],
    }
