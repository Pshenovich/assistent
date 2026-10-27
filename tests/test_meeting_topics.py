import json
from datetime import datetime, timedelta, timezone

from assistant.services import meeting_brief as mb
from assistant.services import meeting_topics as svc
from assistant.stores import meeting_topics as store


def test_norm_and_keys():
    assert store.norm_title("Б-ГПТ") == store.norm_title("БГПТ") == store.norm_title("bgpt")
    assert "bgpt" in store.keys("Бустра ГПТ")  # «Бустра ГПТ» пишут как «БГПТ»
    assert store.keys("Штурм по БГПТ") == {"bgpt"}
    assert not store.keys("Obuchat Демо релизов") & store.keys("БГПТ")
    assert not store.keys("Синк по пилоту") & store.keys("Демо релизов")


def _chats_of(mapping):
    return lambda uid: {str(c) for c in mapping.get(int(uid), [])}


def test_shared_topic_visibility_and_scopes(monkeypatch):
    # 1 и 2 в чате -100, 3 — нет
    monkeypatch.setattr(store, "_user_chat_ids", _chats_of({1: [-100], 2: [-100], 3: []}))
    res = store.confirm(1, [{"kind": "title", "value": "Штурм по БГПТ"}, {"kind": "chat", "value": -100, "label": "Бустра ГПТ"}],
                        "БГПТ", by_name="Вова")
    tid = res["topic_id"]
    assert res["created"] and store.visible_topic_ids(1) == [tid] and store.visible_topic_ids(2) == [tid]
    assert store.visible_topic_ids(3) == []
    assert store.topic_for_title(2, "штурм по б-гпт")["id"] == tid  # второму связывать не нужно
    # «у меня» — только у себя
    store.set_item(2, tid, "title", "Штурм по БГПТ", on=False, scope="me", by_name="Андрей")
    assert store.topic_for_title(2, "Штурм по БГПТ") is None
    assert store.topic_for_title(1, "Штурм по БГПТ")["id"] == tid
    # «у всех»
    store.set_item(2, tid, "chat", -100, on=False, scope="all", by_name="Андрей")
    view = store.topic_view(1, tid)
    assert [i["active"] for i in view["items"] if i["kind"] == "chat"] == [False]
    assert ("chat", "-100") in store.decided(1)  # убрано у всех — больше не предлагаем
    hist = store.history(tid)
    assert hist[0]["by_name"] == "Андрей" and hist[0]["scope"] == "all" and hist[-1]["op"] == "link"


def test_confirm_adds_to_visible_topic_and_undo(monkeypatch):
    monkeypatch.setattr(store, "_user_chat_ids", _chats_of({11: [-7], 12: [-7]}))
    first = store.confirm(11, [{"kind": "title", "value": "БГПТ"}, {"kind": "chat", "value": -7}], "БГПТ")
    second = store.confirm(12, [{"kind": "chat", "value": -7}, {"kind": "title", "value": "Бустра, Игорь БГПТ"}])
    assert second["topic_id"] == first["topic_id"] and not second["created"]
    store.undo(12, second)
    view = store.topic_view(12, first["topic_id"])
    assert {i["value"] for i in view["items"]} == {"bgpt", "-7"}  # чат остался, новая встреча ушла
    store.undo(11, first)
    assert store.get_topic(first["topic_id"]) is None  # созданная и пустая тема удаляется


def test_reject_is_personal(monkeypatch):
    monkeypatch.setattr(store, "_user_chat_ids", _chats_of({}))
    store.reject(21, [{"kind": "chat", "value": -5}])
    assert ("chat", "-5") in store.decided(21) and ("chat", "-5") not in store.decided(22)
    store.undo(21, {}, [{"kind": "chat", "value": -5}])
    assert ("chat", "-5") not in store.decided(21)


def _meetings(*titles):
    base = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
    return [{"event_key": f"p:{i}", "title": t, "start": base - timedelta(days=i), "note_ids": [i]}
            for i, t in enumerate(titles, 1)]


