"""Кредиты: формула, личный баланс, лимит участника и счёт организации."""

from __future__ import annotations

import multiprocessing as mp
import os

import pytest

from assistant.stores import credits_store as cs


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch, tmp_path):
    monkeypatch.setenv("CREDITS_DB_PATH", str(tmp_path / "credits.sqlite"))
    monkeypatch.setenv("USD_TO_RUB", "92")


def test_formula():
    assert cs.credits_for_cost_usd(1.0) == pytest.approx(230.0)  # 92 × 2 / 0,8
    assert cs.credits_for_minutes(30) == 60
    assert cs.credits_to_rub(100) == pytest.approx(130.0)


def test_personal_balance_and_topup():
    assert cs.balance("user", 1) == 2500
    cs.charge(1, 2400, org_id=None)
    st = cs.status(1, org_id=None)
    assert st["balance"] == pytest.approx(100) and st["can_answer"]
    cs.charge(1, 200, org_id=None)
    st = cs.status(1, org_id=None)
    assert st["state"] == "empty" and not st["can_answer"]
    assert cs.top_up("user", 1) == pytest.approx(2400)
    assert cs.status(1, org_id=None)["can_answer"]


def test_member_limit_then_org_pays():
    assert cs.status(5, org_id="o1")["daily_limit"] == 500
    r = cs.charge(5, 400, org_id="o1")
    assert r["from_org"] == 0
    r = cs.charge(5, 300, org_id="o1")  # 100 в пределах, 200 — со счёта организации
    assert r["from_org"] == pytest.approx(200)
    st = cs.status(5, org_id="o1")
    assert st["state"] == "over_limit" and st["can_answer"]
    assert st["org_balance"] == pytest.approx(20000 - 200)
    cs.charge(5, 50, org_id="o1")
    assert cs.overage_by_member("o1", cs.today_utc(), cs.today_utc()) == {"5": pytest.approx(250)}


def test_org_empty_blocks_over_limit_member():
    cs.set_member_settings("o2", 7, daily_limit=10)
    cs.charge(7, 20010, org_id="o2")  # 10 своих + 20000 со счёта
    st = cs.status(7, org_id="o2")
    assert st["state"] == "org_empty" and not st["can_answer"]
    cs.top_up("org", "o2", by_user=1)
    assert cs.status(7, org_id="o2")["can_answer"]


def test_allowed_models():
    assert cs.model_allowed("o3", 9, "anthropic/claude-opus-5.5")
    cs.set_member_settings("o3", 9, allowed_models=["openai/gpt-4.1-mini", "google/gemini-2.5-flash"])
    assert not cs.model_allowed("o3", 9, "anthropic/claude-opus-5.5")
    assert cs.model_allowed("o3", 9, "google/gemini-2.5-flash")
    assert cs.model_allowed(None, 9, "anthropic/claude-opus-5.5")  # без организации — без ограничений
    cs.set_member_settings("o3", 9, daily_limit=800)
    assert cs.member_settings("o3", 9) == {"daily_limit": 800, "allowed_models": ["google/gemini-2.5-flash", "openai/gpt-4.1-mini"]}
    cs.set_member_settings("o3", 9, clear_models=True)
    assert cs.member_settings("o3", 9)["allowed_models"] is None


def _spender(path: str, n: int) -> None:
    os.environ["CREDITS_DB_PATH"] = path
    from assistant.stores import credits_store

    for _ in range(n):
        credits_store.charge(3, 1.0, org_id=None)


def test_two_processes_lose_nothing(tmp_path):
    path = str(tmp_path / "credits.sqlite")
    cs.balance("user", 3)
    ctx = mp.get_context("spawn")
    ps = [ctx.Process(target=_spender, args=(path, 100)) for _ in range(2)]
    for p in ps:
        p.start()
    for p in ps:
        p.join(60)
    assert [p.exitcode for p in ps] == [0, 0]
    assert cs.balance("user", 3) == pytest.approx(2300)
    assert cs.spent_today(3) == pytest.approx(200)
