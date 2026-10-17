"""Уведомления обсуждений: solo, presence, каналы."""

from __future__ import annotations

import assistant.services.discussion_notify as dn
from assistant.stores import discussion_presence as dp


def test_list_participants_falls_back_to_owner(monkeypatch):
    monkeypatch.setattr(
        "assistant.stores.note_members.list_members_public",
        lambda *a, **k: [],
    )
    members = dn.list_discussion_participants("7", "local", "42")
    assert members == [{"user_id": "7", "name": "Автор", "username": ""}]


def test_solo_owner_ai_notifies_owner(monkeypatch):
    sent_tg: list[int] = []
    sent_wp: list[int] = []
    monkeypatch.setattr(
        dn,
        "list_discussion_participants",
        lambda *a, **k: [{"user_id": "7", "name": "Leo", "username": ""}],
    )
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: set())
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "Заметка")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_telegram",
        lambda uid: True,
    )
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_web_push",
        lambda uid: True,
    )
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text: sent_tg.append(uid))
    monkeypatch.setattr(
        dn, "send_web_push", lambda uid, payload: sent_wp.append(uid) or 1
    )

    out = dn.notify_discussion_comment(
        owner="7",
        kind="local",
        item_id="1",
        author_user_id="0",
        author_name="GPT",
        body="Готово",
        is_assistant=True,
    )
    assert out == ["7"]
    assert sent_tg == [7]
    assert sent_wp == [7]


def test_human_author_is_skipped(monkeypatch):
    sent: list[int] = []
    monkeypatch.setattr(
        dn,
        "list_discussion_participants",
        lambda *a, **k: [{"user_id": "7"}, {"user_id": "8"}],
    )
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: set())
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "Заметка")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_telegram",
        lambda uid: True,
    )
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_web_push",
        lambda uid: False,
    )
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text: sent.append(uid))
    monkeypatch.setattr(dn, "send_web_push", lambda uid, payload: 0)

    out = dn.notify_discussion_comment(
        owner="7",
        kind="local",
        item_id="1",
        author_user_id="7",
        author_name="Leo",
        body="Привет",
        is_assistant=False,
    )
    assert out == ["8"]
    assert sent == [8]


def test_present_recipient_is_skipped(monkeypatch):
    sent: list[int] = []
    monkeypatch.setattr(
        dn,
        "list_discussion_participants",
        lambda *a, **k: [{"user_id": "7"}],
    )
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: {"7"})
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text: sent.append(uid))
    monkeypatch.setattr(dn, "send_web_push", lambda uid, payload: 1)

    out = dn.notify_discussion_comment(
        owner="7",
        kind="local",
        item_id="1",
        author_user_id="0",
        author_name="GPT",
        body="Готово",
        is_assistant=True,
    )
    assert out == []
    assert sent == []


def test_telegram_only_skips_web_push(monkeypatch):
    sent_tg: list[int] = []
    sent_wp: list[int] = []
    monkeypatch.setattr(
        dn,
        "list_discussion_participants",
        lambda *a, **k: [{"user_id": "7"}],
    )
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: set())
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "Заметка")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_telegram",
        lambda uid: True,
    )
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_web_push",
        lambda uid: False,
    )
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text: sent_tg.append(uid))
    monkeypatch.setattr(
        dn, "send_web_push", lambda uid, payload: sent_wp.append(uid) or 1
    )

    out = dn.notify_discussion_comment(
        owner="7",
        kind="local",
        item_id="1",
        author_user_id="0",
        author_name="GPT",
        body="Готово",
        is_assistant=True,
    )
    assert out == ["7"]
    assert sent_tg == [7]
    assert sent_wp == []


def test_web_push_only_skips_telegram(monkeypatch):
    sent_tg: list[int] = []
    sent_wp: list[int] = []
    monkeypatch.setattr(
        dn,
        "list_discussion_participants",
        lambda *a, **k: [{"user_id": "7"}],
    )
    monkeypatch.setattr(dp, "present_user_ids", lambda *a, **k: set())
    monkeypatch.setattr(dn, "discussion_item_title", lambda *a, **k: "Заметка")
    monkeypatch.setattr(dn, "discussion_webapp_url", lambda *a, **k: "https://x/?note=1")
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_telegram",
        lambda uid: False,
    )
    monkeypatch.setattr(
        "assistant.stores.user_prefs.discussion_notify_web_push",
        lambda uid: True,
    )
    monkeypatch.setattr(dn, "_send_telegram", lambda uid, text: sent_tg.append(uid))
    monkeypatch.setattr(
        dn, "send_web_push", lambda uid, payload: sent_wp.append(uid) or 1
    )

    out = dn.notify_discussion_comment(
        owner="7",
        kind="local",
        item_id="1",
        author_user_id="0",
        author_name="GPT",
        body="Готово",
        is_assistant=True,
    )
    assert out == ["7"]
    assert sent_tg == []
    assert sent_wp == [7]
