"""Тесты store / prefs / demo seed для Дайджеста."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


@pytest.fixture()
def digest_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "chat_digest.sqlite"
    monkeypatch.setenv("CHAT_DIGEST_DB_PATH", str(db))
    from assistant.stores import chat_digest as store

    store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]


@pytest.fixture()
def prefs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    d = tmp_path / "users"
    d.mkdir()
    monkeypatch.setenv("USER_PREFS_DIR", str(d))
    from assistant.stores import user_prefs

    return user_prefs


def test_group_gate_unchanged_without_mention():
    from assistant.bot.group_gate import should_process_in_chat
    from types import SimpleNamespace

    msg = SimpleNamespace(text="привет", entities=None, caption=None, caption_entities=None, reply_to_message=None)
    assert should_process_in_chat(msg, chat_type="supergroup", bot_id=1, bot_username="leo") is False


def test_insert_message_and_report(digest_db):
    store = digest_db
    store.upsert_chat(-100, title="Проект", chat_type="supergroup", touch_message=True)
    store.upsert_member(-100, 42, username="alice")
    assert store.insert_message(
        chat_id=-100,
        message_id=1,
        user_id=42,
        username="alice",
        display_name="Alice",
        text="Договорились сдать отчёт в пятницу",
        ts_utc=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
    )
    assert (
        store.insert_message(
            chat_id=-100,
            message_id=1,
            user_id=42,
            username="alice",
            display_name="Alice",
            text="dup",
            ts_utc=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        )
        is False
    )
    msgs = store.messages_for_chat_date(-100, "2026-10-01")
    assert len(msgs) == 1
    store.upsert_report(
        report_date="2026-10-01",
        chat_id=-100,
        summary={"brief": "Коротко", "decisions": [], "next_steps": [], "deadlines": [], "open_questions": [], "context_topics": []},
        message_count=1,
        model="demo",
    )
    reports = store.list_reports_for_chats([-100], limit=10)
    assert len(reports) == 1
    assert reports[0]["preview"] == "Коротко"
    assert reports[0]["item_id"] == "2026-10-01:-100"
    assert store.parse_item_id("2026-10-01:-100") == ("2026-10-01", -100)
    assert store.parse_item_id("bad") is None
    updated = store.update_report_summary(
        "2026-10-01",
        -100,
        {
            "brief": "Коротко",
            "editor_title": "Проект · 01.10.2026",
            "body_html": "<h3>Коротко</h3><p>Новый текст</p>",
        },
    )
    assert updated is not None
    assert updated["summary"]["editor_title"] == "Проект · 01.10.2026"
    assert "<p>Новый текст</p>" in updated["summary"]["body_html"]


def test_digest_prefs_default_off(prefs_dir):
    user_prefs = prefs_dir
    assert user_prefs.digest_enabled(7) is False
    assert user_prefs.digest_chat_ids(7) == []
    user_prefs.set_digest_enabled(7, True)
    user_prefs.set_digest_chat_ids(7, [1, 2, 2, "x", 3])
    assert user_prefs.digest_enabled(7) is True
    assert user_prefs.digest_chat_ids(7) == [1, 2, 3]


def test_operation_label_chat_digest():
    from assistant.lib.usage_store import operation_label_ru

    assert operation_label_ru("chat_digest") == "Дайджест чатов"


def test_seed_demo(digest_db, prefs_dir, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CHAT_DIGEST_DEMO_CHAT_IDS", raising=False)
    from assistant.services import chat_digest as svc

    out = svc.seed_demo_for_user(106)
    assert out["ok"] is True
    assert out["reports_updated"] >= 1
    assert prefs_dir.digest_enabled(106) is True
    selected = prefs_dir.digest_chat_ids(106)
    reports = digest_db.list_reports_for_chats(selected, limit=20)
    assert reports
    assert reports[0]["is_demo"] is True


def test_catchup_dates_since_last_report(digest_db, monkeypatch: pytest.MonkeyPatch):
    from assistant.services import chat_digest as svc

    monkeypatch.setenv("CHAT_DIGEST_MAX_CATCHUP_DAYS", "14")
    store = digest_db
    store.upsert_chat(-55, title="Ops", chat_type="supergroup", touch_message=True)
    for day, mid in (("2026-09-28", 1), ("2026-09-29", 2), ("2026-09-30", 3), ("2026-10-01", 4)):
        store.insert_message(
            chat_id=-55,
            message_id=mid,
            user_id=1,
            username="a",
            display_name="A",
            text="msg " + day,
            ts_utc=datetime.fromisoformat(day + "T12:00:00+00:00"),
        )
    store.upsert_report(
        report_date="2026-09-28",
        chat_id=-55,
        summary={"brief": "old", "decisions": [], "next_steps": [], "deadlines": [], "open_questions": [], "context_topics": []},
        message_count=1,
        model="demo",
    )
    days = svc.dates_to_analyze_for_chat(-55, end_day="2026-10-01")
    assert days == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert store.latest_report_date(-55) == "2026-09-28"
