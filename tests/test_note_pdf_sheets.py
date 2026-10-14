"""PDF заметки: все текстовые листы по порядку, канвасы пропускаются."""

from __future__ import annotations

import itertools
import json

from assistant.lib import journal_pdf
from assistant.stores import note_sheets
from assistant.stores import notes as notes_store

_ids = itertools.count(970_000, 10)


def _capture(monkeypatch):
    seen = {}

    def fake(row):
        seen["row"] = row
        return b"%PDF", "x.pdf", "cap"

    monkeypatch.setattr(journal_pdf, "build_journal_pdf", fake)
    return seen


def test_transcription_pdf_has_all_sheets(monkeypatch):
    uid = next(_ids)
    note = notes_store.create_note(uid, "Созвон", "<p>Итоги встречи: решили запускать пилот</p>")
    note["role"] = notes_store.TRANSCRIPTION_ROLE
    note["primary_sheet_title"] = notes_store.SUMMARY_SHEET_TITLE
    note_sheets.create_sheet(note, title=notes_store.TRANSCRIPT_SHEET_TITLE, body="speaker_1: привет\nspeaker_2: здравствуйте")
    note_sheets.create_sheet(note, title="Задачи", body="<p>Позвонить клиенту</p>")
    note_sheets.create_sheet(note, title="Слайды", body="<html>deck</html>", kind="web")
    seen = _capture(monkeypatch)
    journal_pdf.build_note_pdf(note)
    text = json.loads(seen["row"]["raw_usage_json"])["text"]
    assert "Итоги встречи" in text and "speaker_1: привет" in text and "Позвонить клиенту" in text
    assert "deck" not in text
    assert text.index(notes_store.SUMMARY_SHEET_TITLE) < text.index(notes_store.TRANSCRIPT_SHEET_TITLE) < text.index("Задачи")
    assert seen["row"]["label"] == "Транскрипция"


def test_single_sheet_note_unchanged(monkeypatch):
    note = notes_store.create_note(next(_ids), "Просто", "<p>Один лист</p>")
    seen = _capture(monkeypatch)
    journal_pdf.build_note_pdf(note)
    assert json.loads(seen["row"]["raw_usage_json"])["text"] == "<p>Один лист</p>"
    assert seen["row"]["operation"] == "note"


def test_note_edits_parse_and_validate():
    from assistant.lib import note_edits

    answer = (
        'Готово.\n```leo-note\n{"edits":['
        '{"op":"replace","find":"speaker_1","replace":"Мария","all":true},'
        '{"op":"compute","table":1,"fn":"sum","target":{"col":4},"cols":[2,3],"rows":"all"},'
        '{"op":"set_cell","table":1,"row":0,"col":2,"value":"x"},'
        '{"op":"compute","table":1,"fn":"rm","target":{"col":4},"cols":[2],"rows":"all"},'
        '{"op":"insert_after","anchor":"","text":"Итог"}]}\n```'
    )
    clean, edits = note_edits.parse_blocks(answer)
    assert clean == "Готово."
    assert [e["op"] for e in edits] == ["replace", "compute", "insert_after"]
    assert edits[0]["all"] is True and edits[1]["rows"] == "all"
