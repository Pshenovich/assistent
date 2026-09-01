"""Тесты форматирования транскрипта Vexa."""

from assistant.lib.vexa_transcript import (
    formatted_transcript_from_vexa,
    participant_names_from_segments,
)


def test_formatted_transcript_with_speakers() -> None:
    body = {
        "segments": [
            {"speaker": "Alice", "text": "Привет всем"},
            {"speaker": "Bob", "text": "Добрый день"},
            {"speaker": "Alice", "text": "Начнём"},
        ]
    }
    parsed = formatted_transcript_from_vexa(body)
    assert parsed is not None
    plain, formatted, names = parsed
    assert "Alice: Привет всем" in plain
    assert "Bob: Добрый день" in plain
    assert "Alice:\nПривет всем" in formatted
    assert names == ["Alice", "Bob"]


def test_formatted_transcript_empty() -> None:
    assert formatted_transcript_from_vexa({}) is None
    assert formatted_transcript_from_vexa({"segments": []}) is None


def test_participant_names_deduped() -> None:
    segs = [
        {"speaker": "A", "text": "x"},
        {"speaker": "B", "text": "y"},
        {"speaker": "A", "text": "z"},
    ]
    assert participant_names_from_segments(segs) == ["A", "B"]
