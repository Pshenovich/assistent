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
