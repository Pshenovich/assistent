"""Новое в обсуждении: плашка (живое событие), карточка в Фокусе, сохранение ответа ИИ сервером."""

from __future__ import annotations

import os
import tempfile

import pytest

import assistant.services.discussion_notify as dn
from assistant.services import proactive
from assistant.stores import discussion_presence as dp
from assistant.stores import discussion_unread as du
from assistant.stores import notes as notes_store


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    tmp = tempfile.TemporaryDirectory()
    monkeypatch.setenv("NOTES_DB_PATH", os.path.join(tmp.name, "notes.sqlite"))
    notes_store._CONN = None  # type: ignore[attr-defined]
    du._CONN = None  # type: ignore[attr-defined]
    yield
    notes_store._CONN = None  # type: ignore[attr-defined]
    du._CONN = None  # type: ignore[attr-defined]
    tmp.cleanup()


def _mute_channels(monkeypatch, members, present=()):
    monkeypatch.setattr(dn, "list_discussion_participants", lambda *a, **k: members)
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: set(present))
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "Планёрка")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    # внешние каналы выключены — плашка и Фокус всё равно должны быть
    monkeypatch.setattr("assistant.stores.user_prefs.discussion_notify_telegram", lambda uid: False)
    monkeypatch.setattr("assistant.stores.user_prefs.discussion_notify_web_push", lambda uid: False)


def test_message_raises_banner_event_and_focus_card(monkeypatch):
    events = []
    monkeypatch.setattr("assistant.realtime.notify.publish", lambda topic, ev: events.append((topic, ev)))
    _mute_channels(monkeypatch, [{"user_id": "7"}, {"user_id": "8"}, {"user_id": "9"}], present=["9"])

    for text in ("Посмотри таблицу", "И ещё вопрос"):
        dn.notify_discussion_comment(
            owner="7", kind="local", item_id="1", author_user_id="7", author_name="Вова", body=text
        )

    # автор (7) и тот, у кого обсуждение открыто (9), — без плашки
    got = [(t, e) for t, e in events if e["type"] == "discussion.message"]
    assert {t for t, _ in got} == {"user:8"}
    assert got[-1][1]["preview"] == "И ещё вопрос" and got[-1][1]["author"] == "Вова"

    cards = proactive.rule_discussions(8)
    assert len(cards) == 1
    c = cards[0]
    assert c["key"] == "discuss:local:1" and c["type"] == "discussion"
    assert c["title"] == "Вова: И ещё вопрос"
    assert "ещё 1" in c["source"] and "Планёрка" in c["source"]
    assert c["actions"][0]["act"] == "open_discussion"
    assert proactive.rule_discussions(7) == [] and proactive.rule_discussions(9) == []


def test_open_clears_card():
    du.bump(8, owner_user_id=7, kind="chat", item_id="5", title="Чат", author="Аня", preview="привет")
    assert du.clear(8, "chat", "5") is True
    assert du.clear(8, "chat", "5") is False
    assert proactive.rule_discussions(8) == []


def test_reply_body_matches_client_format():
    import usage_server as us

    body = us._gpt_reply_comment_body(
        {"answer": "Готово", "bullets": ["а", "б"], "calendar_actions": [{"id": "x1"}, {"id": "x2"}]},
        notice="Модель попроще",
    )
    assert body == "_Модель попроще_\n\nГотово\n\n• а\n• б\n\n[[leo-cal:x1,x2]]"
    assert us._gpt_reply_comment_body({"answer": "", "files": [{"id": 3}]}) == "📎"


def test_reply_cancel_is_per_user_and_once():
    import usage_server as us

    us._gpt_reply_cancel("8", "tok")
    assert us._gpt_reply_cancelled("7", "tok") is False
    assert us._gpt_reply_cancelled("8", "tok") is True
    assert us._gpt_reply_cancelled("8", "tok") is False


def test_server_saves_gpt_reply_into_discussion(monkeypatch):
    """Человек ушёл из обсуждения — ответ ИИ всё равно появляется в нём (сохраняет сервер)."""
    import usage_server as us
    from assistant.stores import share_comments

    notified = []
    monkeypatch.setattr(us, "_notify_discussion_comment", lambda **kw: notified.append(kw))
    share_comments._CONN = None  # type: ignore[attr-defined]
    note = notes_store.create_note(77, "Встреча", "текст")
    nid = str(note["id"])
    principal = us._MiniappPrincipal(77, {"username": "vova", "first_name": "Вова"})
    q = share_comments.add_comment(77, "local", nid, author_user_id=77, author_name="Вова", body="вопрос")

    res = us._save_gpt_reply(
        principal,
        kind="local",
        item_id=nid,
        agent_id="gpt",
        model="",
        reply=us._MiniappGptSaveReply(prefix=share_comments.GPT_PREFIX, parent_id=int(q["id"]), token="t1"),
        out={"answer": "Ответ", "bullets": [], "calendar_actions": [], "files": []},
    )
    assert res and res["comment"]["body"] == "Ответ"
    rows = share_comments.list_comments(77, "local", nid)
    assert [r["body"] for r in rows] == ["вопрос", "Ответ"]
    assert share_comments.is_gpt_comment(rows[-1])
    assert notified and notified[0]["is_assistant"] is True

    # «■ Стоп» дошёл раньше, чем модель закончила, — ответ не сохраняется
    us._gpt_reply_cancel("77", "t2")
    res2 = us._save_gpt_reply(
        principal,
        kind="local",
        item_id=nid,
        agent_id="gpt",
        model="",
        reply=us._MiniappGptSaveReply(token="t2"),
        out={"answer": "Лишний"},
    )
    assert res2 is None
    assert len(share_comments.list_comments(77, "local", nid)) == 2


def test_private_chat_reply_notifies_only_asker(monkeypatch):
    """Ответ в личном чате: участникам ничего, а ушедший спросивший получает плашку и карточку."""
    import usage_server as us
    from assistant.stores import note_chats, share_comments

    events = []
    monkeypatch.setattr("assistant.realtime.notify.publish", lambda topic, ev: events.append((topic, ev)))
    monkeypatch.setattr(note_chats, "comment_visible", lambda row, uid: uid is not None)
    monkeypatch.setattr(us, "_notify_discussion_comment", lambda **kw: pytest.fail("участникам не рассылаем"))
    share_comments._CONN = None  # type: ignore[attr-defined]
    nid = str(notes_store.create_note(77, "Встреча", "текст")["id"])
    principal = us._MiniappPrincipal(77, {"username": "vova"})

    us._save_gpt_reply(
        principal, kind="local", item_id=nid, agent_id="gpt", model="",
        reply=us._MiniappGptSaveReply(), out={"answer": "Риски: сроки"},
    )
    assert [t for t, e in events if e["type"] == "discussion.message"] == ["user:77"]
    assert [c["title"] for c in proactive.rule_discussions(77)][0].endswith("Риски: сроки")
