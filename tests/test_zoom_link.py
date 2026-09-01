"""Тесты парсинга Zoom-ссылок."""

from assistant.lib.zoom_link import find_zoom_url, parse_zoom_url


def test_parse_zoom_url_with_passcode() -> None:
    link = parse_zoom_url("https://us05web.zoom.us/j/12345678901?pwd=abcDEF12")
    assert link is not None
    assert link.native_meeting_id == "12345678901"
    assert link.passcode == "abcDEF12"


def test_parse_zoom_url_without_passcode() -> None:
    link = parse_zoom_url("https://zoom.us/j/9876543210")
    assert link is not None
    assert link.native_meeting_id == "9876543210"
    assert link.passcode is None


def test_find_zoom_url_skips_non_zoom() -> None:
    assert find_zoom_url(["https://meet.google.com/abc-defg-hij"]) is None


def test_parse_zoom_url_strips_trailing_punctuation() -> None:
    link = parse_zoom_url("https://zoom.us/j/111222333).")
    assert link is not None
    assert link.native_meeting_id == "111222333"
