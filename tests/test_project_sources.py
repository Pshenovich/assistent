"""Что добавить в проект: встречи и серии, темы, внешние чаты и топики, почта и адреса; ссылка на проект."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assistant.services import project_sources

NOW = datetime.now(timezone.utc)


def _g(event_id, series_id, title, days):
    start = NOW + timedelta(days=days)
    return {"calendar_id": "primary", "event_id": event_id, "series_id": series_id, "title": title,
            "start": start, "end": start + timedelta(hours=1), "all_day": 0}


def test_meetings_group_series_and_keep_singles(monkeypatch):
    rows = [
        _g("rec1_a", "rec1", "Синк с БГПТ", -14),
        _g("rec1_b", "rec1", "Синк с БГПТ", -7),
        _g("rec1_c", "rec1", "Синк с БГПТ", 3),
        _g("one", "", "Демо для продаж", -2),
        _g("allday", "", "Отпуск", 1) | {"all_day": 1},
    ]
    monkeypatch.setattr("assistant.stores.calendar_mirror.events", lambda uid, s, e: rows)
    monkeypatch.setattr(project_sources, "_orbita_rows", lambda uid, s, e: [
        {"calendar_id": "orbita", "event_id": "orb-5_x", "series_id": "orb-5", "title": "Планёрка", "start": NOW + timedelta(days=1)},
    ])
    monkeypatch.setattr("assistant.stores.meeting_topics.topics_for_user", lambda uid: [{"id": 9, "name": "БГПТ"}])
    out = project_sources.meetings(1)
    kinds = {(x["kind"], x["id"]) for x in out}
    assert ("series", "primary:rec1") in kinds
    assert ("series", "orbita:orb-5") in kinds
    assert ("event", "primary:one") in kinds
    assert ("topic", "9") in kinds
    assert not any(x["id"].endswith("allday") for x in out)  # весь день — не встреча
    s = next(x for x in out if x["id"] == "primary:rec1")
    assert "прошло 2" in s["subtitle"] and "следующая" in s["subtitle"]
    # поиск
    found = project_sources.meetings(1, "демо")
    assert [x["id"] for x in found] == ["primary:one"]


def test_series_occurrences(monkeypatch):
    rows = [_g("rec1_a", "rec1", "Синк", -14), _g("rec1_b", "rec1", "Синк", 3), _g("other", "rec2", "Другое", 1)]
    monkeypatch.setattr("assistant.stores.calendar_mirror.events", lambda uid, s, e: rows)
    monkeypatch.setattr(project_sources, "_orbita_rows", lambda uid, s, e: [])
    occ = project_sources.series_occurrences(1, "primary:rec1")
    assert [o["id"] for o in occ] == ["primary:rec1_b", "primary:rec1_a"]
    assert occ[0]["past"] is False and occ[1]["past"] is True


def test_tgchats_with_topics(monkeypatch):
    monkeypatch.setattr("assistant.stores.chat_digest.list_chats_for_user", lambda uid: [
        {"chat_id": -100, "title": "БГПТ × Орбита", "chat_type": "supergroup"},
        {"chat_id": 5, "title": "Личка", "chat_type": "private"},
    ])
    monkeypatch.setattr("assistant.stores.chat_digest.topic_names", lambda cid: {7: "Доступы", 8: "Счета"})
    all_ = project_sources.tgchats(1)
    assert [x["id"] for x in all_] == ["-100"]  # без поиска — только чаты, личные — нет
    hits = project_sources.tgchats(1, "доступ")
    assert [x["id"] for x in hits] == ["-100:7"]


def test_mail_threads_and_address(monkeypatch):
    monkeypatch.setattr("assistant.stores.mail_index.search", lambda uid, q, limit=25: [
        {"thread_key": "t1", "subject": "Счёт на пилот", "from_name": "Ира", "date_utc": NOW.isoformat()},
        {"thread_key": "t1", "subject": "Re: Счёт на пилот", "from_name": "Ира", "date_utc": NOW.isoformat()},
        {"thread_key": "t2", "subject": "Рассылка", "bulk": True},
    ])
    monkeypatch.setattr("assistant.stores.mail_index.correspondents", lambda uid: {"ira@bgpt.ru", "boss@x.ru"})
    out = project_sources.mail(1, "ira@bgpt.ru")
    assert out[0] == {"kind": "mailaddr", "id": "ira@bgpt.ru", "title": "ira@bgpt.ru",
                      "subtitle": "адрес — Лео будет предлагать новые письма от него"}
    assert [x["id"] for x in out if x["kind"] == "mail"] == ["t1"]  # одна переписка, рассылки нет


def test_project_webapp_url(monkeypatch):
    from assistant.stores import projects

    monkeypatch.setattr("assistant.lib.webapp_public.webapp_entry_url", lambda: "https://getorbita.me/webapp/")
    assert projects.webapp_url(12) == "https://getorbita.me/webapp/?project=12"
