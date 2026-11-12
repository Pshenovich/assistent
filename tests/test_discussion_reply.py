"""«Ответить» под уведомлением из обсуждения документа/чата: ответ из Telegram уходит в то же обсуждение."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import assistant.services.discussion_notify as dn
from assistant.bot import task_reply

OWNER, GUEST, STRANGER = 501, 502, 503


@pytest.fixture()
def comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import notes as notes_store
    from assistant.stores import share_comments

    notes_store._CONN = None  # type: ignore[attr-defined]
    monkeypatch.setattr(
        dn, "list_discussion_participants", lambda *a, **k: [{"user_id": str(OWNER)}, {"user_id": str(GUEST)}]
    )
    monkeypatch.setattr("assistant.stores.discussion_presence.present_user_ids", lambda *a, **k: set())
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "План запуска")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    monkeypatch.setattr(dn, "_notify_in_app", lambda *a, **k: None)
    monkeypatch.setattr("assistant.stores.user_prefs.discussion_notify_telegram", lambda uid: True)
    monkeypatch.setattr("assistant.stores.user_prefs.discussion_notify_web_push", lambda uid: False)
    monkeypatch.setattr("assistant.stores.note_members.get_profile", lambda uid: {"first_name": "Ульяна"})
    yield share_comments
    notes_store._CONN = None  # type: ignore[attr-defined]


@pytest.fixture()
def tg(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text, markup=None: out.append({"to": uid, "text": text, "markup": markup}))
    return out


class _Msg:
    def __init__(self, reply_to=None):
        self.replies: list[str] = []
        self.reply_to_message = reply_to

    async def reply_text(self, text, **_):
        self.replies.append(text)


def _update(uid: int, reply_to=None):
    return SimpleNamespace(
        message=_Msg(reply_to),
        effective_user=SimpleNamespace(id=uid, full_name="TG Name"),
        effective_chat=SimpleNamespace(type="private", id=uid),
    )


def _ctx(uid: int, comment_id: int | None = None):
    ud = {"leo_task_reply": {"user_id": uid, "comment_id": comment_id, "at": time.time()}} if comment_id else {}
    return SimpleNamespace(user_data=ud, bot=SimpleNamespace(id=999))


def test_human_message_push_has_reply_button_assistant_does_not(comments, tg):
    dn.notify_discussion_comment(
        owner=str(OWNER), kind="local", item_id="1", author_user_id=str(OWNER),
        author_name="Вова", body="Посмотри план", comment_id=42,
    )
    assert [p["to"] for p in tg] == [GUEST]
    btn = tg[0]["markup"]["inline_keyboard"][0][0]
    assert btn["text"].endswith("Ответить") and btn["callback_data"] == "dsc:reply:42"
    tg.clear()
    dn.notify_discussion_comment(
        owner=str(OWNER), kind="local", item_id="1", author_user_id="0",
        author_name="Лео", body="Готово", is_assistant=True, comment_id=43,
    )
    assert tg and all(p["markup"] is None for p in tg)


def test_pending_reply_goes_into_same_discussion(comments, tg):
    parent = comments.add_comment(OWNER, "local", "1", author_user_id=OWNER, author_name="Вова", body="Посмотри план")
    ctx = _ctx(GUEST, parent["id"])
    upd = _update(GUEST)
    assert task_reply.any_reply_target(upd, ctx)
    assert asyncio.run(task_reply.try_consume_reply(upd, ctx, "Посмотрела, ок"))
    assert "leo_task_reply" not in ctx.user_data
    rows = [r for r in comments.list_comments(OWNER, "local", "1") if r["id"] != parent["id"]]
    assert len(rows) == 1
    r = rows[0]
    assert r["body"] == "Посмотрела, ок" and int(r["author_user_id"]) == GUEST and int(r["parent_id"]) == parent["id"]
    assert r["author_name"] == "Ульяна"
    # Вове — пуш с той же кнопкой, чтобы мог ответить дальше
    assert [p["to"] for p in tg] == [OWNER]
    assert tg[0]["markup"]["inline_keyboard"][0][0]["callback_data"] == f"dsc:reply:{r['id']}"
    assert "Ответ добавлен" in upd.message.replies[0]


def test_swipe_reply_on_push_uses_button(comments, tg):
    parent = comments.add_comment(OWNER, "local", "1", author_user_id=OWNER, author_name="Вова", body="Вопрос")
    replied = SimpleNamespace(
        from_user=SimpleNamespace(id=999, is_bot=True),
        reply_markup=SimpleNamespace(inline_keyboard=[[SimpleNamespace(callback_data=f"dsc:reply:{parent['id']}")]]),
        text="Вова в обсуждении План запуска:\nВопрос",
        entities=[],
    )
    upd = _update(GUEST, replied)
    ctx = _ctx(GUEST)
    assert task_reply.reply_target(upd, ctx) is None
    assert task_reply.discussion_target(upd, ctx) == (parent["id"], False)
    assert asyncio.run(task_reply.try_consume_reply(upd, ctx, "Ответ свайпом"))


def test_stranger_cannot_reply(comments, tg):
    parent = comments.add_comment(OWNER, "local", "1", author_user_id=OWNER, author_name="Вова", body="Вопрос")
    upd = _update(STRANGER)
    assert asyncio.run(task_reply.try_consume_reply(upd, _ctx(STRANGER, parent["id"]), "Влезу"))
    assert "недоступно" in upd.message.replies[0]
    assert len(comments.list_comments(OWNER, "local", "1")) == 1
