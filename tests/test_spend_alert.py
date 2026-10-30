"""Всплеск расхода ИИ: предупреждаем при пороге и заметном отличии от обычного дня."""

from __future__ import annotations

from assistant.bot import spend_alert_job as sa
from assistant.lib import usage_store


def _rows(day_map):
    def fake(from_d, to_d, *a, **k):
        return day_map.get((from_d, to_d), [])
    return fake


def test_spike_detection(monkeypatch):
    monkeypatch.delenv("SPEND_ALERT_MIN_USD", raising=False)
    monkeypatch.delenv("SPEND_ALERT_RATIO", raising=False)
    today = [
        {"telegram_user_id": "1", "telegram_username": "big", "cost_usd": 6.0},    # обычно 0.5/день → всплеск
        {"telegram_user_id": "2", "telegram_username": "heavy", "cost_usd": 3.0},  # обычно 2/день → норма
        {"telegram_user_id": "3", "telegram_username": "new", "cost_usd": 2.5},    # новый, выше порога
        {"telegram_user_id": "4", "telegram_username": "small", "cost_usd": 0.3},  # ниже порога
        {"telegram_user_id": "__unknown__", "telegram_username": "", "cost_usd": 9.0},
    ]
    base = [
        {"telegram_user_id": "1", "cost_usd": 7.0},
        {"telegram_user_id": "2", "cost_usd": 28.0},
    ]
    monkeypatch.setattr(usage_store, "users_summary", _rows({
        ("2026-10-30", "2026-10-30"): today,
        ("2026-10-16", "2026-10-29"): base,
    }))
    got = {s["uid"] for s in sa.find_spikes("2026-10-30")}
    assert got == {"1", "3"}


def test_alert_once_per_day(monkeypatch):
    sent = []
    monkeypatch.setattr(sa, "find_spikes", lambda day=None: [{"uid": "1", "username": "big", "today_usd": 6.0, "avg_usd": 0.5}])
    monkeypatch.setattr(sa, "_ALERTED", {})
    import assistant.lib.telegram_access_allowlist as al
    import assistant.lib.telegram_notify as tn
    monkeypatch.setattr(al, "approver_user_ids", lambda: frozenset({100}))
    monkeypatch.setattr(tn, "send_message", lambda chat, text, **k: sent.append((chat, text)) or True)
    assert sa._tick_sync() == 1
    assert sa._tick_sync() == 0
    assert "@big" in sent[0][1] and "$6.00" in sent[0][1]
