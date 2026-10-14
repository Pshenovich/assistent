"""Чаты обсуждения заметки: видимость «Только я» / «Все участники», «Комментарии», старые сообщения."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.stores import note_chats, note_members, share_links
from assistant.stores import notes as notes_store
from assistant.stores import share_comments as sc

BOT_TOKEN = "123456:NOTE-CHATS"
_ids = itertools.count(960_000, 10)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": f"U{uid}"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    sent: list[dict] = []
    monkeypatch.setattr(usage_server, "_notify_discussion_comment", lambda **kw: sent.append(kw))
    owner, member = next(_ids), next(_ids)
    note = notes_store.create_note(owner, "Сделка", "<p>текст</p>")
    note_members.add_member(owner, note["id"], member, role="edit")
    client = TestClient(usage_server.app, raise_server_exceptions=False)
    base = f"/api/miniapp/notes/local/{note['id']}"
    return {"owner": owner, "member": member, "note": note, "c": client, "base": base, "sent": sent}


def _post(env, uid, **body):
    r = env["c"].post(env["base"] + "/comments", headers=_headers(uid), json=body)
    assert r.status_code == 200, r.text
    return r.json()["comment"]


def _feed(env, uid):
    r = env["c"].get(env["base"] + "/comments", headers=_headers(uid))
    assert r.status_code == 200, r.text
    return r.json()["comments"]


def _new_chat(env, uid, visibility="private"):
    r = env["c"].post(env["base"] + "/chats", headers=_headers(uid), json={"visibility": visibility})
    assert r.status_code == 200, r.text
    return r.json()["chat"]


def test_private_chat_is_hidden_from_other_members(env):
    chat = _new_chat(env, env["member"])
    secret = _post(env, env["member"], body="личный вопрос ИИ", chat_id=chat["id"])
    shared = _post(env, env["owner"], body="комментарий всем", chat_id=note_chats.COMMENTS_CHAT_ID)

    owner_ids = {c["id"] for c in _feed(env, env["owner"])}
    assert secret["id"] not in owner_ids and shared["id"] in owner_ids
    assert secret["id"] in {c["id"] for c in _feed(env, env["member"])}

    owner_chats = env["c"].get(env["base"] + "/chats", headers=_headers(env["owner"])).json()["chats"]
    assert [c["id"] for c in owner_chats] == [note_chats.COMMENTS_CHAT_ID]

    # Ни писать, ни отвечать, ни удалять в чужом личном чате нельзя — как будто его нет.
    h = _headers(env["owner"])
    assert env["c"].post(env["base"] + "/comments", headers=h, json={"body": "x", "chat_id": chat["id"]}).status_code == 404
    assert env["c"].post(env["base"] + "/comments", headers=h, json={"body": "x", "parent_id": secret["id"]}).status_code == 404
    assert env["c"].delete(env["base"] + f"/comments/{secret['id']}", headers=h).status_code == 404

    # Личные сообщения не попадают в превью списка заметок и не рассылаются.
    latest = sc.latest_briefs([(str(env["owner"]), "local", str(env["note"]["id"]))])
    assert latest[(str(env["owner"]), "local", str(env["note"]["id"]))]["id"] == shared["id"]
    assert [n["body"] for n in env["sent"]] == ["комментарий всем"]


def test_author_can_share_private_chat(env):
    chat = _new_chat(env, env["member"])
    msg = _post(env, env["member"], body="план", chat_id=chat["id"])
    r = env["c"].patch(env["base"] + f"/chats/{chat['id']}", headers=_headers(env["owner"]), json={"visibility": "shared"})
    assert r.status_code == 404  # владельцу заметки этот чат не виден
    r = env["c"].patch(env["base"] + f"/chats/{chat['id']}", headers=_headers(env["member"]), json={"visibility": "shared"})
    assert r.status_code == 200 and r.json()["chat"]["visibility"] == "shared"
    assert msg["id"] in {c["id"] for c in _feed(env, env["owner"])}
    r = env["c"].patch(env["base"] + f"/chats/{chat['id']}", headers=_headers(env["owner"]), json={"visibility": "private"})
    assert r.status_code == 403


def test_reply_inherits_chat_and_text_stays_in_ai_chat(env):
    chat = _new_chat(env, env["owner"], "shared")
    q = _post(env, env["owner"], body="вопрос", prefix="__gpt__", chat_id=chat["id"])
    answer = _post(env, env["owner"], body="ответ", as_role="gpt", parent_id=q["id"])
    note_text = _post(env, env["owner"], body="просто текст в чате", chat_id=chat["id"])
    assert answer["chat_id"] == chat["id"] and note_text["chat_id"] == chat["id"]


def test_public_link_never_shows_private_chats(env):
    chat = _new_chat(env, env["owner"])
    secret = _post(env, env["owner"], body="личное", chat_id=chat["id"])
    shared = _post(env, env["owner"], body="общее", chat_id=note_chats.COMMENTS_CHAT_ID)
    link = share_links.create_or_get_share(str(env["owner"]), "local", str(env["note"]["id"]), "comment", None)
    r = env["c"].get(f"/api/public/share/{link['token']}/comments")
    ids = {c["id"] for c in r.json()["comments"]}
    assert shared["id"] in ids and secret["id"] not in ids


def test_legacy_messages_are_sorted_into_chats(env):
    owner, nid = str(env["owner"]), str(env["note"]["id"])
    q = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="O", body="вопрос", prefix="__gpt__")
    a = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="GPT", author_username="gpt", body="ответ", parent_id=q["id"])
    t = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="O", body="текст без ИИ")
    quoted = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="O", body="к цитате", quote="фраза")
    reply = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="M", body="согласен", parent_id=quoted["id"])
    late = sc.add_comment(owner, "local", nid, author_user_id=owner, author_name="O", body="после сброса", prefix="__gpt__")

    by_id = {c["id"]: c["chat_id"] for c in _feed(env, env["owner"])}
    legacy = by_id[q["id"]]
    assert legacy > 0 and by_id[a["id"]] == legacy and by_id[t["id"]] == legacy and by_id[late["id"]] == legacy
    assert by_id[quoted["id"]] == by_id[reply["id"]] == note_chats.COMMENTS_CHAT_ID
    chats = env["c"].get(env["base"] + "/chats", headers=_headers(env["owner"])).json()["chats"]
    assert chats[0]["visibility"] == "shared"

    # Сброс контекста из браузера → отдельный чат, один раз.
    r = env["c"].post(env["base"] + f"/chats/{legacy}/split", headers=_headers(env["owner"]), json={"after_id": t["id"]})
    new_chat = r.json()["chat"]
    assert new_chat and new_chat["id"] != legacy
    by_id = {c["id"]: c["chat_id"] for c in _feed(env, env["owner"])}
    assert by_id[late["id"]] == new_chat["id"] and by_id[q["id"]] == legacy
    again = env["c"].post(env["base"] + f"/chats/{legacy}/split", headers=_headers(env["owner"]), json={"after_id": q["id"]})
    assert again.json()["chat"] is None


def test_attached_chat_context_respects_visibility(env):
    from assistant.nlu.ask_context import pack_attached_notes

    chat = _new_chat(env, env["member"])
    _post(env, env["member"], body="секретный план", chat_id=chat["id"])
    _post(env, env["owner"], body="общий комментарий", chat_id=note_chats.COMMENTS_CHAT_ID)
    nid = env["note"]["id"]
    assert "секретный план" in pack_attached_notes(env["member"], [f"chat:{nid}:{chat['id']}"])
    assert pack_attached_notes(env["owner"], [f"chat:{nid}:{chat['id']}"]) == ""
    assert "общий комментарий" in pack_attached_notes(env["owner"], [f"chat:{nid}:-1"])
    assert pack_attached_notes(next(_ids), [f"chat:{nid}:-1"]) == ""  # не участник заметки


def test_read_marks_are_per_user_and_shared_across_devices(env):
    old = _post(env, env["owner"], body="до чатов", chat_id=note_chats.COMMENTS_CHAT_ID)
    chats = env["c"].get(env["base"] + "/chats", headers=_headers(env["member"])).json()["chats"]
    comments = [c for c in chats if c["id"] == note_chats.COMMENTS_CHAT_ID][0]
    assert comments["last_seen_id"] == old["id"]  # старое — уже прочитано

    new = _post(env, env["owner"], body="новое", chat_id=note_chats.COMMENTS_CHAT_ID)
    r = env["c"].post(env["base"] + "/chats/-1/read", headers=_headers(env["member"]), json={"last_id": new["id"] + 100})
    assert r.status_code == 200 and r.json()["last_seen_id"] == new["id"]  # не дальше последнего сообщения
    # «Другое устройство» того же участника видит ту же отметку; у владельца — своя.
    seen = {c["id"]: c["last_seen_id"] for c in env["c"].get(env["base"] + "/chats", headers=_headers(env["member"])).json()["chats"]}
    assert seen[note_chats.COMMENTS_CHAT_ID] == new["id"]
    seen_owner = {c["id"]: c["last_seen_id"] for c in env["c"].get(env["base"] + "/chats", headers=_headers(env["owner"])).json()["chats"]}
    assert seen_owner[note_chats.COMMENTS_CHAT_ID] == new["id"]  # точка отсчёта владельца — момент первого открытия

    secret = _new_chat(env, env["owner"])
    r = env["c"].post(env["base"] + f"/chats/{secret['id']}/read", headers=_headers(env["member"]), json={"last_id": 1})
    assert r.status_code == 404
