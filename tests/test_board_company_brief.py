"""Компактный бриф компании вместо полного каталога в каждом ходе."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from assistant.board.company_brief import (
    catalog_index,
    format_company_context,
    select_sections,
    split_sections,
)
from assistant.board.share_source import DEFAULT_SHARE_URL, default_company_share_url, share_body_to_text


CATALOG = """
Речевая аналитика
Платформа анализа звонков менеджеров. Монетизация: 2–3,6 ₽ за минуту. Клиентов: 12.

Боты
ИИ-бот с базой знаний и эскалацией на менеджера. Диалог стоит 10 рублей, себестоимость 1,5 ₽.

Обучат Деск
Help-desk для переписок. 4500 ₽ за менеджера в месяц.

Прошивка телефонов
Запись GSM-звонков на Android и интеграция с речевой аналитикой.

Агрегатор мессенджеров
Веб-приложение для SMS. Монетизации пока нет.

Умный анализ
Рекомендации менеджеру в карточке лида. Анализ сделки 75 ₽. Далее длинное описание фаз, adoption и win rate, которое не должно попадать в индекс: UNIQUE_BITRIX_TAIL.

Команда
CPO, два PM, фулстек, несколько n8n-разработчиков, QA.
"""


class CompanyBriefTest(unittest.TestCase):
    def test_splits_headings(self) -> None:
        sections = split_sections(CATALOG)
        titles = [s["title"] for s in sections]
        self.assertIn("Боты", titles)
        self.assertIn("Речевая аналитика", titles)
        self.assertGreaterEqual(len(sections), 5)

    def test_html_nests_product_sections(self) -> None:
        from assistant.board.company_brief import split_html_sections

        html = (
            "<h3><strong>Боты</strong></h3><h3><strong>Уже есть</strong></h3>"
            "<p>ИИ-бот с базой знаний</p>"
            "<h3><strong>Монетизация</strong></h3><p>Диалог стоит 10 рублей</p>"
            "<h2><strong>Обучат Деск</strong></h2><p>Help-desk</p>"
        )
        sections = split_html_sections(html)
        titles = [s["title"] for s in sections]
        self.assertIn("Боты · Уже есть", titles)
        self.assertIn("Боты · Монетизация", titles)
        self.assertIn("Обучат Деск", titles)
        index = catalog_index(sections)
        self.assertIn("Боты", index)
        self.assertIn("Обучат Деск", index)
        self.assertNotIn("Qolio", index)
        html = "<h3>Речевая аналитика</h3><p>Платформа</p><h3>Боты</h3><p>Диалог 10 рублей</p>"
        text = share_body_to_text(html)
        titles = [s["title"] for s in split_sections(text)]
        self.assertIn("Боты", titles)

    def test_selects_relevant_section(self) -> None:
        sections = split_sections(CATALOG)
        picked = select_sections(sections, "сколько стоит диалог бота для клиента", limit=2)
        titles = [s["title"] for s in picked]
        self.assertIn("Боты", titles)
        self.assertNotIn("Прошивка телефонов", titles)

    def test_brief_is_much_smaller_than_source(self) -> None:
        fat = CATALOG + (" подробности тарифа и роадмапа. " * 400)
        pack = {
            "source_url": DEFAULT_SHARE_URL,
            "_live_share": {"title": "Все продукты", "updated_at": "2026-09-07"},
            "_documents": [{"filename": "Все продукты", "kind": "live", "text": fat}],
        }
        brief = format_company_context(pack, query="юнит-экономика ботов")
        self.assertLess(len(brief), 4500)
        self.assertLess(len(brief) * 4, len(fat))
        self.assertIn("Боты", brief)
        self.assertIn("Каталог", brief)
        self.assertIn("10 рублей", brief)

    def test_index_lists_products_without_full_bodies(self) -> None:
        sections = split_sections(CATALOG)
        index = catalog_index(sections)
        self.assertIn("Умный анализ", index)
        self.assertNotIn("UNIQUE_BITRIX_TAIL", index)

    def test_default_share_url(self) -> None:
        old = os.environ.get("BOARD_COMPANY_SHARE_URL")
        os.environ.pop("BOARD_COMPANY_SHARE_URL", None)
        try:
            self.assertEqual(default_company_share_url(), DEFAULT_SHARE_URL)
            os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
            self.assertEqual(default_company_share_url(), "")
        finally:
            if old is None:
                os.environ.pop("BOARD_COMPANY_SHARE_URL", None)
            else:
                os.environ["BOARD_COMPANY_SHARE_URL"] = old

    def test_meeting_reuses_stored_brief(self) -> None:
        from assistant.board import store
        from assistant.board.context import build_agent_context
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(tmp.name) / "board.sqlite")
        os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
        store.reset_connection()
        store.init_db()
        company = store.get_or_create_company_for_chat(9)
        meeting = store.create_meeting(
            user_id=9, chat_id=9, question="Нужен бот для клиники", company_id=company["id"]
        )
        store.set_analysis(
            meeting["id"],
            {"title": "Бот", "company_brief": "КОМПАНИЯ\nКаталог:\n• Боты — 10 ₽"},
        )
        with patch("assistant.board.context.load_company_pack") as load:
            load.return_value = None
            ctx = build_agent_context(meeting_id=meeting["id"], agent="P")
        load.assert_called()
        self.assertIn("Боты — 10 ₽", ctx["text"])
        self.assertNotIn("Прошивка", ctx["text"])
        store.reset_connection()
        tmp.cleanup()

    def test_knowledge_note_becomes_company_context(self) -> None:
        import tempfile
        from pathlib import Path

        from assistant.board.context import attach_knowledge_note
        from assistant.stores import notes as notes_store

        tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = str(Path(tmp.name) / "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        kb = notes_store.ensure_knowledge_note(11)
        notes_store.update_note(
            11, kb["id"], body="<h2>Боты</h2><p>Диалог стоит 10 рублей</p>"
        )
        pack = attach_knowledge_note(None, 11)
        self.assertIsNotNone(pack)
        self.assertEqual(pack["_documents"][0]["kind"], "knowledge")
        text = format_company_context(pack, query="сколько стоит диалог бота")
        self.assertIn("база знаний", text.lower())
        self.assertIn("Боты", text)
        notes_store._CONN = None  # type: ignore[attr-defined]
        tmp.cleanup()

    def test_knowledge_note_overrides_stored_brief(self) -> None:
        from assistant.board import store
        from assistant.board.context import build_agent_context
        from assistant.stores import notes as notes_store
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(tmp.name) / "board.sqlite")
        os.environ["NOTES_DB_PATH"] = str(Path(tmp.name) / "notes.sqlite")
        os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
        store.reset_connection()
        store.init_db()
        notes_store._CONN = None  # type: ignore[attr-defined]
        kb = notes_store.ensure_knowledge_note(12)
        notes_store.update_note(
            12, kb["id"], body="<h2>Клиника</h2><p>Пакет сопровождения 15 000 ₽</p>"
        )
        company = store.get_or_create_company_for_chat(12)
        meeting = store.create_meeting(
            user_id=12, chat_id=12, question="Сколько стоит сопровождение?", company_id=company["id"]
        )
        store.set_analysis(
            meeting["id"],
            {"title": "Сопровождение", "company_brief": "КОМПАНИЯ\nКаталог:\n• Боты — 10 ₽"},
        )
        ctx = build_agent_context(meeting_id=meeting["id"], agent="P")
        self.assertIn("база знаний", ctx["text"].lower())
        self.assertIn("Клиника", ctx["text"])
        self.assertIn("15 000", ctx["text"])
        self.assertNotIn("Боты — 10 ₽", ctx["text"])
        notes_store._CONN = None  # type: ignore[attr-defined]
        store.reset_connection()
        tmp.cleanup()

    def test_knowledge_can_be_disabled_for_a_meeting(self) -> None:
        from assistant.board import store
        from assistant.board.context import build_agent_context
        from assistant.stores import notes as notes_store
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        os.environ["BOARD_DB_PATH"] = str(Path(tmp.name) / "board.sqlite")
        os.environ["NOTES_DB_PATH"] = str(Path(tmp.name) / "notes.sqlite")
        os.environ["BOARD_COMPANY_SHARE_URL"] = "off"
        store.reset_connection()
        store.init_db()
        notes_store._CONN = None  # type: ignore[attr-defined]
        kb = notes_store.ensure_knowledge_note(13)
        notes_store.update_note(13, kb["id"], body="<p>Секретный пакет 15 000</p>")
        company = store.get_or_create_company_for_chat(13)
        meeting = store.create_meeting(
            user_id=13,
            chat_id=13,
            question="Личная заметка",
            company_id=company["id"],
            extra_instruction="[knowledge:off]\nНе подмешивай базу знаний.",
        )
        ctx = build_agent_context(meeting_id=meeting["id"], agent="P")
        self.assertNotIn("Секретный пакет", ctx["text"])
        self.assertNotIn("[knowledge:off]", ctx["text"])
        notes_store._CONN = None  # type: ignore[attr-defined]
        store.reset_connection()
        tmp.cleanup()

    def test_multiple_knowledge_notes_in_company_context(self) -> None:
        import tempfile
        from pathlib import Path

        from assistant.board.context import attach_knowledge_note
        from assistant.stores import notes as notes_store

        tmp = tempfile.TemporaryDirectory()
        os.environ["NOTES_DB_PATH"] = str(Path(tmp.name) / "notes.sqlite")
        notes_store._CONN = None  # type: ignore[attr-defined]
        notes_store.create_note(
            14,
            "Продукты",
            "<h2>Боты</h2><p>Диалог стоит 10 рублей</p>",
            role=notes_store.KNOWLEDGE_ROLE,
        )
        notes_store.create_note(
            14,
            "Процессы",
            "<h2>Онбординг</h2><p>Новых сотрудников ведёт наставник</p>",
            role=notes_store.KNOWLEDGE_ROLE,
        )
        notes_store.create_note(14, "Пустой", "", role=notes_store.KNOWLEDGE_ROLE)
        off = notes_store.create_note(
            14, "Секрет", "<p>Нельзя в контекст</p>", role=notes_store.KNOWLEDGE_ROLE
        )
        notes_store.update_note(14, off["id"], kb_enabled=False)
        pack = attach_knowledge_note(None, 14)
        self.assertIsNotNone(pack)
        names = {d["filename"] for d in pack["_documents"]}
        self.assertEqual(names, {"Продукты", "Процессы"})
        self.assertTrue(all(d.get("html") for d in pack["_documents"]))
        text = format_company_context(pack, query="онбординг наставник")
        self.assertIn("база знаний", text.lower())
        self.assertIn("наставник", text.lower())
        notes_store._CONN = None  # type: ignore[attr-defined]
        tmp.cleanup()
