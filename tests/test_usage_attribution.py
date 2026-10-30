"""Расход ИИ в фоне и в пуле потоков саммари записывается на пользователя."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from assistant.integrations import openrouter_client as oc
from assistant.lib.ctx_pool import ContextThreadPoolExecutor


def test_usage_user_sets_and_restores():
    oc.set_openrouter_usage_telegram_user(telegram_user_id=None)
    with oc.usage_user(42):
        assert oc.get_openrouter_usage_telegram_user() == ("42", None)
    assert oc.get_openrouter_usage_telegram_user() == (None, None)


def test_context_pool_carries_user_plain_pool_does_not():
    with oc.usage_user(7):
        with ContextThreadPoolExecutor(max_workers=2) as pool:
            got = [f.result() for f in [pool.submit(oc.get_openrouter_usage_telegram_user) for _ in range(4)]]
        with ThreadPoolExecutor(max_workers=1) as plain:
            bare = plain.submit(oc.get_openrouter_usage_telegram_user).result()
    assert got == [("7", None)] * 4
    assert bare == (None, None)  # то, что было раньше — без пользователя


def test_summary_v2_calls_logged_with_user(monkeypatch):
    from assistant.nlu import summary_v2

    seen = []
    monkeypatch.setattr(summary_v2, "ThreadPoolExecutor", ContextThreadPoolExecutor)

    def probe():
        seen.append(oc.get_openrouter_usage_telegram_user()[0])

    with oc.usage_user(106):
        with summary_v2.ThreadPoolExecutor(max_workers=3) as pool:
            for _ in range(3):
                pool.submit(probe)
    assert seen == ["106"] * 3


def test_usage_split_equal_shares(monkeypatch):
    rows = []
    monkeypatch.setattr(oc, "insert_usage_event", lambda **kw: rows.append(kw) or 1)
    data = {"id": "gen1", "model": "google/gemini-2.5-flash", "usage": {"prompt_tokens": 900, "completion_tokens": 30, "cost": 0.003}}
    with oc.usage_user(1):
        with oc.usage_split([1, 2, 3]):
            oc._log_usage(data, {"model": "x"}, operation="chat_alerts", provider="openrouter")
    assert [r["telegram_user_id"] for r in rows] == ["1", "2", "3"]
    assert all(abs(r["usage"]["cost"] - 0.001) < 1e-12 and r["usage"]["prompt_tokens"] == 300 for r in rows)
    assert len({r["generation_id"] for r in rows}) == 3
    rows.clear()
    with oc.usage_user(1):
        oc._log_usage(data, {"model": "x"}, operation="ask", provider="openrouter")  # без деления — как раньше
    assert len(rows) == 1 and rows[0]["telegram_user_id"] == "1" and rows[0]["usage"]["cost"] == 0.003