def test_resolve_candidates(monkeypatch):
    uid = 502
    monkeypatch.setattr(store, "_user_chat_ids", _chats_of({502: [-1, -2, -3]}))
    now = datetime(2026, 10, 27, 8, tzinfo=timezone.utc)
    monkeypatch.setattr(svc, "past_meetings", lambda u, b, **k: _meetings("БГПТ", "Штурм по БГПТ", "Демо релизов"))
    monkeypatch.setattr(svc, "_chats", lambda u: [
        {"chat_id": -1, "title": "Бустра ГПТ", "last_message_at": "2026-10-26T10:00:00+00:00"},
        {"chat_id": -2, "title": "Бустра ГПТ архив", "last_message_at": "2026-01-01T10:00:00+00:00"},
        {"chat_id": -3, "title": "Флуд", "last_message_at": "2026-10-26T10:00:00+00:00"}])
    r = svc.resolve(uid, "Б-ГПТ", before=now)
    assert [m["title"] for m in r["meetings"]] == ["БГПТ"]  # тот же заголовок — без вопроса
    assert [c["label"] for c in r["candidates"]["titles"]] == ["Штурм по БГПТ"]
    assert [c["label"] for c in r["candidates"]["chats"]] == ["Бустра ГПТ"]  # неактивный чат не предлагаем
    assert r["chats"] == []
    r2 = svc.resolve(uid, "БГПТ", before=now, assume=True)
    assert {m["title"] for m in r2["meetings"]} == {"БГПТ", "Штурм по БГПТ"}
    assert [c["chat_id"] for c in r2["chats"]] == [-1]
    # отклонённое не предлагаем снова, подтверждённое — уже в теме
    store.reject(uid, [{"kind": "chat", "value": -1}])
    store.confirm(uid, [{"kind": "title", "value": "БГПТ"}, {"kind": "title", "value": "Штурм по БГПТ"}])
    r3 = svc.resolve(uid, "БГПТ", before=now)
    assert r3["candidates"] == {"titles": [], "chats": []}
    assert {m["title"] for m in r3["meetings"]} == {"БГПТ", "Штурм по БГПТ"}


def _meet(date, tasks=(), questions=()):
    return {"date": date, "title": "БГПТ", "note_id": 1, "topic": "", "decisions": [], "open_questions": list(questions),
            "tasks": [{"task": t, "assignee": w, "deadline": "", "completed": c} for t, w, c in tasks]}


def test_stuck_items():
    series = [  # новые первыми
        _meet("2026-10-26", [("Довести деперсонализацию BGPT до 100%", "Ваня", False), ("Выслать договор", "Гари", True)]),
        _meet("2026-10-19", [("Доработать деперсонализацию BGPT до 100%", "Ваня", False), ("Выслать договор", "Гари", False)],
              ["Какой процент данных скрывать по закону?"]),
        _meet("2026-10-12", [("Деперсонализация BGPT до 100%", "Ваня", False)], ["Какой процент данных скрывать по закону?"]),
    ]
    stuck = mb.stuck_items(series)
    texts = {s["text"]: s for s in stuck}
    assert texts["Довести деперсонализацию BGPT до 100%"]["times"] == 3
    assert texts["Довести деперсонализацию BGPT до 100%"]["first_seen"] == "2026-10-12"
    assert "Какой процент данных скрывать по закону?" in texts
    assert "Выслать договор" not in texts  # закрыто на последней встрече


