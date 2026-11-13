"""Продуктовые метрики → дашборд «Орбита — продукт» (assistant/lib/product_dashboard)."""

from __future__ import annotations

import itertools
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from assistant.lib import analytics_forward
from assistant.lib import product_dashboard as PD
from assistant.stores import analytics_store as an
from assistant.stores import dashboards as D

TODAY = date(2026, 11, 13)
_ids = itertools.count(981_000, 10)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setenv("ANALYTICS_EXCLUDE_IDS", "900")
    monkeypatch.setattr(analytics_forward, "_internal_cache", (0.0, frozenset()))
    yield
    an.flush()


def _minute(d: date, h: int = 10) -> int:
    return int(datetime(d.year, d.month, d.day, h, tzinfo=timezone.utc).timestamp() // 60)


def _seed():
    conn = an._conn()
    act = {  # пользователь → дни активности (смещение от TODAY)
        "1": [0, 1, 2, 8, 15],
        "2": [0, 9],
        "3": [20],
        "900": [0, 1],  # служебный — не считается
    }
    for uid, offs in act.items():
        for o in offs:
            conn.execute("INSERT INTO activity_minutes VALUES (?,?,?)", (uid, _minute(TODAY - timedelta(days=o)), "web"))
    ev = [
        ("1", 0, "note.create", {}), ("1", 0, "note.create", {}), ("2", 0, "ai.ask", {"context": "chat"}),
        ("2", 1, "ai.ask", {"context": "note"}), ("1", 1, "bot.command", {"name": "start"}),
        ("900", 0, "note.create", {}),
    ]
    for uid, o, name, props in ev:
        d = TODAY - timedelta(days=o)
        conn.execute(
            "INSERT INTO events (ts, day, user_id, event, platform, props_json) VALUES (?,?,?,?,?,?)",
            (_minute(d) * 60.0, d.isoformat(), uid, name, "web", json.dumps(props)),
        )
    conn.commit()


def test_active_series_rolling_windows_and_skips_internal():
    _seed()
    res = PD.active_series(TODAY, 3, {"900"})
    today = {p["series"]: p["value"] for p in res["active"] if p["ts"] == TODAY.isoformat()}
    assert today == {"DAU": 2, "WAU": 2, "MAU": 3}
    assert len(res["stickiness"]) == 3 and res["stickiness"][-1]["ts"] == TODAY.isoformat()


def test_features_table_and_daily_zeros():
    _seed()
    res = PD.features(TODAY, 3, {"900"})
    by = {r["feature"]: r for r in res["table"]}
    notes = by["Заметки (создание)"]
    assert notes["users"] == 1 and notes["events"] == 2 and notes["per_user"] == 2.0
    assert by["Чаты с ИИ"]["users"] == 1 and by["Telegram-бот (любое действие)"]["events"] == 1
    assert notes["share"] == round(100 / 3, 1)
    daily = {(p["ts"], p["series"]): p["value"] for p in res["daily"]}
    assert daily[(TODAY.isoformat(), "Заметки (создание)")] == 2
    assert daily[((TODAY - timedelta(days=1)).isoformat(), "Заметки (создание)")] == 0


def test_push_once_writes_into_dashboard(monkeypatch):
    _seed()
    owner = next(_ids)
    d = D.create(owner, "Орбита — продукт")
    for key, kind, cols in [
        ("active_users", "metric", None), ("stickiness", "metric", None), ("retention_w1", "metric", None),
        ("cohorts", "table", [{"name": n, "type": "text"} for n in ("cohort", "step", "pct", "size")]),
        ("features", "table", [{"name": n, "type": "text"} for n in ("feature", "users", "share", "events", "per_user")]),
        ("feature_daily", "metric", None),
    ]:
        D.create_dataset(owner, d["id"], key, kind, columns=cols)
    monkeypatch.setenv("PRODUCT_DASHBOARD_ID", str(d["id"]))
    monkeypatch.setattr(PD.product_data, "internal_ids", lambda: {"900"})
    res = PD.push_once(days=30, today=TODAY)
    assert res["active_users"] == 90 and res["features"] == len(PD.product_data.FEATURES)
    assert res["cohorts"] > 0 and res["feature_daily"] > 0
    again = PD.push_once(days=30, today=TODAY)  # повтор не дублирует
    ds = D.dataset_by_key(d["id"], "active_users")
    assert again["active_users"] == 90 and len(D.points(ds["id"])) == 90


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("PRODUCT_DASHBOARD_ID", raising=False)
    assert PD.push_once() == {}
