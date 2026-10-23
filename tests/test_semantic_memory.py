import hashlib
import json
import re

import pytest

from assistant.services import semantic_memory as sm
from assistant.stores import semantic_index as idx


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in re.findall(r"[а-яёa-z0-9]{3,}", t.lower()):
            v[int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 64] += 1.0
        out.append(v)
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("SEMANTIC_DB_PATH", str(tmp_path / "sem.sqlite"))
    idx._CONN = None
    srcs = [
        {"key": "tr_sum:1", "kind": "meeting_summary", "title": "Синк по ценам", "date": "2026-10-01",
         "text": "Решили поднять цены на тариф Про на десять процентов с ноября. Оксана готовит рассылку клиентам.",
         "ref": {"note_id": 1}},
        {"key": "note:2", "kind": "note", "title": "Интеграция почты", "date": "2026-10-05",
         "text": "Подключить десять почтовых каналов на Бустере, обсудить стоимость с техкомандой.", "ref": {"note_id": 2}},
    ]
    monkeypatch.setattr(sm, "sources_for_user", lambda uid: [dict(s) for s in srcs])
    yield srcs
    idx._CONN = None


def test_chunk_text_sizes_and_overlap():
    text = " ".join(f"Предложение номер {i} про проект." for i in range(200))
    chunks = sm.chunk_text(text, size=300, overlap=60)
    assert len(chunks) > 5 and all(len(c) <= 360 for c in chunks)
    assert "номер 0 " in chunks[0] and "номер 199" in chunks[-1]
    assert sm.chunk_text("коротко") == ["коротко"] and sm.chunk_text("") == []


def test_index_and_search(env):
    calls = []

    def counting(texts):
        calls.append(len(texts))
        return fake_embed(texts)

    assert sm.index_user(1, embed=counting) == {"indexed": 2, "skipped": 0, "removed": 0, "chunks": 2}
    assert sm.index_user(1, embed=counting)["skipped"] == 2 and len(calls) == 2  # без изменений — без вызовов

    hits = sm.search(1, "что решили по ценам тарифа", embed=fake_embed)
    assert hits[0]["title"] == "Синк по ценам" and hits[0]["ref"] == {"note_id": 1}
    hits = sm.search(1, "почтовые каналы на Бустере", embed=fake_embed)
    assert hits[0]["title"] == "Интеграция почты"

    env[1]["text"] += " Срок — конец месяца."
    env.pop(0)
    res = sm.index_user(1, embed=fake_embed)
    assert res["indexed"] == 1 and res["removed"] == 1
    assert idx.stats(1) == {"sources": 1, "chunks": 1}
    assert idx.stats(2) == {"sources": 0, "chunks": 0}  # чужой индекс пуст


def test_tool_and_active_tools(env, monkeypatch):
    from assistant.lib import embed_client
    from assistant.services import gpt_calendar_tools as gct

    monkeypatch.delenv("SEMANTIC_SEARCH_ENABLED", raising=False)
    assert "memory_search" not in [t["function"]["name"] for t in gct.active_tools()]
    assert "не включён" in gct.tool_memory_search(gct.GptCalendarContext(uid=1), {"query": "цены"})

    monkeypatch.setenv("SEMANTIC_SEARCH_ENABLED", "1")
    monkeypatch.setattr(embed_client, "embed", fake_embed)
    sm.index_user(1, embed=fake_embed)
    assert "memory_search" in [t["function"]["name"] for t in gct.active_tools()]
    res = json.loads(gct.tool_memory_search(gct.GptCalendarContext(uid=1), {"query": "повышение цен тариф"}))
    assert res["results"][0]["title"] == "Синк по ценам"


def test_sources_for_user_real_stores(tmp_path, monkeypatch):
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    monkeypatch.setattr(tnotes, "_focus_summary_ready", lambda *a, **k: None)
    tnotes.create_with_summary(5, title="Синк", transcript="Спикер 1: обсуждаем цены", summary="<p>Решили поднять цены</p>")
    notes_store.create_note(5, "Идеи", "<p>Длинная заметка про интеграцию почтовых каналов и бота</p>")
    notes_store.create_note(5, "Пусто", "<p>коротко</p>")
    keys = sorted(s["key"].split(":")[0] for s in sm.sources_for_user(5))
    assert keys == ["note", "tr_sum", "tr_txt"]
    notes_store._CONN = None


def test_sheets_are_indexed(tmp_path, monkeypatch):
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import note_sheets
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    monkeypatch.setattr(tnotes, "_focus_summary_ready", lambda *a, **k: None)
    # Основной лист короткий — важное на втором листе.
    note = notes_store.create_note(6, "Проект", "<p>см. листы</p>")
    sheet = note_sheets.create_sheet(note, "Бюджет", body="<p>Бюджет пилота — 1,2 млн, согласовал финдиректор в среду</p>")
    note_sheets.create_sheet(note, "Слайды", body="<html><body><h1>Слайды про бюджет пилота и сроки</h1></body></html>", kind="web")
    meet = tnotes.create_with_summary(6, title="Синк", transcript="Спикер 1: обсуждаем цены на тариф", summary="<p>Решили поднять цены</p>")
    note_sheets.create_sheet(meet, "Задачи", body="<p>Оксана готовит рассылку клиентам до пятницы</p>")
    srcs = {s["key"]: s for s in sm.sources_for_user(6)}
    sheet_src = srcs[f"sheet:{note['id']}:{sheet['id']}"]
    assert sheet_src["title"] == "Проект · Бюджет" and "1,2 млн" in sheet_src["text"]
    assert sheet_src["ref"] == {"note_id": note["id"], "sheet_id": sheet["id"], "sheet_title": "Бюджет", "kind": "note"}
    meet_sheets = [s for k, s in srcs.items() if k.startswith(f"sheet:{meet['id']}:")]
    # Лист «Транскрипции» уже в tr_txt — не дублируем; канвас не индексируем.
    assert [s["title"] for s in meet_sheets] == ["Синк · Задачи"]
    assert not any("Слайды" in s["title"] for s in srcs.values())
    assert f"note:{note['id']}" not in srcs  # основной лист короче 40 символов
    notes_store._CONN = None


