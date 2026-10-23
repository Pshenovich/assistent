"""Выгрузка заметки: PDF / Word / TXT, в чат или на устройство по одноразовой ссылке."""

from __future__ import annotations

import hashlib
import hmac
import io
import itertools
import json
import time
import zipfile
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.lib import note_export
from assistant.lib import telegram_notify
from assistant.stores import notes as notes_store

BOT_TOKEN = "123456:NOTE-EXPORT"
_ids = itertools.count(980_000, 10)

HTML = (
    "<h2>План</h2><p>Обычный <strong>жирный</strong>, <a href=\"https://ya.ru\">ссылка</a>.</p>"
    "<ul><li><p>Пункт</p><ul><li>Вложенный</li></ul></li></ul>"
    "<ul class=\"note-task-list\"><li checked><p>Сделано</p></li><li><p>Не сделано</p></li></ul>"
    "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
)


def _row(text: str, topic: str = "Тест") -> dict:
    return {
        "id": 7,
        "operation": "note",
        "ts_utc": "2026-10-23T10:00:00",
        "raw_usage_json": json.dumps({"text": text, "meta": {"main_topic": topic}}),
        "label": "",
    }


def test_txt_keeps_structure():
    data, fname, _cap, mime = note_export.build_export(_row(HTML), "txt")
    text = data.decode("utf-8")
    assert fname.endswith(".txt") and mime.startswith("text/plain")
    assert text.startswith("Тест\nДата: 2026-10-23 10:00")
    assert "ссылка (https://ya.ru)" in text
    assert "• Пункт\n  • Вложенный" in text
    assert "☑ Сделано" in text and "☐ Не сделано" in text
    assert "A | B\n1 | 2" in text


def test_docx_is_valid_package():
    data, fname, _cap, mime = note_export.build_export(_row(HTML), "docx")
    assert fname.endswith(".docx") and "wordprocessingml" in mime
    z = zipfile.ZipFile(io.BytesIO(data))
    doc = z.read("word/document.xml").decode("utf-8")
    rels = z.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "[Content_Types].xml" in z.namelist()
    assert 'w:val="Heading2"' in doc and "<w:b/>" in doc and "<w:tbl>" in doc
    assert "Вложенный" in doc and "https://ya.ru" in rels


def test_markdown_body_and_title_not_repeated():
    row = _row("## Итоги\n- **Решили** сделать", topic="")
    text = note_export.build_export(row, "txt")[0].decode("utf-8")
    assert text.startswith("Итоги\n")
    assert text.count("Итоги") == 1
    assert "• Решили сделать" in text


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        note_export.build_export(_row(HTML), "exe")


def _headers(uid: int) -> dict[str, str]:
    fields = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "U"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return {"Authorization": "tma " + urlencode(fields)}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.delenv("MINIAPP_DEV_MODE", raising=False)
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    monkeypatch.setattr(usage_server, "_remember_principal_profile", lambda _p: None)
    return TestClient(usage_server.app, raise_server_exceptions=False)


def test_export_to_device_and_chat(client, monkeypatch):
    owner, stranger = next(_ids), next(_ids)
    note = notes_store.create_note(owner, "Отчёт", HTML)
    url = f"/api/miniapp/notes/local/{note['id']}/export"

    r = client.post(url, headers=_headers(owner), json={"format": "docx", "to": "device"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["filename"].endswith(".docx")
    f = client.get(body["url"])
    assert f.status_code == 200
    assert "attachment" in f.headers["content-disposition"] and "filename*=UTF-8''" in f.headers["content-disposition"]
    zipfile.ZipFile(io.BytesIO(f.content))  # настоящий docx

    r = client.post(url, headers=_headers(owner), json={"format": "txt", "to": "device", "inline": True})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert ".txt" in r.headers["content-disposition"]
    assert "План" in r.content.decode("utf-8")

    sent = []
    monkeypatch.setattr(telegram_notify, "send_user_document", lambda uid, data, **kw: sent.append((uid, kw)) or True)
    r = client.post(url, headers=_headers(owner), json={"format": "txt", "to": "chat"})
    assert r.status_code == 200
    assert sent and sent[0][0] == owner and sent[0][1]["filename"].endswith(".txt") and sent[0][1]["mime"] == "text/plain"

    assert client.post(url, headers=_headers(stranger), json={"format": "pdf", "to": "device"}).status_code == 404
    assert client.post(url, headers=_headers(owner), json={"format": "exe", "to": "device"}).status_code == 400
    assert client.get("/api/miniapp/notes/export/file/nope").status_code == 404
