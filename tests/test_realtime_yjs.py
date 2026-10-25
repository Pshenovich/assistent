"""Совместная правка Yjs поверх /api/miniapp/ws: посев, рассылка, журнал, правки мимо Yjs."""

from __future__ import annotations

import base64
import pytest

from assistant.stores import note_ydoc
from assistant.stores import notes as notes_store
from tests.test_realtime import _connect, _headers, _receive_until  # noqa: F401
from tests.test_realtime import env as _base_env  # noqa: F401

def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _wait_saved(nid, expect_updates, timeout=3.0):
    """Уход из комнаты пишет журнал отдельной задачей — ждём записи."""
    import time

    end = time.time() + timeout
    while True:
        got = note_ydoc.load(nid)
        if got[0] == expect_updates or time.time() > end:
            return got
        time.sleep(0.02)


@pytest.fixture
def env(_base_env):  # noqa: F811
    return _base_env


def _join(ws, uid, nid):
    _connect(ws, uid)
    ws.send_json({"type": "sub", "note_id": nid})
    sub = ws.receive_json()
    assert sub["type"] == "subscribed" and sub["yjs"] is True
    ws.send_json({"type": "y.join", "note_id": nid})
    return _receive_until(ws, "y.state")


def test_first_joiner_seeds_second_waits_then_syncs(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a, c.websocket_connect("/api/miniapp/ws") as b:
        st_a = _join(a, env["owner"], nid)
        assert st_a["role"] == "seed"
        assert st_a["html"] == "<p>hello</p>"
        st_b = _join(b, env["member"], nid)
        assert st_b["role"] == "wait"  # второй не сеет — иначе текст удвоится
        a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"seed")})
        assert _receive_until(a, "y.ack")["seq"] == 0
        up = _receive_until(b, "y.update")
        assert base64.b64decode(up["update"]) == b"seed" and up["seq"] == 0
    # Все вышли — журнал сохранён.
    updates, base, _ = _wait_saved(nid, [b"seed"])
    assert updates == [b"seed"]
    assert base == notes_store.get_note_by_id(nid)["revision"]


def test_rejoin_gets_log(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a:
        _join(a, env["owner"], nid)
        a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"u1")})
        _receive_until(a, "y.ack")
    with c.websocket_connect("/api/miniapp/ws") as b:
        st = _join(b, env["member"], nid)
        assert st["role"] == "sync"
        assert [base64.b64decode(u) for u in st["updates"]] == [b"u1"]
        assert st["seq"] == 1


def test_outside_edit_while_closed_is_merged_not_reseeded(env):
    nid = env["note_id"]
    note_ydoc.save(nid, [b"doc"], notes_store.get_note_by_id(nid)["revision"], "<p>hello</p>")
    notes_store.update_note(env["owner"], nid, body="<p>hello</p><p>from AI</p>")  # мимо Yjs
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        st = _join(a, env["owner"], nid)
        assert st["role"] == "sync"  # журнал (с правками, которых нет в HTML) не выбрасываем
        msg = _receive_until(a, "y.reseed")
        assert msg["old_html"] == "<p>hello</p>"
        assert msg["html"] == "<p>hello</p><p>from AI</p>"


def test_legacy_log_without_base_html_reseeds(env):
    nid = env["note_id"]
    note_ydoc.save(nid, [b"old"], notes_store.get_note_by_id(nid)["revision"], None)
    notes_store.update_note(env["owner"], nid, body="<p>from AI</p>")
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        st = _join(a, env["owner"], nid)
        assert st["role"] == "seed"
        assert st["html"] == "<p>from AI</p>"


def test_outside_edit_while_open_asks_leader_to_reseed(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a, c.websocket_connect("/api/miniapp/ws") as b:
        _join(a, env["owner"], nid)
        a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"seed")})
        _receive_until(a, "y.ack")
        _join(b, env["member"], nid)
        notes_store.update_note(env["owner"], nid, body="<p>bot line</p>")
        msg = _receive_until(a, "y.reseed")  # первый в комнате — ведущий
        assert msg["old_html"] == "<p>hello</p>"
        assert msg["html"] == "<p>bot line</p>"