def test_status_and_note_changed(env, monkeypatch):
    import threading

    monkeypatch.setenv("SEMANTIC_SEARCH_ENABLED", "1")
    monkeypatch.setattr(sm.embed_client, "embed", fake_embed)
    sm._STATUS_CACHE.clear()
    st = sm.status(1)
    assert st == {"enabled": True, "total": 2, "indexed": 0, "pending": ["Синк по ценам", "Интеграция почты"]}

    done = threading.Event()
    real = sm._index_now
    monkeypatch.setattr(sm, "_index_now", lambda uid: (real(uid), done.set()))
    sm.note_changed(1, delay=5)
    sm.note_changed(1, delay=0.01)  # повтор отменяет предыдущий таймер
    assert done.wait(3)
    st = sm.status(1)  # кэш сброшен индексацией
    assert st["indexed"] == 2 and st["pending"] == []

    monkeypatch.setenv("SEMANTIC_SEARCH_ENABLED", "0")
    assert sm.status(1) == {"enabled": False}


def test_title_in_question_wins_and_rename_refreshes(env):
    # Короткий вопрос — одно название; текст заметки этого слова не содержит.
    env.append({"key": "note:47", "kind": "note", "title": "Вебапп", "date": "2026-10-22",
                "text": "Сборка мини-приложения, кэш сервис-воркера и версии ассетов при выкатке.", "ref": {"note_id": 47}})
    env.append({"key": "sheet:47:38", "kind": "note", "title": "Вебапп · Лист 2", "date": "2026-10-22",
                "text": "Чеклист перед релизом: тесты, версии, проверка на телефоне.", "ref": {"note_id": 47, "sheet_id": 38}})
    sm.index_user(1, embed=fake_embed)
    hits = sm.search(1, "дай ссылку на заметку Вебапп", embed=fake_embed)
    assert [h["title"] for h in hits[:2]] == ["Вебапп", "Вебапп · Лист 2"]
    assert hits[0]["url"].endswith("note=47")
    # Переименовали — текст тот же, подпись обновилась без пересчёта векторов.
    env[-2]["title"] = "Webapp и PWA"
    calls = []
    sm.index_user(1, embed=lambda t: calls.append(t) or fake_embed(t))
    assert calls == []
    assert any(h["title"] == "Webapp и PWA" for h in sm.search(1, "Webapp PWA", embed=fake_embed))


def test_generic_titles_do_not_hijack(env):
    env.append({"key": "note:9", "kind": "note", "title": "Задачи", "date": "2026-10-22",
                "text": "Купить корм коту и позвонить в банк про карту.", "ref": {"note_id": 9}})
    sm.index_user(1, embed=fake_embed)
    hits = sm.search(1, "какие задачи по ценам тарифа", embed=fake_embed)
    # «Задачи» — общее название: слово в вопросе не поднимает заметку наверх принудительно.
    assert all(h["score"] < sm.TITLE_MATCH_RANK for h in hits if h["title"] == "Задачи")


def test_knowledge_docs_in_memory_and_notes_find(tmp_path, monkeypatch):
    from assistant.services import gpt_calendar_tools as gct
    from assistant.stores import note_sheets
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    notes_store._CONN = None
    kb = notes_store.create_note(8, "Обучат Деск", "<p>Для чего нужен продукт: чат-поддержка на сайте, боты и операторы</p>",
                                 role=notes_store.KNOWLEDGE_ROLE)
    note_sheets.create_sheet(kb, "Тарифы", body="<p>Тариф Про — десять операторов и интеграция с CRM</p>")
    notes_store.create_note(8, "Вебапп", "<p>Сборка мини-приложения и кэш сервис-воркера при выкатке</p>")
    srcs = {s["key"]: s for s in sm.sources_for_user(8)}
    assert srcs[f"kb:{kb['id']}"]["kind"] == "knowledge"
    assert any(k.startswith(f"sheet:{kb['id']}:") and s["title"] == "Обучат Деск · Тарифы" for k, s in srcs.items())
    res = json.loads(gct.tool_notes_find(gct.GptCalendarContext(uid=8), {"query": "дай ссылку на заметку Вебапп"}))
    assert res["notes"][0]["title"] == "Вебапп" and "note=" in res["notes"][0]["url"]
    res = json.loads(gct.tool_notes_find(gct.GptCalendarContext(uid=8), {"query": "Обучат Деск"}))
    assert res["notes"][0]["kind"] == "knowledge"
    notes_store._CONN = None
