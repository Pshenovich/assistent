"""Тесты NLU-маршрута zoom_record."""

from assistant.lib.calendar_intent_heuristics import calendar_detect_route_kind
from assistant.nlu.regex import parse_zoom_record_route, regex_route


def test_zoom_record_with_keyword() -> None:
    route = parse_zoom_record_route("запиши встречу https://zoom.us/j/12345678901")
    assert route is not None
    assert route.skill == "zoom_record"


def test_zoom_record_url_only() -> None:
    route = parse_zoom_record_route("https://zoom.us/j/12345678901")
    assert route is not None
    assert route.skill == "zoom_record"


def test_zoom_record_typo_with_url() -> None:
    route = parse_zoom_record_route(
        "запипши встречу https://us04web.zoom.us/j/77147473463?pwd=abc.1"
    )
    assert route is not None
    assert route.skill == "zoom_record"


def test_zoom_record_ignores_without_zoom_url() -> None:
    assert parse_zoom_record_route("запиши встречу завтра") is None


def test_zoom_url_not_calendar_create() -> None:
    text = "запипши встречу https://us04web.zoom.us/j/77147473463?pwd=abc.1"
    assert calendar_detect_route_kind(text) is None
    route = regex_route(text)
    assert route is not None
    assert route.skill == "zoom_record"


def test_calendar_update_with_zoom_url_still_calendar() -> None:
    text = "перенеси встречу https://zoom.us/j/12345678901 на 15:00"
    assert parse_zoom_record_route(text) is None
    assert calendar_detect_route_kind(text) == "update"