def test_first_in_room_is_leader_and_role_passes_on(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as b:
        with c.websocket_connect("/api/miniapp/ws") as a:
            _join(a, env["owner"], nid)
            assert _receive_until(a, "y.leader")["leader"] is True
            a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"seed")})
            _receive_until(a, "y.ack")
            _join(b, env["member"], nid)
        assert _receive_until(b, "y.leader")["leader"] is True


def test_yjs_save_skips_revision_check_and_no_reseed(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a:
        _join(a, env["owner"], nid)
        a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"seed")})
        _receive_until(a, "y.ack")
        r = c.patch(
            f"/api/miniapp/notes/local/{nid}",
            headers=_headers(env["member"]),
            json={"description": "<p>merged</p>", "expected_revision": 1, "yjs": True},
        )
        assert r.status_code == 200, r.text
        rev = r.json()["item"]["revision"]
        ev = _receive_until(a, "note.changed")
        assert ev["source"] == "yjs"
        a.send_json({"type": "ping"})
        assert _receive_until(a, "pong") == {"type": "pong"}  # y.reseed не пришёл
    got = _wait_saved(nid, [b"seed"])
    assert got[1] == rev and got[2] == "<p>merged</p>"


def test_compaction_replaces_prefix(env):
    nid = env["note_id"]
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        _join(a, env["owner"], nid)
        for i in range(3):
            a.send_json({"type": "y.update", "note_id": nid, "update": b64(b"u%d" % i)})
            _receive_until(a, "y.ack")
        a.send_json({"type": "y.compact", "note_id": nid, "update": b64(b"snap01"), "upto": 2})
        a.send_json({"type": "ping"})
        _receive_until(a, "pong")
    assert _wait_saved(nid, [b"snap01", b"u2"])[0] == [b"snap01", b"u2"]


def test_seeder_leaves_next_member_seeds(env):
    nid = env["note_id"]
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as b:
        with c.websocket_connect("/api/miniapp/ws") as a:
            assert _join(a, env["owner"], nid)["role"] == "seed"
            assert _join(b, env["member"], nid)["role"] == "wait"
        st = _receive_until(b, "y.state")
        assert st["role"] == "seed"


def test_personal_note_is_not_yjs(env):
    nid = int(notes_store.create_note(env["owner"], "Mine", "x")["id"])
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        _connect(a, env["owner"])
        a.send_json({"type": "sub", "note_id": nid})
        assert a.receive_json()["yjs"] is False
        a.send_json({"type": "y.join", "note_id": nid})
        assert _receive_until(a, "y.error")["reason"] == "not_allowed"


def test_yjs_disabled_by_env(env, monkeypatch):
    monkeypatch.setenv("REALTIME_YJS", "0")
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        _connect(a, env["owner"])
        a.send_json({"type": "sub", "note_id": env["note_id"]})
        assert a.receive_json()["yjs"] is False



# ---- холст: комната листа (note_id, sheet_id) ----

BOARD = '{"v":1,"elements":{"a":{"id":"a","type":"rect","x":0,"y":0,"w":100,"h":50,"z":1,"text":"Старт"}}}'


def _board_sheet(env):
    from assistant.stores import note_sheets

    note = notes_store.get_note_by_id(env["note_id"])
    return note_sheets.create_sheet(note, "Холст", body=BOARD, kind="board")


def _join_sheet(ws, uid, nid, sid, connect=True):
    if connect:
        _connect(ws, uid)
        ws.send_json({"type": "sub", "note_id": nid})
        assert ws.receive_json()["yjs"] is True
    ws.send_json({"type": "y.join", "note_id": nid, "sheet_id": sid})
    return _receive_until(ws, "y.state")


