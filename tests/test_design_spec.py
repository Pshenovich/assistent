"""Макет для разработчика: спецификация фрейма, HTML/CSS, MCP по ссылке разработчика."""

from __future__ import annotations

import base64
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
from assistant.lib import design_mcp, design_spec
from assistant.stores import design_links
from assistant.stores import note_sheets as note_sheets_store
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:DESIGN-SPEC"
_ids = itertools.count(970_000, 10)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20

BOARD = {
    "v": 1,
    "mode": "design",
    "elements": {
        "s": {"type": "frame", "name": "Главный экран", "x": 100, "y": 50, "w": 393, "h": 852, "z": 1,
              "style": {"fill": "#ffffff", "radius": 24}},
        "row": {"type": "frame", "name": "Кнопки", "frame": "s", "x": 116, "y": 70, "w": 300, "h": 48, "z": 2,
                "layout": "h 8 8 16 8 16 center between none", "style": {"fill": "#f2f2f7", "radii": "8 8 0 0"}},
        "b1": {"type": "rect", "name": "OK", "frame": "row", "x": 132, "y": 78, "w": 80, "h": 32, "z": 3, "grow": "w",
               "text": "Да", "style": {"fill": "#305bab", "strokeWidth": 1, "stroke": "#000000", "strokeAlign": "inside",
                                     "textColor": "#ffffff", "fontSize": 14, "fontWeight": 600, "shadow": "0 2 8 #00000033"}},
        "t1": {"type": "text", "frame": "row", "x": 260, "y": 80, "w": 60, "h": 20, "z": 4, "autoW": True, "text": "Нет <b>",
               "style": {"fontSize": 14, "textColor": "#1b2559", "fontFamily": "Inter", "lineHeight": 20, "letterSpacing": -2}},
        "img": {"type": "image", "frame": "s", "x": 120, "y": 200, "w": 100, "h": 60, "z": 5,
                "src": "data:image/png;base64," + base64.b64encode(PNG).decode()},
        "gone": {"type": "rect", "frame": "s", "x": 120, "y": 300, "w": 10, "h": 10, "z": 6, "hidden": True},
        "loose": {"type": "rect", "x": 900, "y": 900, "w": 10, "h": 10, "z": 7},
        "s2": {"type": "frame", "name": "Профиль", "x": 600, "y": 50, "w": 393, "h": 852, "z": 8},
    },
}
BODY = json.dumps(BOARD, ensure_ascii=False)


def test_frames_listed_in_reading_order():
    frames = design_spec.list_frames(BODY)
    assert [f["id"] for f in frames] == ["s", "s2"]
    assert frames[0]["name"] == "Главный экран" and frames[0]["nodes"] == 4  # скрытый не считается
    assert design_spec.find_frame(BODY, "профиль")["id"] == "s2"
    assert design_spec.find_frame(BODY, "нет такого") is None


def test_spec_relative_coords_layout_and_tokens():
    spec = design_spec.frame_spec(BODY, "s")
    root = spec["frame"]
    assert (root["x"], root["y"], root["w"], root["radius"]) == (0, 0, 393, 24)
    ids = [c["id"] for c in root["children"]]
    assert "gone" not in ids and "loose" not in ids
    row = next(c for c in root["children"] if c["id"] == "row")
    assert (row["x"], row["y"]) == (16, 20)
    assert row["layout"]["mode"] == "row" and row["layout"]["justify"] == "space-between"
    assert row["layout"]["padding"] == [8, 16, 8, 16] and row["radius"] == [8, 8, 0, 0]
    b1, t1 = row["children"]
    assert b1["sizing"]["w"] == "fill" and b1["stroke"]["align"] == "inside"
    assert b1["text"]["fontWeight"] == 600 and b1["effects"]["shadow"]["color"] == "rgba(0, 0, 0, 0.2)"
    assert t1["sizing"] == {"w": "hug", "h": "hug"} and t1["text"]["letterSpacing"] == "-2%"
    assert spec["assets"][0]["id"] == "img" and spec["assets"][0]["mime"] == "image/png"
    colors = [c["value"] for c in spec["tokens"]["colors"]]
    assert "#305bab" in colors and "#1b2559" in colors
    assert "Кнопки" in design_spec.spec_outline(spec)


def test_code_flex_for_autolayout_and_escaped_text():
    code = design_spec.frame_code(BODY, "s")
    css, markup = code["css"], code["html"]
    assert ".glavnyy-ekran {" in css and "position: relative" in css
    assert "display: flex" in css and "justify-content: space-between" in css and "padding: 8px 16px" in css
    assert "flex: 1 1 0" in css  # «заполнить» по главной оси
    assert "border: 1px solid #000000" in css and "box-shadow: 0px 2px 8px rgba(0, 0, 0, 0.2)" in css
    assert "letter-spacing: -0.02em" in css and "white-space: pre" in css
    assert "Нет &lt;b&gt;" in markup and 'src="assets/img.png"' in markup
    assert code["fonts"] == ["Inter"] and "fonts.googleapis.com" in code["font_link"]
    page = design_spec.frame_page(BODY, "s")
    assert page.startswith("<!doctype html>") and "data:image/png;base64," in page


