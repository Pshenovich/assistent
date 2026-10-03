"""Тесты chat_threads: CRUD, auto-title, cascade delete."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def threads_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db = tmp_path / "chat_threads.sqlite"
    monkeypatch.setenv("CHAT_THREADS_DB_PATH", str(db))
    notes_db = tmp_path / "notes.sqlite"
    monkeypatch.setenv("NOTES_DB_PATH", str(notes_db))
    from assistant.stores import chat_threads as store
    from assistant.stores import notes as notes_store
    from assistant.stores import share_comments

    store._CONN = None  # type: ignore[attr-defined]
    notes_store._CONN = None  # type: ignore[attr-defined]
    yield store
    store._CONN = None  # type: ignore[attr-defined]
    notes_store._CONN = None  # type: ignore[attr-defined]


def test_create_list_rename(threads_db, monkeypatch):
    store = threads_db
    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "План запуска")
    row = store.create_thread(42)
    assert row["title"] == "Новый чат"
    assert row["title_locked"] is False
    listed = store.list_threads(42)
    assert len(listed) == 1
    assert listed[0]["id"] == row["id"]

    renamed = store.rename_thread(42, row["id"], "Мой чат", lock=True)
    assert renamed is not None
    assert renamed["title"] == "Мой чат"
    assert renamed["title_locked"] is True


def test_autotitle_after_first_user_message(threads_db, monkeypatch):
    store = threads_db
    monkeypatch.setattr(store, "suggest_chat_title", lambda m: "Тема из текста")
    row = store.create_thread(7)
    updated = store.after_comment(7, row["id"], "Нужен план запуска продукта", is_user=True)
    assert updated is not None
    assert updated["title"] == "Тема из текста"
    assert updated["title_locked"] is False
    assert "план" in updated["last_preview"].lower() or "Нужен" in updated["last_preview"]

    # locked rename stops auto
    store.rename_thread(7, row["id"], "Закреплено", lock=True)
    again = store.after_comment(7, row["id"], "Другое сообщение", is_user=True)
    assert again is not None
    assert again["title"] == "Закреплено"


def test_pin_thread(threads_db, monkeypatch):
    store = threads_db
    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "X")
    row = store.create_thread(5)
    store.after_comment(5, row["id"], "привет", is_user=True)
    pinned = store.set_pinned(5, row["id"], True)
    assert pinned is not None
    assert pinned["pinned"] is True
    listed = store.list_threads(5)
    assert listed[0]["id"] == row["id"]
    assert listed[0]["pinned"] is True


def test_cascade_delete_comments(threads_db, monkeypatch):
    store = threads_db
    from assistant.stores import share_comments

    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "X")
    row = store.create_thread(3)
    share_comments.add_comment(
        3,
        "chat",
        row["id"],
        author_user_id=3,
        author_name="User",
        body="привет",
    )
    assert len(share_comments.list_comments(3, "chat", row["id"])) == 1
    assert store.delete_thread(3, row["id"]) is True
    assert store.get_thread(3, row["id"]) is None
    assert share_comments.list_comments(3, "chat", row["id"]) == []


def test_list_marks_has_messages_from_comments(threads_db, monkeypatch):
    store = threads_db
    from assistant.stores import share_comments

    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "X")
    empty = store.create_thread(8)
    filled = store.create_thread(8, title="Рабочий чат")
    share_comments.add_comment(
        8,
        "chat",
        filled["id"],
        author_user_id=8,
        author_name="User",
        body="нужен план на неделю",
    )
    listed = {int(t["id"]): t for t in store.list_threads(8)}
    assert listed[int(empty["id"])]["has_messages"] is False
    assert listed[int(empty["id"])]["message_count"] == 0
    assert listed[int(filled["id"])]["has_messages"] is True
    assert listed[int(filled["id"])]["message_count"] == 1
    assert "план" in listed[int(filled["id"])]["last_preview"]


def test_touch_preview_ignores_empty(threads_db, monkeypatch):
    store = threads_db
    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "X")
    row = store.create_thread(9)
    store.after_comment(9, row["id"], "Первое сообщение", is_user=True)
    again = store.touch_preview(9, row["id"], "   ")
    assert again is not None
    assert "Первое" in again["last_preview"]


def test_chat_members(threads_db, monkeypatch):
    store = threads_db
    from assistant.stores import note_members

    note_members._CONN = None  # type: ignore[attr-defined]
    note_members.upsert_profile(10, username="a", first_name="A")
    note_members.upsert_profile(20, username="b", first_name="B")
    monkeypatch.setattr(store, "suggest_chat_title", lambda _m: "X")
    row = store.create_thread(10, title="Shared")
    member = store.add_member(10, row["id"], 20)
    assert int(member["user_id"]) == 20
    listed = store.list_threads(20)
    assert any(int(t["id"]) == int(row["id"]) for t in listed)
    acc = store.get_accessible_thread(20, row["id"])
    assert acc is not None
    assert acc["is_owner"] is False
    assert len(acc["members"]) >= 2
    assert store.remove_member(10, row["id"], 20) is True
    assert store.get_accessible_thread(20, row["id"]) is None
