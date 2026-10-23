from datetime import date, datetime, timedelta, timezone

import pytest

from assistant.services import digest_overview

ME = 100
TODAY = date.today()


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import awaiting_reply, focus_items, proactive, proactive_ai
    from assistant.stores import chat_digest, user_prefs
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("CHAT_DIGEST_DB_PATH", str(tmp_path / "digest.sqlite"))
    for mod in (notes_store, focus_items, proactive_ai, chat_digest, proactive):
        mod._CONN = None
    monkeypatch.setattr(focus_items, "sync_source", lambda *a, **k: None)
    monkeypatch.setattr(user_prefs, "digest_enabled", lambda uid: True)
    monkeypatch.setattr(user_prefs, "digest_chat_ids", lambda uid: [-1001, -1002, -1003])
    for cid, title in [(-1001, "Продукт · ядро"), (-1002, "Продажи"), (-1003, "Офис")]:
        chat_digest.upsert_chat(cid, title=title)
    y = (TODAY - timedelta(days=1)).isoformat()
    chat_digest.upsert_report(report_date=TODAY.isoformat(), chat_id=-1001, message_count=40,
                              summary={"brief": "Релиз 2.4 — во вторник", "decisions": ["Стенд под нагрузку"],
                                       "next_steps": [{"assignee": "Анна", "task": "Отчёт по багам", "deadline": "18:00"}]})
    chat_digest.upsert_report(report_date=y, chat_id=-1001, message_count=12, summary={"brief": "Метрики онбординга"})
    chat_digest.upsert_report(report_date=y, chat_id=-1002, message_count=5, summary={"brief": "Firm недоволен сроками"})
    chat_digest.upsert_report(report_date=y, chat_id=-1003, message_count=3, summary={})
    req = {"key": "chat:-1001:%s:abc" % TODAY.isoformat(), "chat_id": -1001, "chat_title": "Продукт · ядро", "day": TODAY.isoformat(),
           "msg": 5, "text": "Апрув на релиз?", "who": "Анна", "asked_at": datetime.now(timezone.utc).isoformat(), "url": "https://t.me/c/1001/5"}
    monkeypatch.setattr(awaiting_reply, "pending_requests", lambda uid, now=None: [req])
    esc = {"key": "chat:-1002:%s:esc" % y, "type": "chat_escalation", "title": "Firm недоволен сроками",
           "chat": {"id": -1002, "date": y, "title": "Продажи"}, "items": [{"id": "chat:-1002:%s:esc" % y, "text": "x"}],
           "source": "чат «Продажи»", "_item": {"text": "Firm недоволен сроками", "who": "Олег", "due": ""}, "actions": []}
    monkeypatch.setattr(proactive_ai, "chat_cards", lambda uid, **kw: [esc])
    return req


def test_overview_groups_by_chat_with_labels_first(env):
    res = digest_overview.overview(ME)
    titles = [c["title"] for c in res["chats"]]
    assert titles[:2] == ["Продукт · ядро", "Продажи"] and titles[-1] == "Офис"
    first = res["chats"][0]
    assert first["brief"] == "Релиз 2.4 — во вторник" and first["days"] == 2 and first["awaiting"] == 1
    assert [lab["text"] for lab in res["chats"][1]["labels"]] == ["эскалация"]
    assert res["chats"][-1]["quiet"] and res["with_labels"] == 2


def test_chat_detail_days_and_awaiting(env):
    res = digest_overview.chat(ME, -1001)
    assert [d["date"] for d in res["days"]] == [TODAY.isoformat(), (TODAY - timedelta(days=1)).isoformat()]
    assert res["days"][0]["next_steps"][0] == {"who": "Анна", "task": "Отчёт по багам", "deadline": "18:00"}
    assert res["awaiting"][0]["text"] == "Апрув на релиз?" and res["url"] == "https://t.me/c/1"


def test_closing_here_closes_in_focus(env):
    from assistant.services import focus_items

    digest_overview.item_op(ME, env["key"], "not_me")
    row = focus_items.get(ME, env["key"])
    assert row["reason"] == "not_me" and env["key"] in focus_items.hidden_keys(ME)
    with pytest.raises(LookupError):
        digest_overview.item_op(ME, "chat:nope", "done")


def test_unknown_chat_is_not_found(env):
    with pytest.raises(LookupError):
        digest_overview.chat(ME, -5555)
