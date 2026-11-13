"""Продуктовые метрики Орбиты → дашборд «Орбита — продукт» (раздел «Дашборды»).

Сервер Орбиты сам себе источник: считает из data/analytics.sqlite и пишет прямо в
источники дашборда (dashboards.ingest), без HTTP и ключей dsk_. Дашборд и его
источники создал агент через MCP orbita-dashboards; здесь — только данные.

Включается переменной PRODUCT_DASHBOARD_ID (id дашборда). Источники ищутся по key:
active_users (DAU/WAU/MAU по дням), stickiness (%), retention_w1 (%), cohorts (таблица),
features (таблица за 30 дней), feature_daily (действия по функциям в день).
Повторная запись безопасна: точка узнаётся по (день, серия), таблицы заменяются целиком.
Служебные аккаунты (product_data.internal_ids) не считаются.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from assistant.admin import product_data
from assistant.stores import analytics_store

HISTORY_DAYS = 90  # при первом запуске после старта процесса
RECENT_DAYS = 3  # дальше — пересылаем последние дни (правки задним числом)
COHORT_WEEKS = 8

_thread: Optional[threading.Thread] = None
_EPOCH = date(1970, 1, 1)


def dashboard_id() -> int:
    try:
        return int(os.getenv("PRODUCT_DASHBOARD_ID", "") or 0)
    except ValueError:
        return 0


def _minute(d: date) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() // 60)


# --- расчёт (без записи — удобно тестировать) -----------------------------------


def active_series(today: date, days: int, skip: set[str]) -> dict[str, list[dict[str, Any]]]:
    """DAU/WAU/MAU и липкость на каждый из последних `days` дней (окна — скользящие)."""
    lo = _minute(today - timedelta(days=days + 29))
    rows = analytics_store.query(
        "SELECT DISTINCT user_id, minute / 1440 AS d FROM activity_minutes WHERE minute >= ?", (lo,)
    )
    per_day: dict[date, set[str]] = defaultdict(set)
    for r in rows:
        uid = str(r["user_id"])
        if uid not in skip:
            per_day[_EPOCH + timedelta(days=int(r["d"]))].add(uid)

    def window(end: date, n: int) -> set[str]:
        out: set[str] = set()
        for i in range(n):
            out |= per_day.get(end - timedelta(days=i), set())
        return out

    points, sticky = [], []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        ts = d.isoformat()
        dau, wau, mau = len(per_day.get(d, ())), len(window(d, 7)), len(window(d, 30))
        points += [
            {"ts": ts, "series": "DAU", "value": dau},
            {"ts": ts, "series": "WAU", "value": wau},
            {"ts": ts, "series": "MAU", "value": mau},
        ]
        avg7 = statistics.mean(len(per_day.get(d - timedelta(days=k), ())) for k in range(7))
        sticky.append({"ts": ts, "value": round(100.0 * avg7 / mau, 1) if mau else 0})
    return {"active": points, "stickiness": sticky}


def cohorts(today: date, skip: set[str]) -> dict[str, Any]:
    """Недельные когорты: строки для таблицы и «вернулись на 2-й неделе» по последней полной когорте."""
    data = product_data._cohorts(today, skip, weeks=COHORT_WEEKS)
    rows, w1 = [], []
    for c in data:
        start = date.fromisoformat(c["week"])
        label = "с " + start.strftime("%d.%m")
        for k, v in enumerate(c["retention"]):
            if v is not None:
                rows.append({"cohort": label, "step": k, "pct": round(100.0 * v, 1), "size": c["size"]})
        # Неделя 1 закончилась целиком — только тогда цифра честная.
        if len(c["retention"]) > 1 and c["retention"][1] is not None and start + timedelta(days=14) <= today:
            w1.append({"ts": start.isoformat(), "value": round(100.0 * c["retention"][1], 1)})
    return {"rows": rows, "w1": w1}


def features(today: date, days: int, skip: set[str]) -> dict[str, Any]:
    """Действия по функциям по дням (за `days`) и сводка за 30 дней."""
    lo = today - timedelta(days=max(days, 30) - 1)
    rows = analytics_store.query(
        "SELECT day, user_id, event, props_json FROM events WHERE day >= ?", (lo.isoformat(),)
    )
    labels = {key: label for key, label, _ev, _cond in product_data.FEATURES}
    daily: Counter[tuple[str, str]] = Counter()
    users30: dict[str, set[str]] = defaultdict(set)
    n30: Counter[str] = Counter()
    mau: set[str] = set()
    cut30 = (today - timedelta(days=29)).isoformat()
    since = (today - timedelta(days=days - 1)).isoformat()
    for r in rows:
        uid = str(r["user_id"])
        if uid in skip:
            continue
        day = str(r["day"])
        try:
            props = json.loads(r["props_json"] or "{}")
        except ValueError:
            props = {}
        for key in product_data.feature_keys(str(r["event"]), props):
            if day >= since:
                daily[(day, key)] += 1
            if day >= cut30:
                users30[key].add(uid)
                n30[key] += 1
    # Доля — от MAU по минутам активности (как на /dashboard/product).
    for r in analytics_store.query(
        "SELECT DISTINCT user_id FROM activity_minutes WHERE minute >= ?", (_minute(today - timedelta(days=29)),)
    ):
        if str(r["user_id"]) not in skip:
            mau.add(str(r["user_id"]))
    table = []
    for key, label in labels.items():
        u, n = len(users30[key]), n30[key]
        table.append({
            "feature": label, "users": u, "share": round(100.0 * u / len(mau), 1) if mau else 0,
            "events": n, "per_user": round(n / u, 1) if u else 0,
        })
    table.sort(key=lambda x: (-x["users"], -x["events"]))
    points = [{"ts": d, "series": labels[k], "value": v} for (d, k), v in sorted(daily.items())]
    # Нули за дни без действий — чтобы мини-графики не «держали» прошлое значение.
    seen = {k for (_d, k) in daily}
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        for k in seen:
            if (d, k) not in daily:
                points.append({"ts": d, "series": labels[k], "value": 0})
    return {"daily": points, "table": table}


# --- запись ---------------------------------------------------------------------


def _push(dash_id: int, key: str, payload: dict[str, Any]) -> int:
    from assistant.stores import dashboards

    ds = dashboards.dataset_by_key(dash_id, key)
    if not ds:
        print(f"[product-dashboard] dash={dash_id} нет источника {key}")
        return 0
    accepted = 0
    items_key = "points" if "points" in payload else None
    if items_key and len(payload[items_key]) > 4000:
        items = payload[items_key]
        for i in range(0, len(items), 4000):
            accepted += int(dashboards.ingest(ds, {items_key: items[i:i + 4000]}).get("accepted") or 0)
        return accepted
    try:
        return int(dashboards.ingest(ds, payload).get("accepted") or 0)
    except ValueError as e:
        dashboards.record_error(ds["id"], str(e))
        raise


def push_once(*, days: int = RECENT_DAYS, today: Optional[date] = None) -> dict[str, int]:
    dash_id = dashboard_id()
    if not dash_id:
        return {}
    today = today or datetime.now(timezone.utc).date()
    analytics_store.flush()
    skip = product_data.internal_ids()
    act = active_series(today, days, skip)
    coh = cohorts(today, skip)
    feat = features(today, days, skip)
    return {
        "active_users": _push(dash_id, "active_users", {"points": act["active"]}),
        "stickiness": _push(dash_id, "stickiness", {"points": act["stickiness"]}),
        "retention_w1": _push(dash_id, "retention_w1", {"points": coh["w1"]}) if coh["w1"] else 0,
        "cohorts": _push(dash_id, "cohorts", {"mode": "replace", "rows": coh["rows"]}),
        "features": _push(dash_id, "features", {"mode": "replace", "rows": feat["table"]}),
        "feature_daily": _push(dash_id, "feature_daily", {"points": feat["daily"]}) if feat["daily"] else 0,
    }


def _loop() -> None:
    interval = max(60.0, float(os.getenv("PRODUCT_DASHBOARD_INTERVAL_SEC", "900") or 900))
    days, backoff = HISTORY_DAYS, 60.0
    time.sleep(60)  # дать серверу подняться
    while True:
        try:
            res = push_once(days=days)
            if res:
                print(f"[product-dashboard] pushed days={days} {res}")
            days, backoff = RECENT_DAYS, interval
        except Exception as e:
            print(f"[product-dashboard] push_fail err={e!r}")
            backoff = min(3600.0, backoff * 2)
        time.sleep(backoff)


def start() -> None:
    """Фоновый поток в веб-сервере. Без PRODUCT_DASHBOARD_ID ничего не делает."""
    global _thread
    if not dashboard_id() or (_thread is not None and _thread.is_alive()):
        return
    _thread = threading.Thread(target=_loop, name="product-dashboard", daemon=True)
    _thread.start()
    print(f"[product-dashboard] thread started dash={dashboard_id()}")
