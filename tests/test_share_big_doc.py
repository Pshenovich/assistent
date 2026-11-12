"""Ссылка на несколько листов большого документа (картинки внутри) — видны все листы целиком."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def notes_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    from assistant.stores import notes as notes_store

    notes_store._CONN = None  # type: ignore[attr-defined]
    yield notes_store
    notes_store._CONN = None  # type: ignore[attr-defined]


def test_share_shows_all_sheets_of_big_doc(notes_db):
    from assistant.stores import note_sheets, share_links
    import usage_server

    img = '<img src="data:image/png;base64,' + "A" * 200_000 + '">'
    note = notes_db.create_note(5, "Баги", "<p>Функциональные ошибки</p>" + img + "<p>Конец основного</p>")
    s2 = note_sheets.create_sheet(note, "Недочеты интерфейса", "<p>Кнопка уехала</p>" + img)
    s3 = note_sheets.create_sheet(note, "На обсуждение", "<p>Вопросы по сервису</p>")
    link = share_links.create_or_get_share(5, "local", note["id"], "view", sheet_ids=["main", str(s2["id"]), str(s3["id"])])
    payload = usage_server._public_share_payload(link)
    body = payload["body"]
    for text in ("Конец основного", "Недочеты интерфейса", "Кнопка уехала", "На обсуждение", "Вопросы по сервису"):
        assert text in body