def test_related_verified(monkeypatch):
    from assistant.nlu import llm
    from assistant.services import semantic_memory

    hits = [
        {"title": "Заметка BGPT прайс", "date": "2026-10-20", "kind": "note", "snippet": "Цена BGPT 5000", "score": 0.6, "ref": {}},
        {"title": "1:1 с Сашей", "date": "2026-10-20", "kind": "note", "snippet": "отпуск, личное", "score": 0.7, "ref": {}},
        {"title": "BGPT позже", "date": "2026-10-28", "kind": "note", "snippet": "BGPT", "score": 0.9, "ref": {}},
    ]
    monkeypatch.setattr(semantic_memory, "search", lambda uid, q, k=8: hits)
    seen = {}

    def fake_chat(system, user, **kw):
        seen["cands"] = json.loads(user)["candidates"]
        return json.dumps([{"id": 1, "fact": "Цена 5000"}, {"id": 99, "fact": "выдумка"}])

    monkeypatch.setattr(llm, "_chat", fake_chat)
    meeting = {"title": "БГПТ", "description": "", "attendees": ["Андрей"]}
    out = mb.related_items(1, meeting, [], [], datetime(2026, 10, 27, tzinfo=timezone.utc))
    assert [c["title"] for c in seen["cands"]] == ["Заметка BGPT прайс"]  # без слова темы и после встречи — не к LLM
    assert out == [{"source": "Заметка BGPT прайс", "date": "2026-10-20", "fact": "Цена 5000"}]  # id 99 отброшен


def test_chat_context(monkeypatch):
    from assistant.stores import chat_digest

    monkeypatch.setattr(chat_digest, "list_reports_for_chats", lambda ids, limit=20: [
        {"report_date": "2026-10-26", "summary": {"brief": "Договорились о демо"}},
        {"report_date": "2026-10-01", "summary": {"brief": "старое"}}])
    out = mb._chat_context([{"chat_id": -1, "title": "Бустра ГПТ"}], datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert out == [{"chat": "Бустра ГПТ", "since": "2026-10-20", "text": "2026-10-26: Договорились о демо"}]


def test_link_card(monkeypatch):
    uid = 503
    now = datetime(2026, 10, 27, 6, tzinfo=timezone.utc)
    monkeypatch.setattr(store, "_user_chat_ids", _chats_of({503: [-1, -2]}))
    monkeypatch.setattr(svc, "past_meetings", lambda u, b, **k: _meetings("Обсуждение задач по Б-ГПТ", "Штурм по БГПТ"))
    monkeypatch.setattr(svc, "_has_summary", lambda u, ids: ids == [1])
    monkeypatch.setattr(svc, "_chats", lambda u: [
        {"chat_id": -1, "title": "Бустра ГПТ", "last_message_at": "2026-10-26T10:00:00+00:00"},
        {"chat_id": -2, "title": "Бустра & Obuchat AI chat's", "last_message_at": "2026-10-26T10:00:00+00:00"}])
    monkeypatch.setattr(svc, "_tz", lambda u: timezone.utc)
    ev = {"id": "e", "summary": "Бустра, Игорь БГПТ", "start": "2026-10-27T08:30:00+00:00",
          "attendees": [{"email": "a@x", "self": True}, {"email": "b@x", "name": "Андрей"}]}
    card = svc.link_card(uid, [ev], now)
    items = {i["label"]: i["checked"] for i in card["topic"]["items"]}
    # без итогов встречу не предлагаем; «Бустра & Obuchat» — только по слову «Бустра», без галочки
    assert items == {"Обсуждение задач по Б-ГПТ": True, "Бустра ГПТ": True, "Бустра & Obuchat AI chat's": False}
    assert card["topic"]["anchor"] == {"kind": "title", "value": "Бустра, Игорь БГПТ", "label": "Бустра, Игорь БГПТ"}
    assert card["type"] == "topic_link" and card["urgency"] == 3 and "сегодня в 08:30" in card["source"]
    store.confirm(uid, [{"kind": "title", "value": "Обсуждение задач по Б-ГПТ"}, {"kind": "chat", "value": -1}])
    store.reject(uid, [{"kind": "chat", "value": -2}])
    assert svc.link_card(uid, [ev], now) is None  # всё решено — карточки нет
