from assistant.services import market_insight as mi


def test_meeting_kind_internal_and_external():
    own = {"obuchat.me"}
    assert mi.meeting_kind([{"email": "me@obuchat.me", "self": True}, {"email": "anna@obuchat.me"}], own) == "внутренняя"
    kind = mi.meeting_kind([{"email": "me@obuchat.me", "self": True}, {"email": "ceo@boostra.ru"}, {"email": "x@gmail.com"}], own)
    assert kind == "с внешними участниками: boostra.ru"
    assert mi.meeting_kind([{"email": "room@resource.calendar.google.com", "resource": True}], own) == "внутренняя"


def test_prompt_forbids_client_meetings_without_direct_link():
    assert "Встречу с клиентом НЕ предлагай" in mi.SYSTEM and "не упоминай встречи вообще" in mi.SYSTEM
