from assistant.config import DEFAULT_EVENT_TITLE
from assistant.services.calendar import normalize_event_title


def test_empty_title():
    assert normalize_event_title({}) == DEFAULT_EVENT_TITLE


def test_placeholder_title():
    assert normalize_event_title({"title": "встреча"}) == DEFAULT_EVENT_TITLE


def test_real_title():
    assert normalize_event_title({"title": "Синк с командой"}) == "Синк с командой"
