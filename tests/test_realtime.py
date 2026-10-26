"""Живые обновления: хаб, WebSocket /api/miniapp/ws, внутренний publish от бота."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import itertools
import json
import threading
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import usage_server
from assistant.bot import access_gate
from assistant.realtime import notify as realtime_notify
from assistant.realtime.hub import HUB, Connection, Hub, note_topic
from assistant.stores import note_members
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:REALTIME"
_ids = itertools.count(930_000, 10)


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.delenv("REALTIME_INTERNAL_SECRET", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_remember_principal_profile", lambda _p: None)
    owner, member, stranger = (next(_ids) + i for i in range(3))
    note = notes_store.create_note(owner, "Shared", "<p>hello</p>")
    note_members.add_member(owner, note["id"], member)
    # Один цикл событий на все соединения — как в проде (uvicorn, один процесс).
    with TestClient(usage_server.app, raise_server_exceptions=False) as client:
        yield {"owner": owner, "member": member, "stranger": stranger, "note_id": int(note["id"]), "client": client}


def _connect(ws, uid: int) -> dict:
    ws.send_json({"type": "auth", "headers": _headers(uid)})
    hello = ws.receive_json()
    assert hello == {"type": "hello", "user_id": str(uid)}
    return hello


def _receive_until(ws, kind: str, limit: int = 10) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"no {kind}")


# ---- хаб ----

def test_hub_publish_from_thread_reaches_subscriber():
    hub = Hub()

    async def scenario():
        hub.bind_loop(asyncio.get_running_loop())
        a, b = Connection("1"), Connection("2")
        hub.subscribe(a, "note:5")
        hub.subscribe(b, "note:5")
        t = threading.Thread(target=hub.publish, args=("note:5", {"type": "note.changed"}))
        t.start()
        t.join()
        got = await asyncio.wait_for(a.queue.get(), 1)
        assert json.loads(got) == {"type": "note.changed", "topic": "note:5"}
        hub.publish("note:5", {"type": "x"}, exclude=b)
        assert b.queue.qsize() == 1  # только событие из потока
        hub.drop(a)
        assert hub.subscriber_count("note:5") == 1

    asyncio.run(scenario())


def test_hub_inactive_without_loop():
    assert Hub().publish("note:1", {"type": "x"}) is False


def test_slow_client_is_closed_not_blocking():
    async def scenario():
        conn = Connection("1")
        for _ in range(500):
            conn.push("x")
        assert conn.closed

    asyncio.run(scenario())


# ---- WebSocket ----

def test_ws_rejects_bad_auth(env):
    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        ws.send_json({"type": "auth", "headers": {"Authorization": "tma garbage"}})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_ws_rejects_foreign_origin(env):
    with pytest.raises(WebSocketDisconnect):
        with env["client"].websocket_connect("/api/miniapp/ws", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()


def test_member_gets_note_changed_after_owner_patch(env):
    nid = env["note_id"]
    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, env["member"])
        ws.send_json({"type": "sub", "note_id": nid})
        assert ws.receive_json() == {"type": "subscribed", "note_id": nid, "yjs": True}
        r = env["client"].patch(
            f"/api/miniapp/notes/local/{nid}", headers=_headers(env["owner"]), json={"description": "<p>new</p>"}
        )
        assert r.status_code == 200, r.text
        ev = _receive_until(ws, "note.changed")
        assert ev["note_id"] == nid
        assert ev["revision"] == r.json()["item"]["revision"]
        assert ev["actor"] == str(env["owner"])


def test_stranger_cannot_subscribe(env):
    nid = env["note_id"]
    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, env["stranger"])
        ws.send_json({"type": "sub", "note_id": nid})
        assert ws.receive_json() == {"type": "sub_denied", "note_id": nid}
        notes_store.update_note(env["owner"], nid, body="<p>secret</p>")
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}  # событий заметки нет


def test_cursor_presence_reaches_other_member(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as owner_ws, c.websocket_connect("/api/miniapp/ws") as member_ws:
        _connect(owner_ws, env["owner"])
        _connect(member_ws, env["member"])
        for ws in (owner_ws, member_ws):
            ws.send_json({"type": "sub", "note_id": nid})
            assert ws.receive_json()["type"] == "subscribed"
        member_ws.send_json({"type": "cursor", "note_id": nid, "cursor": 7})
        ev = _receive_until(owner_ws, "presence")
        peer = next(p for p in ev["peers"] if p["user_id"] == str(env["member"]))
        assert peer["cursor"] == 7
    assert all(p["user_id"] != str(env["member"]) for p in note_members.list_peers(env["owner"], nid))


def test_sheet_and_comment_events(env):
    nid = env["note_id"]
    from assistant.stores import note_sheets, share_comments

    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, env["member"])
        ws.send_json({"type": "sub", "note_id": nid})
        ws.receive_json()
        note_sheets.create_sheet(notes_store.get_note_by_id(nid), "Лист 2")
        assert _receive_until(ws, "sheets.changed")["note_id"] == nid
        share_comments.add_comment(
            owner_user_id=env["owner"], item_kind="local", item_id=str(nid),
            author_user_id=env["owner"], author_name="O", body="hi",
        )
        assert _receive_until(ws, "comments.changed")["note_id"] == nid


def test_owner_gets_notes_changed_on_create(env):
    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, env["owner"])
        created = notes_store.create_note(env["owner"], "From bot", "x")
        ev = _receive_until(ws, "notes.changed")
        assert ev["note_id"] == created["id"]


# ---- бот → веб-сервер ----

def test_internal_publish_requires_secret(env):
    c = env["client"]
    body = {"events": [{"topic": note_topic(env["note_id"]), "event": {"type": "note.changed"}}]}
    assert c.post("/internal/realtime/publish", json=body).status_code == 403
    proxied = {realtime_notify.SECRET_HEADER: realtime_notify.internal_secret(), "X-Real-IP": "1.2.3.4"}
    assert c.post("/internal/realtime/publish", json=body, headers=proxied).status_code == 404


def test_internal_publish_delivers(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, env["member"])
        ws.send_json({"type": "sub", "note_id": nid})
        ws.receive_json()
        body = {"events": [
            {"topic": note_topic(nid), "event": {"type": "note.changed", "note_id": nid}},
            {"topic": "bogus", "event": {"type": "x"}},
        ]}
        r = c.post("/internal/realtime/publish", json=body,
                   headers={realtime_notify.SECRET_HEADER: realtime_notify.internal_secret()})
        assert r.json() == {"ok": True, "sent": 1}
        assert _receive_until(ws, "note.changed")["note_id"] == nid


def test_remote_notify_queues_when_hub_inactive(monkeypatch):
    monkeypatch.setattr(realtime_notify, "_REMOTE_ENABLED", True)
    monkeypatch.setattr(realtime_notify, "_ensure_remote_thread", lambda: None)
    monkeypatch.setattr(realtime_notify, "HUB", Hub())
    import queue as queue_mod

    q = queue_mod.Queue()
    monkeypatch.setattr(realtime_notify, "_REMOTE_QUEUE", q)
    realtime_notify.note_changed(42, revision=3)
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert items == [("note:42", {"type": "note.changed", "note_id": 42, "revision": 3})]
    realtime_notify.note_deleted(42, owner_user_id=7)
    assert ("user:7", {"type": "notes.changed", "note_id": 42}) in [q.get_nowait() for _ in range(q.qsize())]


def test_remote_notify_off_by_default(monkeypatch):
    monkeypatch.setattr(realtime_notify, "HUB", Hub())
    import queue as queue_mod

    q = queue_mod.Queue()
    monkeypatch.setattr(realtime_notify, "_REMOTE_QUEUE", q)
    realtime_notify.note_changed(42)
    assert q.qsize() == 0


def test_task_reply_from_bot_reaches_owner_socket(env, tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from assistant.bot import task_reply
    from assistant.lib import telegram_notify
    from assistant.stores import calendar_tasks

    monkeypatch.setenv("CALENDAR_TASKS_DB_PATH", str(tmp_path / "tasks.sqlite"))
    monkeypatch.setattr(calendar_tasks, "_CONN", None)
    monkeypatch.setattr(telegram_notify, "send_message", lambda *a, **k: True)
    owner, assignee = env["owner"], env["member"]
    task = calendar_tasks.create_task(
        owner, title="Прайс", start_at=datetime(2026, 10, 27, 12, 0, tzinfo=ZoneInfo("Europe/Moscow")),
        assignee_user_id=assignee, assignee_name="U", tz=ZoneInfo("Europe/Moscow"),
    )
    with env["client"].websocket_connect("/api/miniapp/ws") as ws:
        _connect(ws, owner)
        assert task_reply.post_reply(assignee, task["id"], "Сделаю завтра")
        ev = _receive_until(ws, "task.changed")
        assert ev["task_id"] == task["id"] and ev["actor"] == str(assignee)
