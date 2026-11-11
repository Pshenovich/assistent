"""Библиотеки компонентов организации: разбор мастеров макета, публикация, кто что видит."""

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
from assistant.lib import design_library
from assistant.stores import note_sheets as note_sheets_store
from assistant.stores import notes as notes_store
from assistant.stores import orgs_store

BOT_TOKEN = "123456:DESIGN-LIB"
_ids = itertools.count(981_000, 10)

BOARD = {
    "v": 1,
    "mode": "design",
    "elements": {
        "set": {"type": "frame", "name": "Buttons", "x": 0, "y": 0, "w": 400, "h": 100, "z": 1},
        "b1": {"type": "frame", "name": "Button=Default", "frame": "set", "comp": True, "x": 20, "y": 20, "w": 120, "h": 40, "z": 2,
               "style": {"fill": "#2f80ed"}},
        "t1": {"type": "text", "frame": "b1", "x": 36, "y": 30, "w": 70, "h": 20, "z": 3, "text": "Отправить", "autoW": True},
        "b2": {"type": "frame", "name": "Button=Disabled", "frame": "set", "comp": True, "x": 200, "y": 20, "w": 120, "h": 40, "z": 4},
        "card": {"type": "frame", "name": "Карточка", "comp": True, "x": 0, "y": 200, "w": 300, "h": 160, "z": 5},
        "in": {"type": "frame", "frame": "card", "inst": "b1", "x": 20, "y": 300, "w": 120, "h": 40, "z": 6},
        "in_t": {"type": "text", "frame": "in", "msrc": "t1", "x": 36, "y": 310, "w": 60, "h": 20, "z": 7, "text": "Купить", "ovr": ["text"]},
        "free": {"type": "rect", "x": 900, "y": 0, "w": 10, "h": 10, "z": 8, "text": "Секрет"},
    },
}


def test_components_labels_versions_and_subtree():
    comps = {c["id"]: c for c in design_library.components(json.dumps(BOARD))}
    assert set(comps) == {"b1", "b2", "card"}
    assert comps["b1"]["label"] == "Buttons · Button=Default" and comps["b1"]["set"] == "Buttons"
    assert comps["b1"]["set_id"] == comps["b2"]["set_id"] == "set" and comps["card"]["set_id"] == ""
    assert comps["card"]["label"] == "Карточка"
    assert [e["id"] for e in comps["card"]["elements"]] == ["card", "in", "in_t"]  # вложенный экземпляр — внутри
    assert all(e["id"] != "free" for c in comps.values() for e in c["elements"])  # остальное в макете не отдаём
    moved = json.loads(json.dumps(BOARD))
    for k in ("b1", "t1"):
        moved["elements"][k]["x"] += 500  # перенос мастера по холсту — не обновление
    assert {c["id"]: c["ver"] for c in design_library.components(json.dumps(moved))}["b1"] == comps["b1"]["ver"]
    moved["elements"]["t1"]["text"] = "Отправить заявку"
    assert {c["id"]: c["ver"] for c in design_library.components(json.dumps(moved))}["b1"] != comps["b1"]["ver"]


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
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    owner, member, outsider = next(_ids), next(_ids), next(_ids)
    org = orgs_store.create_org(owner, f"Студия {owner}")
    with orgs_store._LOCK:
        orgs_store._insert_member(org["id"], str(member), key=f"m{member}", role="member")
        orgs_store._conn().commit()
    note = notes_store.create_note(owner, "Дизайн-система", "")
    sheet = note_sheets_store.create_sheet(note, "Макет", json.dumps(BOARD), kind="board")
    empty = note_sheets_store.create_sheet(note, "Пусто", json.dumps({"v": 1, "elements": {}}), kind="board")
    return {"owner": owner, "member": member, "outsider": outsider, "org": org, "note": note, "sheet": sheet, "empty": empty,
            "client": TestClient(usage_server.app, raise_server_exceptions=False)}


def test_publish_list_and_unpublish(env):
    c, org, note, sheet = env["client"], env["org"], env["note"], env["sheet"]
    url = "/api/miniapp/design/libraries"
    body = {"note_id": note["id"], "sheet_id": str(sheet["id"]), "org_id": org["id"]}
    # Чужой документ и чужая организация — нельзя; пустой макет — нечего публиковать.
    assert c.post(url, headers=_headers(env["outsider"]), json=body).status_code == 404
    other_org = orgs_store.create_org(env["outsider"], f"Чужая {env['outsider']}")
    assert c.post(url, headers=_headers(env["owner"]), json={**body, "org_id": other_org["id"]}).status_code == 400
    assert c.post(url, headers=_headers(env["owner"]), json={**body, "sheet_id": str(env["empty"]["id"])}).status_code == 400

    r = c.post(url, headers=_headers(env["owner"]), json=body)
    assert r.status_code == 200, r.text
    lib = r.json()["library"]
    assert lib["name"] == "Дизайн-система"
    assert r.json()["published"][0]["org_id"] == org["id"]

    # Участник организации видит библиотеку с компонентами (без остального макета), посторонний — нет.
    got = c.get(url, headers=_headers(env["member"])).json()
    libs = [x for x in got["libraries"] if x["id"] == lib["id"]]
    assert libs and {x["id"] for x in libs[0]["components"]} == {"b1", "b2", "card"}
    assert "Секрет" not in json.dumps(got, ensure_ascii=False)
    assert not [x for x in c.get(url, headers=_headers(env["outsider"])).json()["libraries"] if x["id"] == lib["id"]]
    # В самой библиотеке она себе не показывается.
    own = c.get(url + f"?note_id={note['id']}&sheet_id={sheet['id']}", headers=_headers(env["owner"])).json()
    assert not [x for x in own["libraries"] if x["id"] == lib["id"]] and own["self"]["published"][0]["id"] == lib["id"]

    # Снять: рядовой участник без доступа к документу — нет, автор — да.
    assert c.delete(f"{url}/{lib['id']}", headers=_headers(env["member"])).status_code == 400
    assert c.delete(f"{url}/{lib['id']}", headers=_headers(env["owner"])).json()["ok"]
    assert not [x for x in c.get(url, headers=_headers(env["member"])).json()["libraries"] if x["id"] == lib["id"]]
