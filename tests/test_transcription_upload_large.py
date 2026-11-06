"""Загрузка записи на транскрибацию: файл идёт через диск (не целиком в памяти), лимит — TRANSCRIPTION_UPLOAD_MAX_MB."""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import os
import time
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.bot import access_gate
from assistant.integrations import transcribe as obu

BOT_TOKEN = "123456:BIG-UPLOAD"
_ids = itertools.count(970_000, 10)


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
    monkeypatch.setenv("CREDITS_ENFORCE", "0")
    monkeypatch.setattr(access_gate, "miniapp_access_message", lambda **_: (True, ""))
    sent: list[dict] = []

    def fake_transcribe_file(path, filename, **kw):
        with open(path, "rb") as f:
            sent.append({"path": str(path), "filename": filename, "sha": hashlib.sha256(f.read()).hexdigest()})
        return obu.TranscribeResult(plain_text="расшифровка", formatted_text="расшифровка", job_id="j1")

    def no_bytes(*a, **k):
        raise AssertionError("запись не должна уходить в транскрибацию из памяти")

    monkeypatch.setattr(obu, "transcribe_file", fake_transcribe_file)
    monkeypatch.setattr(obu, "transcribe_bytes", no_bytes)
    return {"client": TestClient(usage_server.app, raise_server_exceptions=False), "sent": sent}


def test_upload_goes_through_disk_and_temp_is_removed(env):
    uid = next(_ids)
    payload = os.urandom(3 * 1024 * 1024 + 17)
    r = env["client"].post(
        "/api/miniapp/notes/transcriptions/upload",
        headers=_headers(uid),
        files={"file": ("Созвон.mp4", payload, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    assert env["sent"][0]["sha"] == hashlib.sha256(payload).hexdigest()
    assert env["sent"][0]["filename"] == "Созвон.mp4"
    assert not os.path.exists(env["sent"][0]["path"])  # временный каталог удалён


def test_upload_over_limit_rejected(env, monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_UPLOAD_MAX_MB", "1")
    r = env["client"].post(
        "/api/miniapp/notes/transcriptions/upload",
        headers=_headers(next(_ids)),
        files={"file": ("big.mp4", b"x" * (1024 * 1024 + 1), "video/mp4")},
    )
    assert r.status_code == 413
    assert "1 МБ" in r.json()["detail"]
    assert env["sent"] == []


def test_default_limit_is_500mb(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_UPLOAD_MAX_MB", raising=False)
    assert usage_server._transcription_upload_max_bytes() == 500 * 1024 * 1024


def test_upload_makes_one_document(env):
    # событие расхода связано с документом: перенос журнала не делает второй («audio….m4a» рядом со встречей)
    from assistant.lib import usage_store
    from assistant.services.transcription_notes_migrate import migrate_user_if_needed
    from assistant.stores import notes as notes_store

    uid = next(_ids)
    r = env["client"].post(
        "/api/miniapp/notes/transcriptions/upload",
        headers=_headers(uid),
        files={"file": ("audio1.m4a", b"x" * 2048, "audio/mp4")},
        data={"meeting": json.dumps({"id": "ev1", "summary": "Проектный синк"})},
    )
    assert r.status_code == 200, r.text
    assert migrate_user_if_needed(uid)["created"] == 0
    assert [n["title"] for n in notes_store.list_transcription_notes(uid)] == ["Проектный синк"]
    assert usage_store.user_journal_entries(str(uid), limit=20) == []
