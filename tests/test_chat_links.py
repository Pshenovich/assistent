"""Ссылки на Google Документы/Таблицы в чате: скачиваем и подкладываем модели текстом;
большие — обрабатываем частями в фоне (bulk_links)."""

from __future__ import annotations

import time

from assistant.integrations import knowledge_google_docs as gdocs
from assistant.lib import chat_links
from assistant.lib.chat_links import Source
from assistant.services import bulk_links

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


def _fake_fetch(url):
    if "spreadsheets" in url:
        return Source(url=url, label="Google Таблица", sheets=[("Лист1", ["Клиент\tТем", "Альфа\t3"])])
    return Source(url=url, label="Google Документ", error="no_access")


def test_prompt_notes_content_and_no_access(monkeypatch):
    monkeypatch.setattr(chat_links, "_inline_body", lambda src, limit: "\n".join(src.sheets[0][1]))
    chat_links._CACHE.clear()
    notes = chat_links.prompt_notes(f"сделай таблицу по {SHEET} и {DOC}", fetch=_fake_fetch)
    assert "Клиент\tТем\nАльфа\t3" in notes
    assert "не говори, что не умеешь открывать ссылки" in notes
    assert "закрыта" in notes and "Все, у кого есть ссылка" in notes
    assert chat_links.prompt_notes("просто текст", fetch=_fake_fetch) == ""


def test_load_sources_cached():
    chat_links._CACHE.clear()
    calls = []

    def fetch(url):
        calls.append(url)
        return _fake_fetch(url)

    chat_links.load_sources(SHEET, fetch=fetch)
    chat_links.load_sources(f"ещё раз {SHEET}", fetch=fetch)
    assert calls == [SHEET]


def test_fetch_google_doc_detects_login_page(monkeypatch):
    class R:
        status_code = 200
        ok = True
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = "https://accounts.google.com/ServiceLogin?continue=..."
        text = "<html>Sign in</html>"
        content = b"<html>Sign in</html>"
        encoding = None

    monkeypatch.setattr(gdocs.requests, "get", lambda *a, **k: R())
    for fn in (gdocs.fetch_google_doc, gdocs.fetch_google_sheet_xlsx):
        try:
            fn(SHEET)
        except gdocs.GoogleDocNoAccess:
            continue
        raise AssertionError("закрытый документ должен давать GoogleDocNoAccess")


def _big_source():
    rows_a = ["Транскрипт"] + [f"диалог {i}: " + "слово " * 400 for i in range(60)]
    rows_b = ["Транскрипт"] + [f"разговор {i}: " + "текст " * 400 for i in range(30)]
    return Source(url=SHEET, label="Google Таблица", sheets=[("Вергиз", rows_a), ("SmartMag", rows_b)])


def test_make_chunks_by_sheet_with_header():
    chunks, sheets, rows = bulk_links.make_chunks([_big_source()])
    assert sheets == ["Вергиз", "SmartMag"] and rows == 90
    assert all(len(c.text) <= bulk_links.CHUNK_CHARS + 600 for c in chunks)
    assert all(c.text.startswith("Транскрипт\n") for c in chunks)
    assert chunks[0].label.startswith("Google Таблица, лист «Вергиз», строки 2–")
    assert any("лист «SmartMag»" in c.label for c in chunks)
    # все строки попали ровно в одну часть
    joined = "\n".join(c.text for c in chunks)
    assert joined.count("диалог ") == 60 and joined.count("разговор ") == 30


def test_bulk_job_runs_all_chunks_and_builds_result():
    seen = []

    def complete(payload):
        part = payload["messages"][1]["content"]
        seen.append(part)
        assert "Просьба пользователя: посчитай темы" in part
        return {"choices": [{"message": {"content": "Поверка счётчика — 3"}}]}

    job = bulk_links.start_job(7, "посчитай темы " + SHEET, [_big_source(), _fake_fetch(DOC)], complete=complete)
    for _ in range(100):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "done" and job.done == len(job.chunks) == len(seen) and job.failed == 0
    assert bulk_links.get_job(job.id, 7) is job and bulk_links.get_job(job.id, 8) is None
    notes = bulk_links.result_notes(job)
    assert "ПОЛНЫЙ охват" in notes and notes.count("Поверка счётчика — 3") == len(job.chunks)
    assert "90 строк" in notes and "Вергиз, SmartMag" in notes
    assert "закрыта" in notes  # закрытый документ не потерялся
    assert job.public()["total"] == len(job.chunks)
