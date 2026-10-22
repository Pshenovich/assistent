"""Лео в Telegram: скрытый тред, история как в Orbita, модель, маршрутизация в агента."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from assistant.nlu.intent_router import Route


@pytest.fixture()
def threads_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHAT_THREADS_DB_PATH", str(tmp_path / "chat_threads.sqlite"))
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import chat_threads as store
    from assistant.stores import notes as notes_store

    store._CONN = None  # type: ignore[attr-defined]
    notes_store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]
    notes_store._CONN = None  # type: ignore[attr-defined]


def test_telegram_thread_is_single_and_hidden(threads_db):
    store = threads_db
    visible = store.create_thread(7)
    t1 = store.telegram_thread(7)
    t2 = store.telegram_thread(7)
    assert t1["id"] == t2["id"]
    assert t1["origin"] == "telegram"
    assert t1["title"] == "Лео · Telegram"
    assert [r["id"] for r in store.list_threads(7)] == [visible["id"]]
    # Клиент не может создать служебный тред через POST /chats.
    assert store.create_thread(7, origin="telegram")["origin"] == ""


def _c(cid: int, body: str, *, gpt: bool = False, at: datetime | None = None) -> dict:
    ts = (at or datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)) + timedelta(seconds=cid)
    return {
        "id": cid,
        "body": body,
        "created_at": ts.isoformat(),
        "author_username": "gpt" if gpt else "vova",
        "author_name": "GPT-5.5" if gpt else "Вова",
        "prefix": "__gpt__" if gpt else "",
    }


def test_history_roles_cut_and_marker(monkeypatch):
    from assistant.services import gpt_calendar_tools as gcal
    from assistant.services import leo_agent

    monkeypatch.setattr(
        gcal,
        "actions_by_ids",
        lambda ids: [
            {"id": i, "kind": "event_create", "status": "done", "title": "Созвон", "owner_uid": 1,
             "payload": {}, "result": {"when": "пт 15:00"}}
            for i in ids
        ],
    )
    comments = [
        _c(1, "старое"),
        _c(2, "что у меня в пятницу?"),
        _c(3, "Поставил созвон.\n\n[[leo-cal:abc123]]", gpt=True),
        _c(4, "перенеси на 16"),
    ]
    hist = leo_agent.history_from_comments(comments, cut_id=1)
    assert [h["role"] for h in hist] == ["user", "assistant", "user"]
    assert "[[leo-cal" not in hist[1]["content"]
    assert "Уже выполнено, не повторять: Созвон — пт 15:00" in hist[1]["content"]


def test_history_limits_and_idle_reset():
    from assistant.services import leo_agent

    many = [_c(i, f"msg {i}") for i in range(1, 40)]
    assert len(leo_agent.history_from_comments(many)) == leo_agent.HISTORY_MAX_TURNS

    big = [_c(i, "x" * 7000) for i in range(1, 6)]
    hist = leo_agent.history_from_comments(big)
    assert all(len(h["content"]) <= leo_agent.HISTORY_MSG_MAX for h in hist)
    assert sum(len(h["content"]) for h in hist) <= leo_agent.HISTORY_MAX_CHARS

    t0 = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    rows = [_c(1, "вчерашняя тема", at=t0), _c(2, "новая тема", at=t0 + timedelta(hours=9))]
    now = t0 + timedelta(hours=9, minutes=1)
    hist = leo_agent.history_from_comments(rows, idle_reset=timedelta(hours=6), now=now)
    assert [h["content"] for h in hist] == ["новая тема"]


def test_tg_leo_model_default_and_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("USER_PREFS_DIR", str(tmp_path))
    from assistant.stores import user_prefs

    assert user_prefs.get_tg_leo_model(5) == "openai/gpt-5.5"
    user_prefs.set_tg_leo_model(5, "openai/gpt-4.1")
    assert user_prefs.get_tg_leo_model(5) == "openai/gpt-4.1"
    with pytest.raises(ValueError):
        user_prefs.set_tg_leo_model(5, "evil/model")
    # Модель убрали из списка — откатываемся к дефолту.
    prefs = user_prefs.load_prefs(5)
    prefs[user_prefs.TG_LEO_MODEL_KEY] = "openai/removed"
    user_prefs.save_prefs(5, prefs)
    assert user_prefs.get_tg_leo_model(5) == "openai/gpt-5.5"


def test_goes_to_agent():
    from assistant.nlu.dispatch import goes_to_agent

    def r(skill, sub="", kind=""):
        return Route(skill=skill, sub_intent=sub, body="x", confidence=1.0, reason="t", calendar_kind=kind)

    assert goes_to_agent(r("ask"))
    assert goes_to_agent(r("reminder"))
    assert goes_to_agent(r("assign_task"))
    assert goes_to_agent(r("calendar", "create"))
    assert not goes_to_agent(r("calendar", "zoom_instant", "zoom"))
    assert not goes_to_agent(r("calendar", "telemost"))
    assert not goes_to_agent(r("calendar", "contacts"))
    assert not goes_to_agent(r("transcribe"))
    assert not goes_to_agent(r("zoom_record"))
    assert not goes_to_agent(r("journal_qa"))


def _update():
    msg = SimpleNamespace(
        text="", caption=None, document=None, audio=None, video=None, voice=None, reply_to_message=None
    )
    return SimpleNamespace(message=msg, effective_user=SimpleNamespace(id=1))


@pytest.mark.parametrize(
    ("text", "chat_type", "expect_agent"),
    [
        ("напомни через час позвонить Ане", "private", True),
        ("как дела?", "private", True),
        ("как дела?", "group", False),
        ("транскрибируй https://youtu.be/x", "private", False),
    ],
)
def test_route_text_agent_mode(monkeypatch, text, chat_type, expect_agent):
    from assistant import config
    from assistant.nlu import dispatch
    from assistant.skills import leo_chat

    monkeypatch.setattr(config, "TG_LEO_AGENT", True)
    monkeypatch.setattr(dispatch, "is_llm_configured", lambda: True)
    calls: dict = {}

    async def fake_agent(update, context, t, *, extras=None):
        calls["agent"] = t

    async def fake_dispatch_other(*a, **k):
        calls["other"] = True

    async def no_llm(*a, **k):
        raise AssertionError("LLM-классификатор не должен вызываться в режиме агента")

    monkeypatch.setattr(leo_chat, "handle", fake_agent)
    monkeypatch.setattr(dispatch.transcribe_skill, "handle_url", fake_dispatch_other)
    monkeypatch.setattr(dispatch, "classify_intent", no_llm)
    monkeypatch.setattr(dispatch.reminders_skill, "handle", fake_dispatch_other)
    if chat_type != "private":
        assert not dispatch.agent_enabled(chat_type)
        return
    upd = _update()
    upd.message.text = text
    handled = asyncio.run(
        dispatch.route_text(
            upd, SimpleNamespace(user_data={}), text, dispatch.RouteExtras(), chat_type=chat_type
        )
    )
    assert handled
    assert ("agent" in calls) is expect_agent