def test_frame_svg_is_cropped_to_frame():
    svg = design_spec.frame_svg(BODY, "s")
    assert 'viewBox="100.0 50.0 393.0 852.0"' in svg and "Главный экран" not in svg


def _src(previews=None):
    return design_mcp.DesignSource(title="Тест", load_body=lambda: BODY, load_preview=lambda fid: (previews or {}).get(fid))


def _call(src, name, args=None):
    r = design_mcp.handle(src, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
    return r["result"]


def test_mcp_protocol_basics():
    src = _src()
    init = design_mcp.handle(src, {"jsonrpc": "2.0", "id": 0, "method": "initialize",
                                   "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    assert init["result"]["protocolVersion"] == "2025-03-26" and "tools" in init["result"]["capabilities"]
    assert design_mcp.handle_payload(src, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = [t["name"] for t in design_mcp.handle(src, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
    assert names == ["list_frames", "get_frame_image", "get_frame_spec", "get_frame_code", "get_asset"]
    bad = design_mcp.handle(src, {"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert bad["error"]["code"] == -32601


def test_mcp_tools():
    src = _src({"s": PNG})
    frames = json.loads(_call(src, "list_frames")["content"][0]["text"])
    assert [f["id"] for f in frames["frames"]] == ["s", "s2"]
    img = _call(src, "get_frame_image", {"frame": "Главный экран"})["content"][0]
    assert img["type"] == "image" and base64.b64decode(img["data"]) == PNG
    sketch = _call(src, "get_frame_image", {"frame": "s2"})["content"][0]
    assert sketch["type"] == "text" and "<svg" in sketch["text"]
    spec = _call(src, "get_frame_spec", {"frame": "s", "node": "row"})["content"]
    assert json.loads(spec[1]["text"])["frame"]["id"] == "row"
    assert "```css" in _call(src, "get_frame_code", {"frame": "s"})["content"][0]["text"]
    asset = _call(src, "get_asset", {"id": "img.png"})["content"][0]
    assert asset["mimeType"] == "image/png" and base64.b64decode(asset["data"]) == PNG
    miss = _call(src, "get_frame_spec", {"frame": "xxx"})
    assert miss["isError"] and "Главный экран" in miss["content"][0]["text"]


# ── HTTP: ссылка разработчика, MCP, превью ─────────────────────────────────────────

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
    owner = next(_ids)
    note = notes_store.create_note(owner, "Приложение", "")
    sheet = note_sheets_store.create_sheet(note, "Макет", BODY, kind="board")
    base = f"/api/miniapp/notes/local/{note['id']}/sheets/{sheet['id']}"
    return {"owner": owner, "base": base, "client": TestClient(usage_server.app, raise_server_exceptions=False)}


def test_dev_link_mcp_and_revoke(env):
    c, h, base = env["client"], _headers(env["owner"]), env["base"]
    assert c.get(base + "/design-link", headers=h).json()["link"] is None
    assert c.post(base + "/design-link", headers=_headers(env["owner"] + 1)).status_code == 404  # чужая заметка
    link = c.post(base + "/design-link", headers=h).json()["link"]
    tok = link["token"]
    assert link["mcp_url"].endswith("/mcp/design/" + tok)
    assert c.post(base + "/design-link", headers=h).json()["link"]["token"] == tok  # одна ссылка на лист

    r = c.post("/mcp/design/" + tok, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": "list_frames", "arguments": {}}})
    assert r.status_code == 200 and "Главный экран" in r.json()["result"]["content"][0]["text"]
    assert c.post("/mcp/design/" + tok, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    assert c.get("/mcp/design/" + tok).status_code == 405
    assert "flex" in c.get(f"/api/design/{tok}?format=code&frame=s").json()["css"]
    assert c.get(f"/api/design/{tok}?format=asset&asset=img").content == PNG
    assert c.get(f"/api/design/{tok}?format=png&frame=s").status_code == 404

    png64 = base64.b64encode(PNG).decode()
    assert c.post(base + "/design/preview", headers=h, json={"frame": "s", "sig": "abc", "png": png64}).json()["ok"]
    assert c.post(base + "/design/preview", headers=h, json={"frame": "s", "png": "bm90IHBuZw=="}).status_code == 400
    assert c.get(base + "/design-link", headers=h).json()["sigs"] == {"s": "abc"}
    assert c.get(f"/api/design/{tok}?format=png&frame=s").content == PNG

    assert c.delete(base + "/design-link", headers=h).json()["ok"]
    assert c.post("/mcp/design/" + tok, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404
    assert design_links.resolve(tok) is None
