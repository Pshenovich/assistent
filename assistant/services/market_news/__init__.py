"""Выпуск «Рынок» под организацию: хранение выпусков и сборка по расписанию.

Свой выпуск — у организации, где заданы конкуренты или темы (Профиль → Организация → «Выпуск «Рынок»»).
Остальные видят общий выпуск /news/ (его по-прежнему собирает /opt/obuchat-news).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
KEEP_DAYS = 30


def _conn():
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS market_issues (
            org_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            html TEXT NOT NULL,
            articles INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            PRIMARY KEY (org_id, day)
        )
        """
    )
    return conn


def is_personal(settings: dict[str, Any]) -> bool:
    return bool(settings.get("competitors") or settings.get("topics"))


def save_issue(org_id: int, day: str, page: str, articles: int = 0) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO market_issues (org_id, day, html, articles, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(org_id, day) DO UPDATE SET html = excluded.html, articles = excluded.articles, "
            "created_at = excluded.created_at",
            (int(org_id), day, page, int(articles), now),
        )
        old = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).date().isoformat()
        conn.execute("DELETE FROM market_issues WHERE day < ?", (old,))
        conn.commit()


def get_issue(org_id: int, day: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Выпуск за день; без дня — последний."""
    with _LOCK:
        if day:
            row = _conn().execute("SELECT * FROM market_issues WHERE org_id = ? AND day = ?", (int(org_id), day)).fetchone()
        else:
            row = _conn().execute("SELECT * FROM market_issues WHERE org_id = ? ORDER BY day DESC LIMIT 1",
                                  (int(org_id),)).fetchone()
    return dict(row) if row else None


def page_for_user(user_id: int, day: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Свой выпуск организации пользователя (если она настроила выпуск), иначе None — показываем общий."""
    from assistant.services import market_settings
    from assistant.stores import orgs_store

    org = orgs_store.current_org(int(user_id))
    if not org or not is_personal(market_settings.get(org["id"])):
        return None
    issue = get_issue(org["id"], day)
    return {**issue, "org_name": org["name"]} if issue else None


def _org_tz(org_id: int):
    """Часовой пояс выпуска — по владельцу организации."""
    from zoneinfo import ZoneInfo

    from assistant.stores import orgs_store, user_prefs

    owner = next((m for m in orgs_store.list_members(org_id) if m["role"] == "owner" and m["user_id"]), None)
    try:
        return user_prefs.get_user_tz(int(owner["user_id"])) if owner else ZoneInfo("Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def due_orgs(now: Optional[datetime] = None) -> list[int]:
    """Организации, которым пора собрать сегодняшний выпуск (за 20 минут до времени выпуска)."""
    from assistant.services import market_settings
    from assistant.stores import orgs_store

    now = now or datetime.now(timezone.utc)
    out = []
    for oid in orgs_store.all_org_ids():
        st = market_settings.get(oid)
        if not is_personal(st):
            continue
        tz = _org_tz(oid)
        local = now.astimezone(tz)
        hh, mm = (int(x) for x in st["time"].split(":"))
        build_at = local.replace(hour=hh, minute=mm, second=0, microsecond=0) - timedelta(minutes=20)
        if local >= build_at and not get_issue(oid, local.date().isoformat()):
            out.append(oid)
    return out


def build_for_org(org_id: int, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    from assistant.services import market_settings
    from assistant.services.market_news import generator
    from assistant.stores import orgs_store

    org = orgs_store.get_org(org_id)
    if not org:
        return None
    st = market_settings.get(org_id)
    res = generator.build_issue(org, st, now=now, tz=_org_tz(org_id))
    save_issue(org_id, res["date"], res["html"], res["articles"])
    print(f"[market_news] built org={org_id} day={res['date']} articles={res['articles']}")
    return res
