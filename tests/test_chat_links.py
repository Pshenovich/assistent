"""Ссылки на Google Документы/Таблицы в чате: скачиваем и подкладываем модели текстом."""

from __future__ import annotations

from types import SimpleNamespace

from assistant.integrations import knowledge_google_docs as gdocs
from assistant.lib import chat_links

SHEET = "https://docs.google.com/spreadsheets/d/1AbC_d-9/edit#gid=42"
DOC = "https://docs.google.com/document/d/XyZ123/edit?usp=sharing"


def test_find_links_dedupes_and_strips_punctuation():
    text = f"Глянь {SHEET}, и ещё {DOC}. И снова {SHEET}"
    assert chat_links.find_links(text) == [SHEET, DOC]
    assert chat_links.find_links("без ссылок https://example.com") == []


def test_export_url_uses_sheet_gid():
    assert gdocs.parse_gid(SHEET) == "42"
    assert gdocs.export_url("1AbC_d-9", spreadsheet=True, gid="42").endswith("format=csv&gid=42")
    assert "gid" not in gdocs.export_url("XyZ123", spreadsheet=False, gid="42")


def test_csv_to_text_trims_and_limits():
    raw = "Клиент,Тем,Встреч,,\nАльфа,3,5,,\n,,,,\n\"Бета, ООО\",2,4\n"
    assert chat_links.csv_to_text(raw) == "Клиент\tТем\tВстреч\nАльфа\t3\t5\nБета, ООО\t2\t4"
    big = "\n".join(f"r{i},{'x' * 50}" for i in range(100))
    out = chat_links.csv_to_text(big, limit=600)
    assert "таблица обрезана" in out and "из 100 строк" in out


def test_prompt_notes_content_and_no_access():
    def fetch(url):
        if "spreadsheets" in url:
            return SimpleNamespace(text="Клиент,Тем\nАльфа,3\n")
        raise gdocs.GoogleDocNoAccess("closed")

    notes = chat_links.prompt_notes(f"сделай таблицу по {SHEET} и {DOC}", fetch=fetch)
    assert "Клиент\tТем\nАльфа\t3" in notes
    assert "не говори, что не умеешь открывать ссылки" in notes
    assert "закрыта" in notes and "Все, у кого есть ссылка" in notes
    assert chat_links.prompt_notes("просто текст", fetch=fetch) == ""


def test_fetch_google_doc_detects_login_page(monkeypatch):
    class R:
        status_code = 200
        ok = True
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = "https://accounts.google.com/ServiceLogin?continue=..."
        text = "<html>Sign in</html>"
        encoding = None

    monkeypatch.setattr(gdocs.requests, "get", lambda *a, **k: R())
    try:
        gdocs.fetch_google_doc(SHEET)
    except gdocs.GoogleDocNoAccess:
        pass
    else:
        raise AssertionError("закрытый документ должен давать GoogleDocNoAccess")