def test_board_room_is_separate_from_main(env):
    nid = env["note_id"]
    sid = str(_board_sheet(env)["id"])
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a, c.websocket_connect("/api/miniapp/ws") as b:
        st = _join_sheet(a, env["owner"], nid, sid)
        assert st["role"] == "seed" and st["sheet_id"] == sid
        assert '"Старт"' in st["html"]
        main = _join(b, env["member"], nid)  # основной лист — своя комната, свой посевщик
        assert main["role"] == "seed" and "sheet_id" not in main
        _join_sheet(b, env["member"], nid, sid, connect=False)
        a.send_json({"type": "y.update", "note_id": nid, "sheet_id": sid, "update": b64(b"board")})
        assert _receive_until(a, "y.ack")["sheet_id"] == sid
        up = _receive_until(b, "y.update")
        assert up["sheet_id"] == sid and base64.b64decode(up["update"]) == b"board"
    import time

    end = time.time() + 3
    while note_ydoc.load(nid, sid)[0] != [b"board"] and time.time() < end:
        time.sleep(0.02)
    assert note_ydoc.load(nid, sid)[0] == [b"board"]
    assert note_ydoc.load(nid)[0] == []  # журнал основного листа не тронут


def test_board_room_only_for_board_sheets(env):
    from assistant.stores import note_sheets

    nid = env["note_id"]
    note = notes_store.get_note_by_id(nid)
    doc = note_sheets.create_sheet(note, "Текст", body="<p>x</p>")
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        _connect(a, env["owner"])
        a.send_json({"type": "sub", "note_id": nid})
        a.receive_json()
        a.send_json({"type": "y.join", "note_id": nid, "sheet_id": str(doc["id"])})
        err = _receive_until(a, "y.error")
        assert err["reason"] == "not_found" and err["sheet_id"] == str(doc["id"])


def test_board_yjs_save_and_outside_edit(env):
    from assistant.stores import note_sheets

    nid = env["note_id"]
    sh = _board_sheet(env)
    sid = str(sh["id"])
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a:
        _join_sheet(a, env["owner"], nid, sid)
        a.send_json({"type": "y.update", "note_id": nid, "sheet_id": sid, "update": b64(b"seed")})
        _receive_until(a, "y.ack")
        # Сохранение из Yjs: без проверки ревизии, событие помечено — комната не пересевается.
        moved = BOARD.replace('"x":0', '"x":40')
        r = c.patch(
            f"/api/miniapp/notes/local/{nid}/sheets/{sid}",
            headers=_headers(env["member"]),
            json={"description": moved, "expected_revision": 1, "yjs": True},
        )
        assert r.status_code == 200, r.text
        ev = _receive_until(a, "sheets.changed")
        assert ev["source"] == "yjs"
        # Правка мимо Yjs (восстановление версии, старое приложение) — ведущему уходит слияние.
        note_sheets.update_sheet(nid, int(sid), body=BOARD)
        msg = _receive_until(a, "y.reseed")
        assert msg["sheet_id"] == sid
        assert '"x":40' in msg["old_html"] and '"x":0' in msg["html"]


def test_board_sheet_delete_closes_room(env):
    from assistant.stores import note_sheets

    nid = env["note_id"]
    sid = str(_board_sheet(env)["id"])
    with env["client"].websocket_connect("/api/miniapp/ws") as a:
        _join_sheet(a, env["owner"], nid, sid)
        a.send_json({"type": "y.update", "note_id": nid, "sheet_id": sid, "update": b64(b"seed")})
        _receive_until(a, "y.ack")
        note_sheets.delete_sheet(nid, int(sid))
        err = _receive_until(a, "y.error")
        assert err["reason"] == "not_found" and err["sheet_id"] == sid
    assert note_ydoc.load(nid, sid)[0] == []


def test_board_cursor_is_broadcast(env):
    nid = env["note_id"]
    sid = str(_board_sheet(env)["id"])
    c = env["client"]
    with c.websocket_connect("/api/miniapp/ws") as a, c.websocket_connect("/api/miniapp/ws") as b:
        for ws, uid in ((a, env["owner"]), (b, env["member"])):
            _connect(ws, uid)
            ws.send_json({"type": "sub", "note_id": nid})
            ws.receive_json()
        a.send_json({"type": "board.cursor", "note_id": nid, "sheet_id": sid, "x": 10.26, "y": -5})
        cur = _receive_until(b, "board.cursor")
        assert cur["sheet_id"] == sid and cur["x"] == 10.3 and cur["user_id"] == str(env["owner"])
