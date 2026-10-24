from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from assistant.services import chat_alerts as ca

ME = 100
FORUM = -1003
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from assistant.stores import chat_digest

    monkeypatch.setenv("CHAT_DIGEST_DB_PATH", str(tmp_path / "digest.sqlite"))
    chat_digest._CONN = None
    ca._SCHEMA_READY = False
    monkeypatch.setattr(ca, "_now", lambda: NOW)
    yield chat_digest
    chat_digest._CONN = None
    ca._SCHEMA_READY = False


def _msg(store, mid, uid, text, minutes_ago, thread=None):
    store.insert_message(chat_id=FORUM, message_id=mid, user_id=uid, username=None, display_name=f"U{uid}",
                         text=text, ts_utc=NOW - timedelta(minutes=minutes_ago), thread_id=thread)


def test_group_by_topic_keeps_every_topic(store):
    store.upsert_topic(FORUM, 10, "Поддержка")
    msgs = [
        {"message_id": 1, "text": "a" * 50, "thread_id": None},
        {"message_id": 2, "text": "b" * 50, "thread_id": 10},
        {"message_id": 3, "text": "c" * 50, "thread_id": 11},
        {"message_id": 4, "text": "d" * 50, "thread_id": 10},
    ]
    chunks = store.group_by_topic(msgs, store.topic_names(FORUM), lambda m: f"#{m['message_id']} {m['text']}", budget=150)
    text = "\n".join(chunks)
    assert "### Топик «General»" in text and "### Топик «Поддержка»" in text and "### Топик «Топик #11»" in text
    assert all(len(c) <= 150 for c in chunks)
    # сообщения одного топика — вместе, даже если шли вперемешку
    sup = next(c for c in chunks if "Поддержка" in c)
    assert "#2 " in sup and "#4 " in sup


def test_group_by_topic_long_topic_keeps_tail(store):
    msgs = [{"message_id": i, "text": f"m{i}", "thread_id": 5} for i in range(200)]
    chunks = store.group_by_topic(msgs, {}, lambda m: f"#{m['message_id']} {m['text']}", budget=300)
    assert len(chunks) == 1 and "#199 " in chunks[0] and "#0 " not in chunks[0]


def test_ingest_forum_topic(store, monkeypatch):
    from assistant.bot import chat_ingest

    root = NS(forum_topic_created=NS(name="Инциденты"), forum_topic_edited=None, message_id=10, from_user=NS(id=ME))
    msg = NS(text="Всё упало", caption=None, entities=None, caption_entities=None, message_id=11,
             from_user=NS(id=201, is_bot=False, username=None, first_name="Петя", last_name=None),
             date=NOW, reply_to_message=root, is_topic_message=True, message_thread_id=10,
             forum_topic_created=None, forum_topic_edited=None)
    upd = NS(message=msg, edited_message=None, effective_chat=NS(id=FORUM, type="supergroup", title="Obuchat AI"))
    import asyncio

    asyncio.run(chat_ingest.ingest_message_update(upd, None))
    row = store.get_message(FORUM, 11)
    assert row["thread_id"] == 10
    # корень топика — не реплай пользователю (иначе «ждут вашего ответа» на каждое сообщение топика)
    assert row["reply_to_user_id"] is None
    assert store.topic_names(FORUM) == {10: "Инциденты"}


def test_verify_gates(store):
    _msg(store, 1, 201, "Оплата не проходит у всех", 10, thread=10)
    _msg(store, 2, 202, "Подтверждаю, касса лежит", 8, thread=10)
    _msg(store, 3, 203, "Сайт недоступен", 7, thread=11)
    _msg(store, 4, ME, "Смотрю", 5, thread=11)
    _msg(store, 5, 204, "Обновили баннер", 4, thread=12)
    new = store.messages_after_rowid(FORUM, 0)
    proposed = [
        {"msg": 1, "severity": "critical", "title": "Не проходят оплаты", "why": ""},
        {"msg": 2, "severity": "critical", "title": "Касса лежит", "why": ""},        # тот же топик — один пуш
        {"msg": 3, "severity": "critical", "title": "Сайт недоступен", "why": ""},    # я уже ответил в топике
        {"msg": 4, "severity": "critical", "title": "Моё", "why": ""},                # моё сообщение
        {"msg": 5, "severity": "high", "title": "Баннер", "why": ""},                 # не critical
        {"msg": 99, "severity": "critical", "title": "Выдумка", "why": ""},           # нет такого
    ]
    got = ca.verify(ME, FORUM, new, proposed, now=NOW)
    assert [(a["message_id"], a["thread_id"]) for a in got] == [(1, 10)]

    alert_id = ca.record_alert(ME, got[0], now=NOW)
    assert ca.verify(ME, FORUM, new, proposed, now=NOW + timedelta(hours=1)) == []  # кулдаун по топику
    assert ca.verify(ME, FORUM, new, proposed, now=NOW + timedelta(hours=4))        # кулдаун прошёл
    assert ca.mute_alert(999, alert_id) is None                                      # чужой
    assert ca.mute_alert(ME, alert_id, now=NOW + timedelta(hours=4))
    assert ca.verify(ME, FORUM, new, proposed, now=NOW + timedelta(hours=5)) == []  # «Не критично» → сутки тихо


def test_scan_user_cursor(store, monkeypatch):
    from assistant.stores import user_prefs

    monkeypatch.setattr(user_prefs, "digest_chat_ids", lambda uid: [FORUM])
    _msg(store, 1, 201, "старое", 60 * 5)      # до окна LOOKBACK_H — не смотрим
    _msg(store, 2, 201, "Всё упало", 30, thread=10)
    seen = []

    def fake_detect(uid, cid, new):
        seen.append([m["message_id"] for m in new])
        return []

    monkeypatch.setattr(ca, "detect", fake_detect)
    ca.scan_user(ME)
    ca.scan_user(ME)  # нового нет — модель не зовём
    _msg(store, 3, 202, "И оплата", 1, thread=10)
    ca.scan_user(ME)
    assert seen == [[2], [3]]


def test_parse_alerts_and_url():
    raw = '```json\n{"alerts":[{"msg":"7","severity":"Critical","title":" Касса  лежит ","why":"x"},{"msg":0,"title":"нет"}]}\n```'
    assert ca.parse_alerts(raw) == [{"msg": 7, "severity": "critical", "title": "Касса лежит", "why": "x"}]
    assert ca.parse_alerts("не json") == []
    assert ca.message_url(-1001234, 10, 55) == "https://t.me/c/1234/10/55"
    assert ca.message_url(-1001234, None, 55) == "https://t.me/c/1234/55"


def test_merge_summaries():
    from assistant.services.chat_digest import merge_summaries

    a = {"brief": "Топик А.", "decisions": [{"text": "1"}], "next_steps": [], "deadlines": [], "open_questions": [], "context_topics": ["x"]}
    b = {"brief": "Топик Б.", "decisions": [{"text": "2"}], "next_steps": [], "deadlines": [], "open_questions": [], "context_topics": ["x", "y"]}
    m = merge_summaries([a, b])
    assert m["brief"] == "Топик А. Топик Б." and len(m["decisions"]) == 2 and m["context_topics"] == ["x", "y"]
