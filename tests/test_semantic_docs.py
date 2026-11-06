"""Поиск Лео: общий документ векторизуется один раз (раздел "doc:<id>"), права — при каждом поиске."""

import hashlib
import json
import re

import pytest

from assistant.services import semantic_memory as sm
from assistant.stores import semantic_index as idx

OWNER, MEMBER, STRANGER = 101, 102, 103


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0] * 64
        for w in re.findall(r"[а-яёa-z0-9]{3,}", t.lower()):
            v[int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 64] += 1.0
        out.append(v)
    return out


class Counting:
    def __init__(self):
        self.texts = []

    def __call__(self, texts):
        self.texts += texts
        return fake_embed(texts)


@pytest.fixture
def stores(tmp_path, monkeypatch):
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    monkeypatch.setenv("NOTES_DB_PATH", str(tmp_path / "notes.sqlite"))
    monkeypatch.setenv("SEMANTIC_DB_PATH", str(tmp_path / "sem.sqlite"))
    monkeypatch.setenv("TELEGRAM_REGISTRY_PATH", str(tmp_path / "reg.json"))
    monkeypatch.setenv("NOTE_VERSIONS_DB_PATH", str(tmp_path / "versions.sqlite"))
    monkeypatch.setattr(tnotes, "_focus_summary_ready", lambda *a, **k: None)
    monkeypatch.setattr(sm, "sources_for_user", lambda uid: [])  # личное (почта, задачи) здесь не нужно
    notes_store._CONN = None
    idx._CONN = None
    yield notes_store
    notes_store._CONN = None
    idx._CONN = None


def _shared_doc(notes_store):
    from assistant.stores import note_members

    note = notes_store.create_note(OWNER, "Регламент релиза",
                                   "<p>Перед выкаткой прогоняем тесты, поднимаем версию ассетов и проверяем вход на телефоне.</p>")
    note_members.add_member(OWNER, note["id"], MEMBER)
    return note


def _note_ids(uid, query):
    return [h["ref"].get("note_id") for h in sm.search(uid, query, embed=fake_embed)]


def test_shared_doc_indexed_once_and_found_by_both(stores):
    note = _shared_doc(stores)
    emb = Counting()
    res = sm.index_docs(embed=emb)
    assert res["indexed"] == 1 and len(emb.texts) == 1  # один раз на документ, не на каждого участника
    assert idx.stats(sm.doc_owner_key(note["id"]))["chunks"] == 1
    assert idx.stats(OWNER)["chunks"] == 0 and idx.stats(MEMBER)["chunks"] == 0
    for uid in (OWNER, MEMBER):
        hits = sm.search(uid, "что проверить перед выкаткой релиза", embed=fake_embed)
        assert hits[0]["ref"]["note_id"] == note["id"] and hits[0]["url"].endswith(f"note={note['id']}")
    assert note["id"] not in _note_ids(STRANGER, "что проверить перед выкаткой релиза")


def test_access_checked_on_every_search(stores):
    from assistant.stores import note_members

    note = _shared_doc(stores)
    sm.index_docs(embed=fake_embed)
    note_members.remove_member(OWNER, note["id"], MEMBER)
    # Без переиндексации: участника убрали — документ из его поиска пропал сразу.
    assert note["id"] not in _note_ids(MEMBER, "что проверить перед выкаткой релиза")
    assert note["id"] in _note_ids(OWNER, "что проверить перед выкаткой релиза")


def test_edit_reindexes_once_for_everyone(stores):
    note = _shared_doc(stores)
    sm.index_docs(embed=fake_embed)
    stores.update_note(OWNER, note["id"], body="<p>Перед выкаткой согласуем окно с поддержкой и пишем в канал релизов.</p>",
                       actor_user_id=OWNER)
    emb = Counting()
    assert sm.index_doc(note["id"], embed=emb)["indexed"] == 1 and len(emb.texts) == 1
    hits = sm.search(MEMBER, "согласовать окно с поддержкой", embed=fake_embed)
    assert hits[0]["ref"]["note_id"] == note["id"] and "окно с поддержкой" in hits[0]["snippet"]


