"""Продуктовая аналитика: запись событий и минут, сессии, пересылка в PostHog, метрики /dashboard/product."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from assistant.admin import product_data
from assistant.lib import analytics_forward
from assistant.stores import analytics_store as an


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_DB_PATH", str(tmp_path / "analytics.sqlite"))
    monkeypatch.setenv("ANALYTICS_EXCLUDE_IDS", "")
    monkeypatch.setattr(analytics_forward, "_internal_cache", (0.0, frozenset()))
    an._last_minute.clear()
    an._throttle.clear()
    an.current_platform.set("")
    yield
    an.flush()


def _events() -> list[dict]:
    an.flush()
    return [dict(r) for r in an.query("SELECT * FROM events ORDER BY id")]


def test_track_writes_event_with_platform_and_clean_props():
    an.set_platform("pwa")
    an.track(
        42,
        "ai.ask",
        model_used="gpt-5.5",
        long="x" * 500,
        nested={"a": 1},
        **{"Bad Key": 1},
        n=3,
        ok=True,
    )
    [ev] = _events()
    assert ev["user_id"] == "42" and ev["event"] == "ai.ask" and ev["platform"] == "pwa"
    props = json.loads(ev["props_json"])
    assert props == {"model_used": "gpt-5.5", "long": "x" * 80, "n": 3, "ok": True}


def test_track_rejects_bad_event_unknown_user_and_platform():
    an.track("", "note.create")
    an.track("__unknown__", "note.create")
    an.track(1, "Note Create!")
    an.track(1, "note.create", platform="evil")
    [ev] = _events()
    assert ev["platform"] == ""  # неизвестная платформа не пишется


def test_track_never_raises_when_db_is_broken(tmp_path, monkeypatch):
    monkeypatch.setenv("ANALYTICS_DB_PATH", str(tmp_path))  # каталог вместо файла
    an.track(1, "note.create")
    an.touch(1, "web")
    an.flush()


def test_track_throttled():
    for _ in range(3):
        an.track_throttled("sheet:1", 600, 7, "sheet.edit")
    an.track_throttled("sheet:2", 600, 7, "sheet.edit")
    assert len(_events()) == 2


def test_touch_dedupes_minute_and_needs_platform():
    an.touch(5)  # платформы нет — не пишем
    an.touch(5, "web")
    an.touch(5, "web")
    an.touch(5, "tg_bot")
    an.flush()
    rows = an.query("SELECT platform FROM activity_minutes WHERE user_id='5' ORDER BY platform")
    assert [r["platform"] for r in rows] == ["tg_bot", "web"]


def _minutes(uid: str, platform: str, minutes: list[int]) -> None:
    an._write_batch([("touch", m * 60.0, uid, platform, m) for m in minutes])


def test_sessions_split_by_gap_and_platform():
    base = 30_000_000
    _minutes("1", "web", [base, base + 1, base + 5, base + 5 + an.SESSION_GAP_MIN + 1])
    _minutes("1", "tg_bot", [base + 2])
    rows = an.query("SELECT platform, start_minute, minutes FROM sessions ORDER BY platform, start_minute")
    got = [(r["platform"], r["start_minute"] - base, r["minutes"]) for r in rows]
    assert got == [("tg_bot", 2, 1), ("web", 0, 3), ("web", 6 + an.SESSION_GAP_MIN, 1)]


def test_emit_closed_sessions_once():
    base = 30_000_000
    _minutes("1", "web", [base, base + 1])
    now = (base + 1 + an.SESSION_GAP_MIN + 1) * 60.0
    assert an.emit_closed_sessions(now) == 1
    assert an.emit_closed_sessions(now) == 0
    [ev] = _events()
    assert ev["event"] == "session" and json.loads(ev["props_json"])["duration_min"] == 2


def test_forward_hashes_id_and_marks_sent(monkeypatch):
    monkeypatch.setenv("ANALYTICS_ID_SALT", "salt")
    monkeypatch.setenv("ANALYTICS_EXCLUDE_IDS", "99")
    an.track(123456, "bot.start", platform="tg_bot", ref="landing")
    an.track(99, "note.create", platform="web")
    an.flush()
    sent: list[list[dict]] = []
    assert analytics_forward.forward_once(post=sent.append) == 2
    batch = sent[0]
    blob = json.dumps(batch)
    assert "123456" not in blob  # наружу только хэш
    first, second = batch
    assert first["distinct_id"] == analytics_forward.distinct_id(123456)
    assert first["properties"]["platform"] == "tg_bot"
    assert first["properties"]["$set_once"] == {"first_platform": "tg_bot", "first_ref": "landing"}
    assert second["properties"]["$set"] == {"internal": True}
    assert analytics_forward.forward_once(post=sent.append) == 0


def test_forward_failure_keeps_events():
    an.track(1, "note.create", platform="web")
    an.flush()

    def boom(_batch):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        analytics_forward.forward_once(post=boom)
    assert len(an.pending_forward()) == 1


def test_forward_enabled_needs_key_and_salt(monkeypatch):
    monkeypatch.setenv("ANALYTICS_FORWARD", "posthog")
    monkeypatch.delenv("POSTHOG_KEY", raising=False)
    monkeypatch.setenv("ANALYTICS_ID_SALT", "s")
    assert not analytics_forward.enabled()
    monkeypatch.setenv("POSTHOG_KEY", "phc_x")
    assert analytics_forward.enabled()


def _day_minute(d: date, offset: int = 600) -> int:
    return int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() // 60) + offset


def test_product_metrics(monkeypatch):
    monkeypatch.setattr(product_data, "internal_ids", lambda: {"999"})
    monkeypatch.setattr(product_data, "_churn", lambda today, skip: [])
    today = date(2026, 11, 3)
    # 1 — каждый день недели в вебе, 2 — сегодня в боте, 999 — свой аккаунт.
    for i in range(7):
        _minutes("1", "web", [_day_minute(today - timedelta(days=i)), _day_minute(today - timedelta(days=i), 601)])
    _minutes("2", "tg_bot", [_day_minute(today)])
    _minutes("999", "web", [_day_minute(today)])
    ts = datetime(2026, 11, 3, 10, tzinfo=timezone.utc).timestamp()
    for uid, ev, plat, props in [
        ("1", "ai.ask", "web", {"context": "note", "model_requested": "leo/auto", "model_used": "claude-haiku-5.5"}),
        ("1", "presentation.generate", "web", {"action": "create", "theme": "mono"}),
        ("2", "bot.voice", "tg_bot", {}),
        ("999", "note.create", "web", {}),
    ]:
        an._write_batch([("event", ts, uid, ev, plat, props)])
    an.save_snapshot("2026-11-03", [("1", "notes", 10), ("2", "notes", 2), ("1", "contacts", 4), ("999", "notes", 100)])

    d = product_data.product(7, today=today)
    assert (d["dau"], d["wau"], d["mau"]) == (2, 2, 2)
    assert d["minutes_median"] == 2
    feats = {f["key"]: f for f in d["features"]}
    assert feats["ai_note"]["users"] == 1 and feats["tg_bot"]["users"] == 1 and feats["notes"]["users"] == 0
    assert d["themes"] == [("mono", 1)]
    assert d["models"] == [{"requested": "leo/auto", "used": "claude-haiku-5.5", "n": 1}]
    notes = next(e for e in d["entities"]["rows"] if e["metric"] == "notes")
    assert notes["users"] == 2 and notes["total"] == 12
    assert {p["platform"] for p in d["platforms"]} == {"web", "tg_bot"}


def test_cohorts_retention():
    today = date(2026, 11, 3)
    w0 = today - timedelta(days=14)
    _minutes("a", "web", [_day_minute(w0), _day_minute(w0 + timedelta(days=7))])
    _minutes("b", "web", [_day_minute(w0)])
    rows = product_data._cohorts(today, set())
    first = rows[0]
    assert first["size"] == 2
    assert first["retention"][0] == 1.0 and first["retention"][1] == 0.5


def test_bot_hooks_describe_handlers():
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

    from assistant.bot import analytics_hooks

    async def handle_voice(update, context):
        return None

    async def cb(update, context):
        return None

    assert analytics_hooks._describe(CommandHandler("new", cb)) == ("bot.command", {"name": "new"})
    assert analytics_hooks._describe(CallbackQueryHandler(cb, pattern=r"^(cal:|ced:|inv:)")) == (
        "bot.callback",
        {"group": "cal"},
    )
    assert analytics_hooks._describe(MessageHandler(filters.VOICE, handle_voice)) == ("bot.voice", {})
    assert analytics_hooks._describe(MessageHandler(filters.TEXT, cb)) is None


def test_snapshot_collects_without_failing():
    from assistant.bot import analytics_snapshot_job

    _minutes("77", "web", [int(time.time() // 60)])
    n = analytics_snapshot_job.run_snapshot("2026-11-03")
    assert n >= 1
    rows = an.query("SELECT DISTINCT user_id FROM user_daily_snapshot WHERE day='2026-11-03'")
    assert "77" in {r["user_id"] for r in rows}
