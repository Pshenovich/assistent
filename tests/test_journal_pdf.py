"""Тесты генерации PDF для журнала."""

from __future__ import annotations

import json

import pytest

from assistant.lib import journal_pdf as jp


def test_html_to_plain_strips_tags():
    assert jp._html_to_plain("<b>Заголовок</b><br/>Текст") == "Заголовок\nТекст"


def test_normalize_collapsed_markdown_splits_sections():
    raw = "## 📌 Кратко Текст раздела. ## 🎯 Что обсуждали Обсуждали план."
    out = jp.normalize_collapsed_markdown(raw)
    assert "## 📌 Кратко" in out
    assert "Текст раздела." in out
    assert "## 🎯 Что обсуждали" in out


def test_normalize_collapsed_markdown_tasks():
    raw = "## 📋 Задачи - [ ] Иван — отчёт - [ ] Петр — проверка"
    out = jp.normalize_collapsed_markdown(raw)
    assert "## 📋 Задачи" in out
    assert "- [ ] Иван — отчёт" in out
    assert "- [ ] Петр — проверка" in out


def test_build_journal_pdf_rejects_unknown_op():
    with pytest.raises(ValueError, match="транскрипций"):
        jp.build_journal_pdf({"operation": "chat", "raw_usage_json": "{}"})


def test_build_journal_pdf_transcribe():
    try:
        jp._resolve_font_paths()
    except RuntimeError:
        pytest.skip("нет шрифта для PDF")
    raw = json.dumps(
        {
            "text": "Привет, это тестовая транскрипция.",
            "filename": "meet.mp3",
        },
        ensure_ascii=False,
    )
    row = {
        "id": 42,
        "operation": "obuchat_transcribe",
        "ts_utc": "2026-06-07T12:00:00",
        "raw_usage_json": raw,
    }
    data, fname, caption = jp.build_journal_pdf(row)
    assert data[:4] == b"%PDF"
    assert fname.endswith(".pdf")
    assert "transkript" in fname
    assert "Транскрипция" in caption


def test_prepare_body_text_html_summary_to_markdown():
    html = (
        "<h2>📌 Кратко</h2><p>Решили запустить проект.</p>"
        "<h2>📋 Задачи</h2><ul><li checked><p>Иван — отчёт</p></li></ul>"
    )
    out = jp._prepare_body_text(html, "summarize")
    assert "## 📌 Кратко" in out
    assert "Решили запустить проект." in out
    assert "[x]" in out
    assert "Иван" in out


def test_build_journal_pdf_summary_strips_html():
    try:
        jp._resolve_font_paths()
    except RuntimeError:
        pytest.skip("нет шрифта для PDF")
    raw = json.dumps(
        {
            "text": "<b>📌 Кратко</b><br/>Решили запустить проект.",
            "meta": {"main_topic": "Статус проекта"},
        },
        ensure_ascii=False,
    )
    row = {
        "id": 7,
        "operation": "summarize",
        "ts_utc": "2026-06-07T13:00:00",
        "raw_usage_json": raw,
    }
    data, fname, caption = jp.build_journal_pdf(row)
    assert data[:4] == b"%PDF"
    assert fname.startswith("samari")
    assert "Саммари" in caption


def test_build_journal_pdf_summary_markdown_sections():
    try:
        jp._resolve_font_paths()
    except RuntimeError:
        pytest.skip("нет шрифта для PDF")
    raw = json.dumps(
        {
            "text": (
                "## 📌 Кратко\n"
                "Краткий итог встречи.\n\n"
                "## 📋 Задачи\n"
                "- [ ] Иван — подготовить отчёт\n"
                "- [ ] Петр — согласовать бюджет"
            ),
            "meta": {"main_topic": "Планирование"},
        },
        ensure_ascii=False,
    )
    row = {
        "id": 8,
        "operation": "summarize",
        "ts_utc": "2026-06-07T14:00:00",
        "raw_usage_json": raw,
    }
    data, fname, caption = jp.build_journal_pdf(row)
    assert data[:4] == b"%PDF"
    assert fname.startswith("samari")
