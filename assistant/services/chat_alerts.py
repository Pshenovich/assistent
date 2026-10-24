"""Критичные проблемы в рабочих чатах → пуш в Telegram сразу, не дожидаясь дайджеста.

Модель только предлагает (по новым сообщениям с контекстом топика), код проверяет:
- уровень «critical», сообщение из новой пачки и не от самого пользователя;
- пользователь ещё не писал в этом топике после него (значит, уже в курсе);
- по топику не было пуша последние COOLDOWN_H часов и его не глушили «Не критично»;
- не больше DAILY_MAX пушей в сутки.
Чаты — те, что выбраны в Дайджесте. Форумы — по топикам.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Optional

from assistant.stores import chat_digest as digest_store

LOOKBACK_H = 3  # первый проход по чату: сообщения за последние часы (не вся история)
COOLDOWN_H = 3
MUTE_H = 24
DAILY_MAX = 8
CONTEXT_PER_TOPIC = 20
MAX_TEXT = 16000

SYSTEM = """Ты следишь за рабочими чатами руководителя и решаешь, надо ли СРОЧНО его побеспокоить.
Тебе дают новые сообщения (помечены «NEW») с контекстом по топикам; сообщения самого руководителя помечены «Я».
critical — только то, что требует внимания руководителя в ближайшие часы:
- продукт/сервис/сайт/оплата/вход не работает или массово ломается у пользователей или клиентов;
- потеря или утечка данных, взлом, доступы попали не туда;
- срывается запуск, эфир, поставка или дедлайн сегодня-завтра; работа команды встала;
- ключевой клиент или партнёр в ярости, грозит уйти, публичный скандал;
- в чате прямо пишут «срочно», «критично», «горит», «всё упало» про реальную проблему, а не в шутку.
НЕ critical: обычные баги и задачи, вопросы, планы, обсуждения, просьбы без срока, уже решённое («починили», «всё ок»), шутки.
Если проблема уже решена в этих же сообщениях — не поднимай её.
Ответ строго JSON:
{"alerts":[{"msg":номер NEW-сообщения, где суть проблемы,"severity":"critical|high","title":"что случилось, до 90 символов","why":"почему это срочно и что сейчас известно, до 160 символов"}]}
Не больше 3. Если ничего срочного — {"alerts":[]}."""


def enabled() -> bool:
    return os.getenv("CHAT_ALERTS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def poll_interval_sec() -> float:
    try:
        return max(60.0, float(os.getenv("CHAT_ALERTS_POLL_SEC", "180") or "180"))
    except ValueError:
        return 180.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()


_SCHEMA_READY = False


def _conn():
    global _SCHEMA_READY
    conn = digest_store._conn()
    if not _SCHEMA_READY:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leo_chat_alert_cursor (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                last_rowid INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leo_chat_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                thread_id INTEGER NOT NULL DEFAULT 0,
                message_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                why TEXT NOT NULL DEFAULT '',
                sent_at TEXT NOT NULL,
                muted_until TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leo_alerts_user ON leo_chat_alerts(user_id, chat_id, thread_id, sent_at)")
        conn.commit()
        _SCHEMA_READY = True
    return conn


def get_cursor(uid: int, chat_id: int) -> Optional[int]:
    with digest_store._LOCK:
        r = _conn().execute(
            "SELECT last_rowid FROM leo_chat_alert_cursor WHERE user_id = ? AND chat_id = ?", (int(uid), int(chat_id))
        ).fetchone()
    return int(r[0]) if r else None


def set_cursor(uid: int, chat_id: int, rowid: int) -> None:
    with digest_store._LOCK:
        conn = _conn()
        conn.execute(
            """
            INSERT INTO leo_chat_alert_cursor (user_id, chat_id, last_rowid) VALUES (?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET last_rowid = excluded.last_rowid
            """,
            (int(uid), int(chat_id), int(rowid)),
        )
        conn.commit()


def _topic_blocked(uid: int, chat_id: int, thread_id: int, now: datetime) -> bool:
    """Был пуш по топику за COOLDOWN_H или топик заглушён «Не критично»."""
    with digest_store._LOCK:
        r = _conn().execute(
            """
            SELECT 1 FROM leo_chat_alerts
            WHERE user_id = ? AND chat_id = ? AND thread_id = ? AND (sent_at > ? OR muted_until > ?)
            LIMIT 1
            """,
            (int(uid), int(chat_id), int(thread_id), _iso(now - timedelta(hours=COOLDOWN_H)), _iso(now)),
        ).fetchone()
    return r is not None


def _sent_today(uid: int, now: datetime) -> int:
    with digest_store._LOCK:
        r = _conn().execute(
            "SELECT COUNT(*) FROM leo_chat_alerts WHERE user_id = ? AND sent_at > ?",
            (int(uid), _iso(now - timedelta(hours=24))),
        ).fetchone()
    return int(r[0] or 0) if r else 0


def record_alert(uid: int, a: dict[str, Any], *, now: Optional[datetime] = None) -> int:
    with digest_store._LOCK:
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO leo_chat_alerts (user_id, chat_id, thread_id, message_id, title, why, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(uid), int(a["chat_id"]), int(a.get("thread_id") or 0), int(a["message_id"]), a["title"], a.get("why") or "", _iso(now or _now())),
        )
        conn.commit()
        return int(cur.lastrowid)


def mute_alert(uid: int, alert_id: int, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """«Не критично»: топик MUTE_H часов без срочных пушей. Чужой alert_id — None."""
    with digest_store._LOCK:
        conn = _conn()
        r = conn.execute("SELECT * FROM leo_chat_alerts WHERE id = ? AND user_id = ?", (int(alert_id), int(uid))).fetchone()
        if not r:
            return None
        conn.execute(
            "UPDATE leo_chat_alerts SET muted_until = ? WHERE id = ?",
            (_iso((now or _now()) + timedelta(hours=MUTE_H)), int(alert_id)),
        )
        conn.commit()
    print(f"[chat_alerts] muted uid={uid} alert={alert_id} chat={r['chat_id']} thread={r['thread_id']}")
    return dict(r)


def message_url(chat_id: int, thread_id: Optional[int], msg: int) -> str:
    s = str(int(chat_id))
    if not s.startswith("-100") or not msg:
        return ""
    if thread_id:
        return f"https://t.me/c/{s[4:]}/{int(thread_id)}/{int(msg)}"
    return f"https://t.me/c/{s[4:]}/{int(msg)}"


def _who(uid: int, m: dict[str, Any]) -> str:
    if m.get("user_id") is not None and int(m["user_id"]) == int(uid):
        return "Я"
    return str(m.get("display_name") or "").strip() or (f"@{m['username']}" if m.get("username") else "кто-то")


def _line(uid: int, m: dict[str, Any], *, new: bool) -> str:
    text = str(m.get("text") or "").strip()
    if not text:
        return ""
    ts = str(m.get("ts_utc") or "")
    mark = "NEW " if new else ""
    return f"{mark}#{m.get('message_id')} [{ts[11:16]}] {_who(uid, m)}: {text[:700]}"


def build_transcript(uid: int, chat_id: int, new: list[dict[str, Any]], *, budget: int = MAX_TEXT) -> str:
    """Новые сообщения по топикам, перед каждыми — хвост этого топика для контекста."""
    names = digest_store.topic_names(int(chat_id))
    order: list[Optional[int]] = []
    by: dict[Optional[int], list[dict[str, Any]]] = {}
    for m in new:
        tid = m.get("thread_id") or None
        if tid not in by:
            by[tid] = []
            order.append(tid)
        by[tid].append(m)
    per = max(2000, budget // max(1, len(order)))
    blocks: list[str] = []
    for tid in order:
        msgs = by[tid]
        ctx = digest_store.thread_tail(int(chat_id), tid, str(msgs[0].get("ts_utc") or ""), limit=CONTEXT_PER_TOPIC)
        lines = [ln for ln in (_line(uid, m, new=False) for m in ctx) if ln]
        lines += [ln for ln in (_line(uid, m, new=True) for m in msgs) if ln]
        kept: list[str] = []
        size = 0
        for ln in reversed(lines):  # не влезает — режем старый контекст, новые сообщения в конце
            if size + len(ln) + 1 > per:
                break
            kept.append(ln)
            size += len(ln) + 1
        label = digest_store.topic_label(names, tid)
        head = f"### Топик «{label}»\n" if label else ""
        blocks.append(head + "\n".join(reversed(kept)))
    return "\n\n".join(blocks)[-budget:]


def parse_alerts(raw: str) -> list[dict[str, Any]]:
    from assistant.lib.llm_json import strip_json_from_markdown

    try:
        obj = json.loads(strip_json_from_markdown(raw or ""))
    except (json.JSONDecodeError, TypeError):
        return []
    out = []
    for it in (obj.get("alerts") if isinstance(obj, dict) else None) or []:
        if not isinstance(it, dict):
            continue
        try:
            msg = int(it.get("msg") or 0)
        except (TypeError, ValueError):
            continue
        title = re.sub(r"\s+", " ", str(it.get("title") or "")).strip()[:120]
        if not msg or not title:
            continue
        out.append({
            "msg": msg,
            "severity": str(it.get("severity") or "").strip().lower(),
            "title": title,
            "why": re.sub(r"\s+", " ", str(it.get("why") or "")).strip()[:220],
        })
    return out[:3]


def verify(
    uid: int, chat_id: int, new: list[dict[str, Any]], proposed: list[dict[str, Any]], *, now: Optional[datetime] = None
) -> list[dict[str, Any]]:
    """Код решает, что из предложенного моделью стоит пуша."""
    now = now or _now()
    by_id = {int(m["message_id"]): m for m in new}
    left = DAILY_MAX - _sent_today(uid, now)
    out: list[dict[str, Any]] = []
    seen_threads: set[int] = set()
    for p in proposed:
        if left <= 0:
            break
        if p.get("severity") != "critical":
            continue
        m = by_id.get(int(p["msg"]))
        if not m:
            continue  # модель сослалась не на новое сообщение (или выдумала номер)
        if m.get("user_id") is not None and int(m["user_id"]) == int(uid):
            continue
        tid = int(m.get("thread_id") or 0)
        if tid in seen_threads:
            continue
        # Пользователь уже писал в этом топике после сообщения — он в курсе.
        if any(
            x.get("user_id") is not None and int(x["user_id"]) == int(uid)
            and int(x.get("thread_id") or 0) == tid and str(x.get("ts_utc") or "") > str(m.get("ts_utc") or "")
            for x in new
        ):
            continue
        if _topic_blocked(uid, chat_id, tid, now):
            continue
        seen_threads.add(tid)
        left -= 1
        out.append({
            "chat_id": int(chat_id),
            "thread_id": tid,
            "message_id": int(m["message_id"]),
            "title": p["title"],
            "why": p.get("why") or "",
            "author": _who(uid, m),
            "ts_utc": str(m.get("ts_utc") or ""),
        })
    return out


def detect(uid: int, chat_id: int, new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.nlu import llm

    if not any(m.get("user_id") is None or int(m["user_id"]) != int(uid) for m in new):
        return []  # писал только сам пользователь
    chat = digest_store.get_chat(int(chat_id)) or {}
    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    payload = {
        "chat": str(chat.get("title") or f"Чат {chat_id}"),
        "now_utc": _iso(_now()),
        "messages": build_transcript(uid, chat_id, new),
    }
    raw = llm._chat(
        SYSTEM,
        json.dumps(payload, ensure_ascii=False),
        operation="chat_alerts",
        model=llm._model_chat_digest(),
        timeout=60,
        temperature=0.0,
        max_tokens=600,
    )
    return verify(uid, chat_id, new, parse_alerts(raw))


def user_enabled(uid: int) -> bool:
    from assistant.stores import user_prefs

    return bool(user_prefs.digest_enabled(uid) and user_prefs.proactive_prefs(uid).get("chat_alerts"))


def scan_user(uid: int) -> list[dict[str, Any]]:
    """Новые сообщения выбранных чатов → проверенные алерты (курсор сдвигается после разбора)."""
    from assistant.stores import user_prefs

    out: list[dict[str, Any]] = []
    for chat_id in user_prefs.digest_chat_ids(uid):
        cursor = get_cursor(uid, chat_id)
        if cursor is None:
            cursor = digest_store.max_rowid(chat_id, since_utc=_iso(_now() - timedelta(hours=LOOKBACK_H)))
            set_cursor(uid, chat_id, cursor)
        new = digest_store.messages_after_rowid(chat_id, cursor)
        if not new:
            continue
        try:
            found = detect(uid, chat_id, new)
        except Exception as e:
            print(f"[chat_alerts] uid={uid} chat={chat_id} err={e!r}")
            continue  # курсор не сдвигаем — попробуем в следующий тик
        set_cursor(uid, chat_id, int(new[-1]["rid"]))
        for a in found:
            a["title_chat"] = str((digest_store.get_chat(chat_id) or {}).get("title") or "чат")
            a["topic"] = digest_store.topic_label(digest_store.topic_names(chat_id), a["thread_id"] or None)
        out.extend(found)
    return out


def message_html(uid: int, a: dict[str, Any]) -> str:
    where = escape(a.get("title_chat") or "чат")
    if a.get("topic"):
        where += " › " + escape(a["topic"])
    lines = [f"🚨 <b>Срочно · {where}</b>", "", f"<b>{escape(a['title'])}</b>"]
    if a.get("why"):
        lines.append(escape(a["why"]))
    when = ""
    try:
        from assistant.stores import user_prefs

        when = datetime.fromisoformat(str(a.get("ts_utc"))).astimezone(user_prefs.get_user_tz(uid)).strftime("%H:%M")
    except (TypeError, ValueError):
        pass
    lines.append(f"<i>{escape(a.get('author') or '')}{', ' + when if when else ''}</i>")
    return "\n".join(lines)
