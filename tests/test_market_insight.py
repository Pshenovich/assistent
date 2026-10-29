from assistant.services import market_insight as mi


def test_meeting_kind_internal_and_external():
    own = {"obuchat.me"}
    assert mi.meeting_kind([{"email": "me@obuchat.me", "self": True}, {"email": "anna@obuchat.me"}], own) == "внутренняя"
    kind = mi.meeting_kind([{"email": "me@obuchat.me", "self": True}, {"email": "ceo@boostra.ru"}, {"email": "x@gmail.com"}], own)
    assert kind == "с внешними участниками: boostra.ru"
    assert mi.meeting_kind([{"email": "room@resource.calendar.google.com", "resource": True}], own) == "внутренняя"


def test_prompt_forbids_client_meetings_without_direct_link():
    assert "Встречу с клиентом НЕ предлагай" in mi.SYSTEM and "не упоминай встречи вообще" in mi.SYSTEM


def test_org_profile_in_context_and_cache_key(monkeypatch):
    from assistant.services import market_settings
    from assistant.stores import orgs_store

    st = {"about": "речевая аналитика продаж", "competitors": ["Gong"], "topics": [], "sources": [], "time": "07:20"}
    monkeypatch.setattr(orgs_store, "current_org", lambda uid: {"id": 5, "name": "Обучат"})
    monkeypatch.setattr(market_settings, "get", lambda org_id: dict(st))
    lines = mi._org_lines(1)
    assert lines == ["компания: Обучат — речевая аналитика продаж", "конкуренты: Gong"]
    k1 = mi._key(1, "2026-10-29")
    st["competitors"] = ["Gong", "Clari"]
    assert mi._key(1, "2026-10-29") != k1  # поменяли конкурентов — разбор пересоберётся
    assert mi._key(1, "2026-10-29").startswith("2026-10-29:" + mi.CACHE_VERSION + ":")
    monkeypatch.setattr(orgs_store, "current_org", lambda uid: None)
    assert mi._key(1, "2026-10-29") == "2026-10-29:" + mi.CACHE_VERSION
