"""Чат с Лео на лендинге /orbita: ответы, антиспам и заявки (без реальных LLM и Telegram)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import usage_server
from assistant.services import site_chat


@pytest.fixture()
def client(monkeypatch):
    site_chat._HITS.clear()
    calls: list[dict] = []

    def fake_completion(payload, *, operation, timeout=120):
        calls.append(payload)
        body = {"reply": "Orbita помнит договорённости.", "suggestions": ["Сколько стоит?", "Как начать?"], "lead": False}
        return {"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]}

    monkeypatch.setattr("assistant.integrations.openrouter_client.openrouter_chat_completion", fake_completion)
    c = TestClient(usage_server.app)
    c.llm_calls = calls  # type: ignore[attr-defined]
    return c


def _ask(c, text, session="sess-test-0001", ip="10.0.0.1", **extra):
    body = {"session": session, "messages": [{"role": "user", "content": text}], **extra}
    return c.post("/api/orbita/chat", json=body, headers={"X-Real-IP": ip})


def test_chat_answers_from_model_and_keeps_history(client):
    r = _ask(client, "Что умеет Orbita?")
    assert r.status_code == 200
    data = r.json()
    assert data["reply"] == "Orbita помнит договорённости."
    assert data["suggestions"] == ["Сколько стоит?", "Как начать?"]
    assert data["lead"] is False
    sent = client.llm_calls[-1]
    assert sent["messages"][0]["role"] == "system"
    assert "БАЗА ЗНАНИЙ" in sent["messages"][0]["content"] and "Solo" in sent["messages"][0]["content"]
    assert sent["max_tokens"] <= 500
    tail = site_chat.session_tail("sess-test-0001")
    assert [m["role"] for m in tail[-2:]] == ["user", "assistant"]


def test_honeypot_skips_model(client):
    r = _ask(client, "buy cheap", website="http://spam.example")
    assert r.status_code == 200
    assert client.llm_calls == []


def test_rejects_long_bad_session_and_links(client):
    assert _ask(client, "а" * 700).status_code == 400
    assert _ask(client, "привет", session="bad").status_code == 400
    assert _ask(client, "http://a.ru http://b.ru http://c.ru").status_code == 400
    assert client.llm_calls == []


def test_ip_rate_limit(client):
    codes = [_ask(client, f"вопрос {i}", session=f"sess-rate-{i:04d}", ip="10.0.0.9").status_code for i in range(10)]
    assert codes[:8] == [200] * 8
    assert codes[8] == 429
    assert _ask(client, "другой ip", session="sess-other-01", ip="10.0.0.10").status_code == 200


def test_session_limit(client, monkeypatch):
    monkeypatch.setattr(site_chat, "SESSION_LIMIT", 2)
    s = "sess-limit-0001"
    assert _ask(client, "1", session=s, ip="10.1.0.1").status_code == 200
    assert _ask(client, "2", session=s, ip="10.1.0.2").status_code == 200
    r = _ask(client, "3", session=s, ip="10.1.0.3")
    assert r.status_code == 429
    assert "заявку" in r.json()["detail"]


def test_daily_limit(client, monkeypatch):
    monkeypatch.setenv("SITE_CHAT_DAILY_LIMIT", "0")
    assert _ask(client, "привет", session="sess-daily-0001", ip="10.2.0.1").status_code == 429
    assert client.llm_calls == []


def test_parse_reply_fallbacks():
    assert site_chat._parse_reply("просто текст")["reply"] == "просто текст"
    parsed = site_chat._parse_reply('```json\n{"reply": "ок", "suggestions": ["a","b","c","d"], "lead": true}\n```')
    assert parsed == {"reply": "ок", "suggestions": ["a", "b", "c"], "lead": True}


def test_lead_saved_and_sent_to_operators(client, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "111,222")
    monkeypatch.setattr("assistant.lib.telegram_notify.send_message", lambda uid, text, **kw: sent.append((uid, text)) or True)
    _ask(client, "Хотим внедрить на 30 человек", session="sess-lead-0001", ip="10.3.0.1")
    body = {"session": "sess-lead-0001", "name": "Анна <b>", "contact": "+7 999 123-45-67", "company": "Альфа", "source": "chat"}
    r = client.post("/api/orbita/lead", json=body, headers={"X-Real-IP": "10.3.0.1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert {uid for uid, _ in sent} == {111, 222}
    text = sent[0][1]
    assert "Анна &lt;b&gt;" in text and "+7 999 123-45-67" in text and "Хотим внедрить" in text
    # тот же контакт сразу ещё раз — без повторного уведомления
    assert client.post("/api/orbita/lead", json=body, headers={"X-Real-IP": "10.3.0.2"}).status_code == 200
    assert len(sent) == 2


def test_lead_validation_and_honeypot(client, monkeypatch):
    sent: list = []
    monkeypatch.setattr("assistant.lib.telegram_notify.send_message", lambda *a, **k: sent.append(a) or True)
    bad = client.post("/api/orbita/lead", json={"name": "A", "contact": "не контакт!"}, headers={"X-Real-IP": "10.4.0.1"})
    assert bad.status_code == 400
    assert client.post("/api/orbita/lead", json={"name": "", "contact": "a@b.ru"}).status_code == 400
    trap = client.post("/api/orbita/lead", json={"name": "Bot", "contact": "a@b.ru", "website": "x"})
    assert trap.status_code == 200 and sent == []


def test_valid_contact():
    for ok in ("+7 (999) 123-45-67", "anna@alfa.ru", "@anna_k", "t.me/anna_k", "anna_k"):
        assert site_chat.valid_contact(ok), ok
    for bad in ("12", "+7 999", "a b c", "<script>"):
        assert not site_chat.valid_contact(bad), bad


def test_feature_request_needs_text_not_contact(client, monkeypatch):
    sent: list[tuple] = []
    monkeypatch.setenv("TELEGRAM_ACCESS_APPROVER_IDS", "111")
    monkeypatch.setattr("assistant.lib.telegram_notify.send_message", lambda uid, text, **kw: sent.append((uid, text)) or True)
    short = client.post("/api/orbita/lead", json={"source": "feature", "note": "да"}, headers={"X-Real-IP": "10.5.0.1"})
    assert short.status_code == 400
    ok = client.post("/api/orbita/lead", json={"source": "feature", "note": "Интеграция с amoCRM"}, headers={"X-Real-IP": "10.5.0.2"})
    assert ok.status_code == 200
    assert len(sent) == 1 and "Пожелание функции" in sent[0][1] and "amoCRM" in sent[0][1]
