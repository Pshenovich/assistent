"""Запасное зеркало Bot API: основное не отвечает — тот же запрос через запасное."""

import asyncio

import httpx
import pytest
import requests
from telegram.error import NetworkError, TimedOut
from telegram.request import HTTPXRequest

from assistant.lib import tg_api_failover as f

NBO = "https://tg-nbo.example"
KZ = "https://tg-kz.example"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", NBO)
    monkeypatch.setenv("TELEGRAM_BOT_API_FALLBACK_URLS", KZ)
    f._DOWN.clear()
    yield
    f._DOWN.clear()


def test_no_fallback_configured(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_API_FALLBACK_URLS")
    assert f.candidate_urls(f"{NBO}/bot1:A/getMe") == [f"{NBO}/bot1:A/getMe"]


def test_loopback_primary_never_fails_over(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081")
    assert f.api_bases() == []


def test_candidates_keep_path_and_put_down_last():
    url = f"{NBO}/file/bot1:A/voice/x.ogg"
    assert f.candidate_urls(url) == [url, f"{KZ}/file/bot1:A/voice/x.ogg"]
    f.mark_down(url, "test")
    assert f.candidate_urls(url) == [f"{KZ}/file/bot1:A/voice/x.ogg", url]
    f.mark_up(url)
    assert f.candidate_urls(url)[0] == url


def test_foreign_url_untouched():
    assert f.candidate_urls("https://t.me/x") == ["https://t.me/x"]


def _fake_httpx(monkeypatch, outcomes):
    calls = []

    async def do_request(self, url, method, request_data=None, **kw):
        calls.append(url)
        out = outcomes[len(calls) - 1]
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(HTTPXRequest, "do_request", do_request)
    return calls


def _wrapped(exc_cls, cause):
    err = exc_cls("x")
    err.__cause__ = cause
    return err


def test_ptb_connect_error_goes_to_fallback(monkeypatch):
    calls = _fake_httpx(monkeypatch, [_wrapped(NetworkError, httpx.ConnectError("no")), (200, b"{}")])
    res = asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/sendMessage", "POST"))
    assert res == (200, b"{}")
    assert calls == [f"{NBO}/bot1:A/sendMessage", f"{KZ}/bot1:A/sendMessage"]
    # Следующий запрос сразу на запасное.
    assert f.candidate_urls(f"{NBO}/bot1:A/getMe")[0].startswith(KZ)


def test_ptb_proxy_502_goes_to_fallback(monkeypatch):
    calls = _fake_httpx(monkeypatch, [(502, b"bad gateway"), (200, b"{}")])
    assert asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/getUpdates", "POST")) == (200, b"{}")
    assert len(calls) == 2


def test_ptb_send_read_timeout_not_repeated(monkeypatch):
    """sendMessage мог уже дойти: повтор на запасном дал бы дубль."""
    calls = _fake_httpx(monkeypatch, [_wrapped(TimedOut, httpx.ReadTimeout("slow"))])
    with pytest.raises(TimedOut):
        asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/sendMessage", "POST"))
    assert len(calls) == 1


def test_ptb_get_read_timeout_repeated(monkeypatch):
    calls = _fake_httpx(monkeypatch, [_wrapped(TimedOut, httpx.ReadTimeout("slow")), (200, b"{}")])
    asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/getUpdates", "POST"))
    assert len(calls) == 2


def test_ptb_telegram_error_status_not_repeated(monkeypatch):
    calls = _fake_httpx(monkeypatch, [(400, b'{"ok":false}')])
    assert asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/sendMessage", "POST"))[0] == 400
    assert len(calls) == 1


def test_ptb_all_down_raises_last(monkeypatch):
    _fake_httpx(monkeypatch, [_wrapped(NetworkError, httpx.ConnectError("a")), _wrapped(NetworkError, httpx.ConnectError("b"))])
    with pytest.raises(NetworkError):
        asyncio.run(f.FailoverRequest().do_request(f"{NBO}/bot1:A/getMe", "POST"))


def test_raw_requests_failover(monkeypatch):
    calls = []

    def fake(method, url, **kw):
        calls.append(url)
        if url.startswith(NBO):
            raise requests.ConnectionError("down")
        r = requests.Response()
        r.status_code, r._content = 200, b'{"ok":true}'
        return r

    monkeypatch.setattr(requests, "post", lambda url, **kw: fake("POST", url, **kw))
    from assistant.lib import tg_account_ids

    monkeypatch.setattr(tg_account_ids, "outgoing_params", lambda p: p)
    r = tg_account_ids.post(f"{NBO}/bot1:A/sendMessage", json={"chat_id": 5, "text": "hi"}, timeout=5)
    assert r.status_code == 200
    assert calls == [f"{NBO}/bot1:A/sendMessage", f"{KZ}/bot1:A/sendMessage"]
