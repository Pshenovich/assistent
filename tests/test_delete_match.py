from assistant.lib.calendar_datetime_parse import (
    calendar_extract_match_query,
    calendar_normalize_parsed,
)
from assistant.services.calendar import _match_summary


def test_extract_match_query_delete():
    q = calendar_extract_match_query("удали встречу сегодня Бустра Ложечкин")
    assert "бустра" in q.lower()
    assert "ложечкин" in q.lower()


def test_normalize_delete_sets_date_and_query():
    parsed = {"intent": "delete_event", "match_query": "", "match_date": ""}
    calendar_normalize_parsed(
        parsed,
        "удали встречу сегодня Бустра Ложечкин",
        today_iso="2026-06-03",
        tz_name="Europe/Moscow",
    )
    assert parsed.get("match_date") == "2026-06-03"
    assert "бустра" in str(parsed.get("match_query") or "").lower()


def test_match_summary_partial():
    assert _match_summary("Бустра Ложечкин", "Бустра")
    assert _match_summary("Бустра Ложечкин", "Бустра Ложечкин")
