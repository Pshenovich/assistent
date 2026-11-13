"""Продуктовые метрики для /dashboard/product из data/analytics.sqlite.

Воронки, сегменты и произвольные срезы — в PostHog (assistant/lib/analytics_forward).
Здесь — то, что нужно видеть всегда и чего внешний инструмент не знает:
DAU/WAU/MAU и время по минутам активности, платформы, функции, сущности на
пользователя из ночного снимка, отток со списком людей. Свои аккаунты
(операторы и ANALYTICS_EXCLUDE_IDS) не считаются.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from assistant.stores import analytics_store

PLATFORM_LABEL = {
    "web": "Веб",
    "pwa": "Приложение (PWA)",
    "tg_bot": "Telegram-бот",
}

# Функции продукта: событие («bot.*» — по префиксу; если нужно, со значением свойства) → подпись.
FEATURES: list[tuple[str, str, str | None, str | None]] = [
    ("notes", "Заметки (создание)", "note.create", None),
    ("note_edit", "Правка заметок и листов", "sheet.edit", None),
    ("presentations", "Презентации", "presentation.generate", None),
    ("boards_manual", "Доски (создание руками)", "sheet.create", "kind=board"),
    ("boards_ai", "Доски (ИИ рисует)", "board.ai_apply", None),
    ("ai_note", "ИИ в заметке", "ai.ask", "context=note"),
    ("ai_chat", "Чаты с ИИ", "ai.ask", "context=chat"),
    ("transcriptions", "Транскрипции", "transcription.create", None),
    ("summaries", "Саммари", "summary.create", None),
    ("focus", "Фокус", "focus.view", None),
    ("digest_chats", "Дайджест чатов", "digest.view", "kind=chats"),
    ("digest_mail", "Почта", "digest.view", "kind=mail"),
    ("digest_market", "Дайджест рынка", "digest.view", "kind=market"),
    ("kb", "База знаний", "kb.add_doc", None),
    ("tasks", "Задачи", "task.create", None),
    ("calendar", "Календарь (встречи)", "calendar.event_create", None),
    ("contacts", "Контакты", "contact.create", None),
    ("tg_bot", "Telegram-бот (любое действие)", "bot.*", None),
]

SNAPSHOT_LABEL = {
    "notes": "Заметки",
    "transcriptions": "Транскрипции",
    "summaries": "Саммари",
    "ai_chats": "Чаты с ИИ",
    "boards": "Доски",
    "presentations": "Презентации",
    "sheets_doc": "Доп. листы-документы",
    "contacts": "Контакты",
    "tasks": "Задачи",
    "reminders_active": "Активные напоминания",
    "knowledge_bases": "Базы знаний",
    "kb_documents": "Документы в базах знаний",
    "kb_notes": "Заметки базы знаний",
    "orgs": "Организации",
}


def feature_keys(ev: str, props: dict[str, Any]) -> list[str]:
    """Какие функции из FEATURES отмечает событие (с учётом условия на свойство)."""
    out = []
    for key, _label, fev, cond in FEATURES:
        if not (ev.startswith(fev[:-1]) if fev.endswith("*") else fev == ev):
            continue
        if cond:
            k, _, v = cond.partition("=")
            if str(props.get(k) or "") != v:
                continue
        out.append(key)
    return out


def platform_label(p: str) -> str:
    if p.startswith("tma-"):
        return "Миниапп Telegram · " + p[4:]
    return PLATFORM_LABEL.get(p, p or "—")


def internal_ids() -> set[str]:
    from assistant.lib import analytics_forward
    from assistant.lib import telegram_access_allowlist as access

    ids = set(analytics_forward.internal_user_ids())
    try:
        ids |= {str(u) for u in access.approver_user_ids()}
    except Exception:
        pass
    return ids


def _minute(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() // 60)


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return float(xs[i])


def product(days: int, *, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    skip = internal_ids()

    # Минуты по (пользователь, платформа, день) — одна выборка, дальше в Python.
    lo = _minute(min(start, today - timedelta(days=29)))
    rows = analytics_store.query(
        "SELECT user_id, platform, minute / 1440 AS d, COUNT(*) AS n FROM activity_minutes "
        "WHERE minute >= ? GROUP BY user_id, platform, d",
        (lo,),
    )
    epoch = date(1970, 1, 1)
    per_day_users: dict[date, set[str]] = defaultdict(set)
    user_day_min: dict[tuple[str, date], int] = defaultdict(int)
    plat_min: Counter[str] = Counter()
    plat_users: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        uid = str(r["user_id"])
        if uid in skip:
            continue
        d = epoch + timedelta(days=int(r["d"]))
        per_day_users[d].add(uid)
        if d >= start:
            user_day_min[(uid, d)] += int(r["n"])
            plat_min[str(r["platform"])] += int(r["n"])
            plat_users[str(r["platform"])].add(uid)

    def active(last: int) -> set[str]:
        out: set[str] = set()
        for i in range(last):
            out |= per_day_users.get(today - timedelta(days=i), set())
        return out

    dau, wau, mau = len(active(1)), len(active(7)), len(active(30))
    series = []
    d = start
    while d <= today:
        series.append({"date": d.isoformat(), "users": len(per_day_users.get(d, set()))})
        d += timedelta(days=1)
    avg_dau = statistics.mean(s["users"] for s in series[-7:]) if series else 0
    daily_minutes = [float(v) for v in user_day_min.values()]

    # Сессии — закрытые и открытые за период.
    srows = analytics_store.query(
        "SELECT user_id, platform, minutes FROM sessions WHERE start_minute >= ?", (_minute(start),)
    )
    sess = [r for r in srows if str(r["user_id"]) not in skip]
    sess_len = [float(r["minutes"]) for r in sess]
    user_days = len(user_day_min) or 1

    total_min = sum(plat_min.values()) or 1
    platforms = [
        {"platform": p, "label": platform_label(p), "minutes": n, "share": n / total_min, "users": len(plat_users[p])}
        for p, n in plat_min.most_common()
    ]

    return {
        "days": days,
        "dau": dau,
        "wau": wau,
        "mau": mau,
        "stickiness": (avg_dau / mau) if mau else 0.0,
        "series": series,
        "minutes_median": statistics.median(daily_minutes) if daily_minutes else 0.0,
        "minutes_p75": _pct(daily_minutes, 0.75),
        "sessions_per_day": len(sess) / user_days,
        "session_median": statistics.median(sess_len) if sess_len else 0.0,
        "platforms": platforms,
        **_events_block(start, skip, mau_users=active(30)),
        "entities": _entities(skip),
        "churn": _churn(today, skip),
        "cohorts": _cohorts(today, skip),
    }


def _events_block(start: date, skip: set[str], *, mau_users: set[str]) -> dict[str, Any]:
    rows = analytics_store.query(
        "SELECT user_id, event, platform, props_json FROM events WHERE day >= ?", (start.isoformat(),)
    )
    by_feature_users: dict[str, set[str]] = defaultdict(set)
    by_feature_n: Counter[str] = Counter()
    themes: Counter[str] = Counter()
    models: Counter[tuple[str, str]] = Counter()
    bot: Counter[str] = Counter()
    action_platform: Counter[str] = Counter()
    for r in rows:
        uid = str(r["user_id"])
        if uid in skip:
            continue
        ev = str(r["event"])
        try:
            props = json.loads(r["props_json"] or "{}")
        except ValueError:
            props = {}
        for key in feature_keys(ev, props):
            by_feature_users[key].add(uid)
            by_feature_n[key] += 1
        if ev == "presentation.generate" and props.get("action") == "create":
            themes[str(props.get("theme") or "default")] += 1
        elif ev == "ai.ask":
            models[(str(props.get("model_requested") or ""), str(props.get("model_used") or ""))] += 1
        elif ev == "bot.command":
            bot["/" + str(props.get("name") or "")] += 1
        elif ev == "bot.skill":
            bot["навык: " + str(props.get("skill") or "")] += 1
        elif ev in ("bot.voice", "bot.document"):
            bot[{"bot.voice": "голосовое", "bot.document": "файл / аудио / видео"}[ev]] += 1
        elif ev == "bot.callback":
            bot["кнопка: " + str(props.get("group") or "")] += 1
        if ev not in ("session", "digest.delivered", "note.open", "focus.view", "digest.view") and r["platform"] not in ("", "server"):
            action_platform[str(r["platform"])] += 1

    mau = len(mau_users) or 1
    features = [
        {"key": key, "label": label, "users": len(by_feature_users[key]), "share": len(by_feature_users[key]) / mau,
         "events": by_feature_n[key], "per_user": by_feature_n[key] / max(1, len(by_feature_users[key]))}
        for key, label, _ev, _cond in FEATURES
    ]
    features.sort(key=lambda f: (-f["users"], -f["events"]))
    total_actions = sum(action_platform.values()) or 1
    return {
        "features": features,
        "themes": themes.most_common(),
        "models": [{"requested": a, "used": b, "n": n} for (a, b), n in models.most_common(15)],
        "bot": bot.most_common(20),
        "action_platforms": [
            {"label": platform_label(p), "n": n, "share": n / total_actions} for p, n in action_platform.most_common()
        ],
    }


def _entities(skip: set[str]) -> dict[str, Any]:
    last = analytics_store.query("SELECT MAX(day) AS d FROM user_daily_snapshot")
    day = last[0]["d"] if last and last[0]["d"] else None
    if not day:
        return {"day": None, "rows": []}
    rows = analytics_store.query("SELECT user_id, metric, value FROM user_daily_snapshot WHERE day=?", (day,))
    per_metric: dict[str, list[float]] = defaultdict(list)
    users = {str(r["user_id"]) for r in rows if str(r["user_id"]) not in skip}
    vals: dict[tuple[str, str], float] = {(str(r["user_id"]), str(r["metric"])): float(r["value"]) for r in rows}
    for m in SNAPSHOT_LABEL:
        per_metric[m] = [vals.get((u, m), 0.0) for u in users]
    out = []
    for m, label in SNAPSHOT_LABEL.items():
        xs = per_metric[m]
        if not xs:
            continue
        out.append({
            "metric": m, "label": label, "users_with": sum(1 for x in xs if x > 0), "users": len(xs),
            "median": statistics.median(xs), "p75": _pct(xs, 0.75), "mean": statistics.mean(xs), "total": sum(xs),
        })
    return {"day": day, "rows": out}


def _churn(today: date, skip: set[str]) -> list[dict[str, Any]]:
    """Были активны в предыдущие 30 дней, молчат последние 14."""
    from assistant.admin import data

    cut = _minute(today - timedelta(days=13))
    lo = _minute(today - timedelta(days=44))
    rows = analytics_store.query(
        "SELECT user_id, MAX(minute) AS last, COUNT(*) AS n FROM activity_minutes WHERE minute >= ? GROUP BY user_id",
        (lo,),
    )
    profiles = data._profiles()
    out = []
    for r in rows:
        uid = str(r["user_id"])
        if uid in skip or int(r["last"]) >= cut:
            continue
        prof = profiles.get(uid)
        out.append({
            "user_id": uid,
            "name": data.display_name(prof, fallback="ID " + uid) if prof else "ID " + uid,
            "username": (prof or {}).get("username") or "",
            "last_seen": datetime.fromtimestamp(int(r["last"]) * 60, tz=timezone.utc),
            "minutes": int(r["n"]),
        })
    out.sort(key=lambda u: -u["minutes"])
    return out[:50]


def _cohorts(today: date, skip: set[str], weeks: int = 8) -> list[dict[str, Any]]:
    """Недельные когорты по первой минуте активности: доля вернувшихся на неделе N."""
    rows = analytics_store.query(
        "SELECT user_id, minute / 10080 AS w FROM activity_minutes GROUP BY user_id, w"
    )
    by_user: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if str(r["user_id"]) not in skip:
            by_user[str(r["user_id"])].add(int(r["w"]))
    now_w = _minute(today) // 10080
    cohorts: dict[int, list[set[int]]] = defaultdict(list)
    for ws in by_user.values():
        first = min(ws)
        if now_w - first <= weeks:
            cohorts[first].append({w - first for w in ws})
    out = []
    for first in sorted(cohorts):
        members = cohorts[first]
        start = date(1970, 1, 1) + timedelta(days=first * 7)
        ret = []
        for k in range(0, weeks + 1):
            if first + k > now_w:
                ret.append(None)
            else:
                ret.append(sum(1 for s in members if k in s) / len(members))
        out.append({"week": start.isoformat(), "size": len(members), "retention": ret})
    return out


def user_activity(uid: str, limit: int = 40) -> dict[str, Any]:
    """Для карточки пользователя: последние события, минуты за 30 дней, сущности."""
    since = _minute(datetime.now(timezone.utc).date() - timedelta(days=29))
    mins = analytics_store.query(
        "SELECT platform, COUNT(*) AS n FROM activity_minutes WHERE user_id=? AND minute>=? GROUP BY platform",
        (uid, since),
    )
    ev = analytics_store.query(
        "SELECT ts, event, platform, props_json FROM events WHERE user_id=? AND event != 'session' "
        "ORDER BY id DESC LIMIT ?",
        (uid, int(limit)),
    )
    snap = analytics_store.query(
        "SELECT metric, value FROM user_daily_snapshot WHERE user_id=? AND day=(SELECT MAX(day) FROM user_daily_snapshot)",
        (uid,),
    )
    return {
        "minutes": [{"label": platform_label(str(r["platform"])), "n": int(r["n"])} for r in mins],
        "events": [
            {"ts": datetime.fromtimestamp(float(r["ts"]), tz=timezone.utc), "event": r["event"],
             "platform": platform_label(str(r["platform"] or "")), "props": r["props_json"] or ""}
            for r in ev
        ],
        "entities": [{"label": SNAPSHOT_LABEL.get(str(r["metric"]), str(r["metric"])), "value": float(r["value"])}
                     for r in snap if float(r["value"]) > 0],
    }

