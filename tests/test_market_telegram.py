"""Выпуск «Рынок» в Telegram: когда слать, что слать, один раз в день."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from assistant.services import market_settings
from assistant.stores import orgs_store, user_prefs

HTML = """<html><head><title>Обучат: отраслевой дайджест за 29.10.2026</title></head><body>
<div class="strategy-item"><button class="strategy-trigger" type="button"><span>AI-агенты &mdash; новая рамка продаж.</span>
<span class="strategy-hint">На основе чего?</span></button></div>
<div class="strategy-item"><button class="strategy-trigger" type="button"><span>Bitrix24 <b>усиливает</b> ИИ.</span></button></div>
</body></html>"""


def test_issue_digest_parses_date_and_items():
    day, items = market_settings.issue_digest(HTML)
    assert day == "2026-10-29"
    assert items == ["AI-агенты — новая рамка продаж.", "Bitrix24 усиливает ИИ."]


def test_due_message_time_once_and_fresh_issue(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    tz = ZoneInfo("Europe/Moscow")
    monkeypatch.setattr(user_prefs, "get_user_tz", lambda uid: tz)
    monkeypatch.setattr(orgs_store, "current_org", lambda uid: {"id": 1, "name": "Обучат"})
    monkeypatch.setattr(market_settings, "get", lambda org_id: {**market_settings.DEFAULTS, "time": "08:00"})
    early = datetime(2026, 10, 29, 7, 59, tzinfo=tz)
    on_time = datetime(2026, 10, 29, 8, 5, tzinfo=tz)
    assert market_settings.due_message(5, now=early, issue_html=HTML) == ""
    text = market_settings.due_message(5, now=on_time, issue_html=HTML)
    assert text.startswith("<b>Рынок · 29.10</b>") and "• AI-агенты" in text
    market_settings.mark_sent(5, "2026-10-29")
    assert market_settings.due_message(5, now=on_time, issue_html=HTML) == ""  # уже отправили сегодня
    next_day = datetime(2026, 10, 30, 9, 0, tzinfo=tz)
    assert market_settings.due_message(5, now=next_day, issue_html=HTML) == ""  # выпуск вчерашний — ждём свежий
    monkeypatch.setattr(orgs_store, "current_org", lambda uid: None)
    assert market_settings.due_message(5, now=on_time, issue_html=HTML) == ""


def test_telegram_user_ids(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    market_settings.set_telegram(7, True)
    market_settings.set_telegram(8, False)
    assert market_settings.telegram_user_ids() == [7]
