"""POST /gpt/chat: «<ссылка> транскрибируй» в чате — ответ сразу, без модели; работа в фоне."""

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
from assistant.compat import miniapp_shims
from assistant.services import chat_url_transcribe
from assistant.stores import chat_threads

BOT_TOKEN = "123456:CHAT-URL-TRANSCRIBE"
GDRIVE = "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"
_ids = itertools.count(960_000, 10)


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
    jobs: list[tuple] = []
    monkeypatch.setattr(
        chat_url_transcribe,
        "run_chat_url_transcription",
        lambda *a, **kw: jobs.append((a, kw)),
    )

    def _no_model(*_a, **_kw):
        raise AssertionError("модель не должна вызываться")

    monkeypatch.setattr(miniapp_shims, "gpt_openrouter_answer_with_context", _no_model)
    uid = next(_ids)
    thread = chat_threads.create_thread(uid, title="Ассистент")
    return {
        "uid": uid,
        "thread": thread,
        "jobs": jobs,
        "client": TestClient(usage_server.app, raise_server_exceptions=False),
    }


def test_link_with_transcribe_starts_background_job(env):
    r = env["client"].post(
        "/api/miniapp/gpt/chat",
        headers=_headers(env["uid"]),
        json={"message": f"{GDRIVE} транскрибируй", "item_kind": "chat", "item_id": str(env["thread"]["id"])},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["answer"].startswith("Скачиваю и транскрибирую")
    assert out["transcription"] == {"url": GDRIVE, "with_summary": False}
    assert len(env["jobs"]) == 1
    args, kwargs = env["jobs"][0]
    assert args[0] == env["uid"]
    assert args[1] == str(env["thread"]["id"])
    assert args[2] == chat_url_transcribe.UrlTranscribeRequest(url=GDRIVE, with_summary=False)
    assert kwargs["prepare_audio"] is usage_server._prepare_miniapp_voice_audio


def test_other_users_thread_is_not_hijacked(env):
    other = next(_ids)
    r = env["client"].post(
        "/api/miniapp/gpt/chat",
        headers=_headers(other),
        json={"message": f"{GDRIVE} транскрибируй", "item_kind": "chat", "item_id": str(env["thread"]["id"])},
    )
    assert env["jobs"] == []
    assert r.status_code != 200
