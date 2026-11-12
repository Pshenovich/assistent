"""«Ответить» под уведомлением о сообщении по задаче: текст / голос из Telegram — в обсуждение задачи."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from assistant.bot import task_reply
from tests.test_posted_tasks import ASSIGNEE, OWNER, _delegated, sent, store  # noqa: F401


@pytest.fixture()
def pushes(monkeypatch):
    from assistant.lib import telegram_notify

    out: list[dict] = []

    def _send(chat_id, text, **kw):
        out.append({"to": int(chat_id), "text": text, "markup": kw.get("reply_markup")})
        return True

    monkeypatch.setattr(telegram_notify, "send_message", _send)
    monkeypatch.setattr("assistant.lib.task_notify.owner_display_name", lambda uid: {OWNER: "Вова", ASSIGNEE: "Ульяна"}.get(int(uid), "Коллега"))
    return out


class _Msg:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text, **_):
        self.replies.append(text)


def _update(uid: int, chat_type: str = "private"):
    return SimpleNamespace(
        message=_Msg(),
        effective_user=SimpleNamespace(id=uid, full_name="TG Name"),
        effective_chat=SimpleNamespace(type=chat_type, id=uid),
    )


def test_owner_message_push_has_reply_button(store, pushes):
    from assistant.lib.task_notify import notify_task_message

    t = _delegated(store)
    notify_task_message(task=t, sender_id=OWNER, sender_name="Вова", text="Как дела?")
    assert len(pushes) == 1 and pushes[0]["to"] == ASSIGNEE
    btn = pushes[0]["markup"]["inline_keyboard"][0][0]
    assert btn["text"].endswith("Ответить") and btn["callback_data"] == f"tsk:reply:{t['id']}"


def test_pending_reply_goes_to_task_discussion(store, pushes):
    t = _delegated(store)
    ctx = SimpleNamespace(user_data={"leo_task_reply": {"user_id": ASSIGNEE, "task_id": t["id"], "at": time.time()}})
    upd = _update(ASSIGNEE)
    assert asyncio.run(task_reply.try_consume_reply(upd, ctx, "Сделаю к пятнице"))
    assert "leo_task_reply" not in ctx.user_data  # один ответ — одно сообщение
    msgs = [e for e in store.list_events(store.get_task_for_user(OWNER, t["id"])) if e["kind"] == "message"]
    assert msgs[-1]["text"] == "Сделаю к пятнице" and msgs[-1]["actor_name"] == "Ульяна"
    assert pushes[-1]["to"] == OWNER and "Сделаю к пятнице" in pushes[-1]["text"]
    assert "Ответ добавлен" in upd.message.replies[-1]
    # следующее сообщение — уже не ответ
    assert not asyncio.run(task_reply.try_consume_reply(upd, ctx, "Привет"))


def test_pending_expires_and_ignored_in_groups(store, pushes):
    t = _delegated(store)
    old = {"user_id": ASSIGNEE, "task_id": t["id"], "at": time.time() - 3600}
    ctx = SimpleNamespace(user_data={"leo_task_reply": dict(old)})
    assert not asyncio.run(task_reply.try_consume_reply(_update(ASSIGNEE), ctx, "поздно"))
    ctx = SimpleNamespace(user_data={"leo_task_reply": {**old, "at": time.time()}})
    assert not asyncio.run(task_reply.try_consume_reply(_update(ASSIGNEE, "group"), ctx, "в группе"))
    assert not asyncio.run(task_reply.try_consume_reply(_update(OWNER), ctx, "чужой"))
    assert task_reply.clear_pending_for_user(ctx, ASSIGNEE)


def test_reply_to_unavailable_task(store, pushes):
    t = _delegated(store)
    ctx = SimpleNamespace(user_data={"leo_task_reply": {"user_id": 33, "task_id": t["id"], "at": time.time()}})
    upd = _update(33)
    assert asyncio.run(task_reply.try_consume_reply(upd, ctx, "Я тут"))
    assert "недоступна" in upd.message.replies[-1]


def _replied(task_id, *, with_button=True, text=None, from_bot=True):
    btn = SimpleNamespace(callback_data=f"tsk:reply:{task_id}")
    return SimpleNamespace(
        from_user=SimpleNamespace(id=999, is_bot=from_bot),
        reply_markup=SimpleNamespace(inline_keyboard=[[btn]]) if with_button else None,
        text=text if text is not None else "Вова по задаче Отчёт:\nКак дела?",
        entities=[SimpleNamespace(url=f"https://x/webapp/?task={task_id}")],
    )


def test_swipe_reply_to_notification_goes_to_task(store, pushes):
    t = _delegated(store)
    ctx = SimpleNamespace(user_data={})
    upd = _update(ASSIGNEE)
    upd.message.reply_to_message = _replied(t["id"])
    assert task_reply.reply_target(upd, ctx) == (t["id"], False)
    assert asyncio.run(task_reply.try_consume_reply(upd, ctx, "Ок, беру"))
    msgs = [e for e in store.list_events(store.get_task_for_user(OWNER, t["id"])) if e["kind"] == "message"]
    assert msgs[-1]["text"] == "Ок, беру"
    # кнопки нет (старое уведомление) — берём id из ссылки, но только у сообщения «по задаче»
    upd.message.reply_to_message = _replied(t["id"], with_button=False)
    assert task_reply.reply_target(upd, ctx) == (t["id"], False)
    upd.message.reply_to_message = _replied(t["id"], with_button=False, text="Вова поставил(а) вам задачу: Отчёт.")
    assert task_reply.reply_target(upd, ctx) is None
    upd.message.reply_to_message = _replied(t["id"], from_bot=False)  # ответ на своё сообщение
    assert task_reply.reply_target(upd, ctx) is None


def test_task_event_publishes_realtime_to_both_sides(store, monkeypatch):
    from assistant.realtime import notify as rn

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(rn, "publish", lambda topic, ev: seen.append((topic, ev)))
    t = _delegated(store)
    seen.clear()
    store.post_message(ASSIGNEE, t["id"], "Вопрос", actor_name="Ульяна")
    assert sorted(topic for topic, _ in seen) == [f"user:{OWNER}", f"user:{ASSIGNEE}"]
    assert seen[0][1] == {"type": "task.changed", "task_id": t["id"], "actor": str(ASSIGNEE)}


def test_messages_allowed_in_closed_task_without_reopening(store, pushes):
    """Задачу приняли — исполнитель всё равно может написать; статус «вопрос» не появляется."""
    t = _delegated(store)
    store.accept_task(OWNER, t["id"])
    row = store.post_message(ASSIGNEE, t["id"], "Спасибо! Ещё вопрос по результату")
    assert row and row["done"] and row["outcome"] == "accepted"
    assert row.get("assignee_status") != "question"
    msgs = [e for e in store.list_events(row) if e.get("kind") == "message"]
    assert msgs and msgs[-1]["text"].startswith("Спасибо")


def test_done_push_has_accept_and_reply_buttons(store, pushes):
    from assistant.lib.task_notify import notify_task_owner_done

    t = _delegated(store)
    notify_task_owner_done(task=t, completer_name="Ульяна")
    assert pushes and pushes[0]["to"] == OWNER
    row = pushes[0]["markup"]["inline_keyboard"][0]
    assert row[0]["text"].endswith("Принять задачу") and row[0]["callback_data"] == f"tsk:accept:{t['id']}"
    assert row[1]["callback_data"] == f"tsk:reply:{t['id']}"


def test_accept_from_telegram(store, pushes, monkeypatch):
    monkeypatch.setattr("assistant.services.task_google_sync.sync_task_google", lambda uid, task, action: None)
    t = _delegated(store)
    assert task_reply.accept_from_telegram(ASSIGNEE, t["id"])[0] == "forbidden"  # исполнитель сам себе не принимает
    assert task_reply.accept_from_telegram(77, t["id"])[0] == "forbidden"
    status, row = task_reply.accept_from_telegram(OWNER, t["id"])
    assert status == "ok" and row["outcome"] == "accepted" and row["done"]
    assert task_reply.accept_from_telegram(OWNER, t["id"])[0] == "already"


def test_accept_callback_edits_message(store, pushes, monkeypatch):
    monkeypatch.setattr("assistant.services.task_google_sync.sync_task_google", lambda uid, task, action: None)
    t = _delegated(store)
    answers, edits = [], []

    class _Q:
        data = f"tsk:accept:{t['id']}"

        async def answer(self, text=None, **_):
            answers.append(text)

    class _M:
        text_html = "Ульяна выполнил(а) задачу"

        async def edit_text(self, text, **_):
            edits.append(text)

    q = _Q()
    q.message = _M()
    upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=OWNER, full_name="Вова"))
    asyncio.run(task_reply.handle_reply_callback(upd, SimpleNamespace(user_data={})))
    assert answers == ["Задача принята"] and edits and edits[0].endswith("✅ Принято")
    assert store.get_task_for_user(OWNER, t["id"])["outcome"] == "accepted"
