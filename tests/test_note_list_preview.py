"""Превью тела заметки для list /notes (без base64 и с усечением)."""

from usage_server import minify_note_for_list, note_body_list_preview


def test_note_body_list_preview_strips_data_uri_and_truncates():
    huge = "hello <img src=\"data:image/png;base64," + ("A" * 5000) + "\"> world"
    preview, truncated = note_body_list_preview(huge, max_len=80)
    assert truncated is True
    assert "base64" not in preview.lower()
    assert "[image]" in preview
    assert len(preview) <= 80


def test_note_body_list_preview_keeps_short_text():
    preview, truncated = note_body_list_preview("короткий текст")
    assert truncated is False
    assert preview == "короткий текст"


def test_minify_note_for_list_sets_flag():
    blob = "x" * 2000
    item = minify_note_for_list(
        {"id": 1, "title": "t", "body": blob, "description": blob}
    )
    assert item["body_preview_only"] is True
    assert len(item["body"]) < 700
    assert len(item["description"]) < 700
    assert item["title"] == "t"
