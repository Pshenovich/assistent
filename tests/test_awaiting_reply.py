from datetime import datetime, timedelta, timezone

import pytest

from assistant.services import awaiting_reply as ar

ME = 100
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)  # 19:00 по Москве
CID, GROUP = -1001, -1002


@pytest.fixture
def env(tmp_path, monkeypatch):
    from assistant.services import calendar as cal
    from assistant.services import focus_items, proactive_ai
    from assistant.stores import chat_digest, note_members
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("CHAT_DIGEST_DB_PATH", str(tmp_path / "digest.sqlite"))
    for mod in (notes_store, focus_items, proactive_ai, chat_digest):
        mod._CONN = None
    monkeypatch.setattr(note_members, "get_profile", lambda uid: {"username": "pshenovich", "first_name": "Вова"})
    monkeypatch.setattr(cal, "_tz_for", lambda uid: timezone(timedelta(hours=3)))
    monkeypatch.setattr(focus_items, "sync_source", lambda *a, **k: None)

    def msg(cid, mid, uid, text, minutes_ago, **kw):
        if uid != 999:  # 999 — бот: в участниках его нет
            chat_digest.upsert_member(cid, uid, username=None)
        chat_digest.insert_message(chat_id=cid, message_id=mid, user_id=uid, username=None, display_name=f"U{uid}",
                                   text=text, ts_utc=NOW - timedelta(minutes=minutes_ago), **kw)

    # группа: 3 участника
    msg(GROUP, 1, ME, "Коллеги, кто смотрел отчёт?", 300)
    msg(GROUP, 2, 201, "Вова, пришлёшь договор?", 240)                        # по имени, без ответа → ждут
    msg(GROUP, 3, 202, "Оксана, когда будет макет?", 230)                     # не мне
    msg(GROUP, 4, 201, "А это кто будет делать?", 220, reply_to_message_id=1, reply_to_user_id=ME)  # реплай мне
    msg(GROUP, 5, 202, "Я сделаю", 200, reply_to_message_id=4, reply_to_user_id=201)                 # ответили реплаем
    msg(GROUP, 6, 201, "@pshenovich глянь доступы", 60, mentions=["@pshenovich"])                   # свежий, < 2 ч
    msg(GROUP, 7, 203, "Кто-нибудь знает пароль от вайфая?", 200)
    msg(GROUP, 8, 999, "Вова, ошибка у клиента — проверьте", 200)              # бот-уведомления
    # 1:1
    msg(CID, 1, 301, "Подскажи, когда созвон?", 300)
    msg(CID, 2, 301, "И ещё: где презентация?", 280)
    items = lambda *its: [{"kind": k, "text": t, "msg": m, "who": ""} for k, t, m in its]
    proactive_ai.save(ME, f"digest:{GROUP}:2026-10-22", "h", "Пилот", items(
        ("request", "Прислать договор", 2), ("request", "Когда будет макет", 3), ("request", "Кто будет делать", 4),
        ("request", "Глянуть доступы", 6), ("request", "Пароль от вайфая", 7), ("request", "Без номера", 0),
        ("request", "Ошибка у клиента", 8),
        ("promise", "Пришлю отчёт", 1)))
    proactive_ai.save(ME, f"digest:{CID}:2026-10-22", "h", "Саша", items(("request", "Когда созвон", 1),
                                                                          ("request", "Где презентация", 2)))
    yield
    for mod in (notes_store, focus_items, proactive_ai, chat_digest):
        mod._CONN = None


def test_pending_filters(env):
    got = [(q["chat_id"], q["msg"]) for q in ar.pending_requests(ME, now=NOW)]
    assert got == [(CID, 1), (CID, 2), (GROUP, 2)]


def test_answered_by_me_later(env):
    from assistant.stores import chat_digest

    chat_digest.insert_message(chat_id=CID, message_id=3, user_id=ME, username=None, display_name="Я",
                               text="В 15:00", ts_utc=NOW - timedelta(minutes=10))
    assert [(q["chat_id"], q["msg"]) for q in ar.pending_requests(ME, now=NOW)] == [(GROUP, 2)]


def test_card_only_after_hour_and_not_me_mutes_chat(env):
    from assistant.services import focus_items

    assert ar.awaiting_card(ME, now=NOW.replace(hour=10)) is None  # 13:00 по Москве — рано
    card = ar.awaiting_card(ME, now=NOW)
    assert card["title"] == "Ждут вашего ответа: 3 вопроса" and card["key"] == "awaiting:2026-10-22"
    assert card["items"][0]["url"]
    # «Не мне» на двух вопросах из 1:1 → чат отключён
    for it in focus_items.item_from_card(card)[:2]:
        focus_items.remember(ME, it)
        focus_items.apply(ME, it["key"], "dismiss", reason="not_me")
    assert ar.muted_chats(ME) == {CID}
    assert [q["chat_id"] for q in ar.pending_requests(ME, now=NOW)] == [GROUP]
    # «Ответил» — вопрос пропадает
    k = focus_items.item_from_card(ar.awaiting_card(ME, now=NOW))[0]
    focus_items.remember(ME, k)
    focus_items.apply(ME, k["key"], "done")
    assert ar.awaiting_card(ME, now=NOW) is None


def test_requests_not_in_registry(env):
    from assistant.services import focus_items

    card = ar.awaiting_card(ME, now=NOW)
    focus_items.ingest(ME, [card], NOW)
    assert focus_items.commitments(ME, now=NOW) == []


def test_message_mentions():
    from types import SimpleNamespace as NS

    from assistant.bot.chat_ingest import message_mentions

    msg = NS(text="@Pshenovich и Саша, гляньте", caption=None, caption_entities=None, entities=[
        NS(type="mention", offset=0, length=11),
        NS(type="text_mention", offset=14, length=4, user=NS(id=555)),
    ])
    assert message_mentions(msg) == ["@pshenovich", "id:555"]