def test_doc_changed_debounced_by_document(stores, monkeypatch):
    import threading

    monkeypatch.setenv("SEMANTIC_SEARCH_ENABLED", "1")
    done = threading.Event()
    calls = []
    monkeypatch.setattr(sm, "_index_doc_now", lambda nid: (calls.append(nid), done.set()))
    sm.doc_changed(7, delay=5)
    sm.doc_changed(7, delay=0.01)  # повтор по тому же документу откладывает, а не добавляет прогон
    assert done.wait(3)
    assert calls == [7]


def test_member_finds_shared_meeting(stores):
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import note_members

    meet = tnotes.create_with_summary(OWNER, title="Синк по ценам", transcript="Спикер 1: обсуждаем цены на тариф",
                                      summary="<p>Решили поднять цены на тариф Про на десять процентов с ноября.</p>")
    note_members.add_member(OWNER, meet["id"], MEMBER)
    sm.index_docs(embed=fake_embed)
    hits = sm.search(MEMBER, "что решили по ценам тарифа", embed=fake_embed)
    assert hits[0]["ref"] == {"note_id": meet["id"], "kind": "transcription"}  # раньше у участника встречи не было


def test_deleted_doc_partition_removed(stores):
    note = _shared_doc(stores)
    sm.index_docs(embed=fake_embed)
    stores.delete_note(OWNER, note["id"])
    assert note["id"] not in _note_ids(OWNER, "что проверить перед выкаткой релиза")
    res = sm.index_docs(embed=fake_embed)
    assert res["removed"] == 1 and idx.stats(sm.doc_owner_key(note["id"]))["sources"] == 0


def test_legacy_copies_moved_without_reembedding(stores):
    """Старая схема: копия документа в личном разделе каждого и в разделе организации.
    Проход переносит готовые векторы в раздел документа и убирает копии — без новых эмбеддингов."""
    note = _shared_doc(stores)
    [src] = sm.doc_sources(stores.get_note_by_id(note["id"]))
    chunks = sm.chunk_text(src["text"])
    vecs = fake_embed([f"{src['title']}. {c}" for c in chunks])
    for owner in (OWNER, MEMBER):
        idx.upsert_source(owner, src["key"], content_hash=sm._hash(src["text"]), kind=src["kind"], title=src["title"],
                          date=src["date"], ref_json=json.dumps(src["ref"]), chunks=chunks, vecs=vecs)
    idx.upsert_source(OWNER, "task:9", content_hash="x", kind="task", title="Задача", date="", ref_json="{}",
                      chunks=["личное"], vecs=fake_embed(["личное"]))
    # До прохода по документам старая копия ищется (но без дублей) и не пропадает при переиндексации личного.
    sm.index_user(MEMBER, embed=fake_embed)
    hits = sm.search(MEMBER, "что проверить перед выкаткой релиза", embed=fake_embed)
    assert [h["ref"]["note_id"] for h in hits] == [note["id"]]

    def no_embed(texts):
        raise AssertionError(f"лишние эмбеддинги: {texts}")

    res = sm.index_docs(embed=no_embed)
    assert res["copied"] == 1 and res["indexed"] == 0 and res["purged"] == 2
    assert idx.stats(OWNER) == {"sources": 1, "chunks": 1}  # личное осталось
    assert idx.stats(MEMBER) == {"sources": 0, "chunks": 0}
    for uid in (OWNER, MEMBER):
        assert _note_ids(uid, "что проверить перед выкаткой релиза")[0] == note["id"]


def test_legacy_org_partition_moved(stores):
    """Документ базы знаний организации раньше лежал в "org:<id>" под ключом org:<note> — переносится как kb:<note>."""
    note = stores.create_note(OWNER, "Перезапуск n8n",
                              "<p>Если воркфлоу перестали срабатывать, перезапустите контейнер n8n на сервере.</p>")
    src = sm.doc_sources(stores.get_note_by_id(note["id"]), org_id=5)[0]
    assert src["key"] == f"kb:{note['id']}"
    chunks = sm.chunk_text(src["text"])
    idx.upsert_source("org:5", f"org:{note['id']}", content_hash=sm._hash(src["text"]), kind="knowledge",
                      title=src["title"], date=src["date"], ref_json="{}", chunks=chunks,
                      vecs=fake_embed([f"{src['title']}. {c}" for c in chunks]))
    emb = Counting()
    sm.index_doc(note["id"], embed=emb, org_id=5)
    assert emb.texts == [] and idx.stats(sm.doc_owner_key(note["id"]))["chunks"] == 1
