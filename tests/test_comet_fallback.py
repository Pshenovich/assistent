import pytest
import requests

from assistant.integrations import openrouter_client as oc

OK = {"choices": [{"message": {"content": "ок"}}], "usage": {}}


@pytest.fixture
def calls(monkeypatch):
    for k in ("COMET_API_KEY", "COMETA_APIKEY", "COMET_APIKEY", "COMETA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENROUTER_KEY", "or")
    monkeypatch.setenv("OPENROUTER_API_URL", "http://proxy/v1/chat/completions")
    monkeypatch.delenv("OPENROUTER_API_URLS", raising=False)
    monkeypatch.setattr(oc, "_OR_DOWN", {})
    monkeypatch.setattr(oc, "_log_usage", lambda *a, **k: None)
    log = []
    state = {"proxy": "down"}

    def post(url, key, payload, *, extra_headers, timeout):
        log.append((url, key, payload.get("model"), timeout))
        if "proxy" in url and state["proxy"] == "down":
            raise requests.ConnectTimeout("proxy timed out")
        return OK

    monkeypatch.setattr(oc, "_post_json", post)
    return log, state


def test_comet_key_alias(calls, monkeypatch):
    log, _ = calls
    monkeypatch.setenv("COMETA_APIKEY", "cm")
    assert oc.comet_api_key() == "cm"
    assert oc.openrouter_chat_completion({"model": "google/gemini-2.5-flash", "messages": []}, operation="t") == OK
    assert [(u.split("/")[2], k, m) for u, k, m, _ in log] == [
        ("proxy", "or", "google/gemini-2.5-flash"),
        ("api.cometapi.com", "cm", "gemini-2.5-flash"),
    ]
    assert log[0][3][0] <= 10  # короткий connect-таймаут у прокси


def test_proxy_down_skipped_then_retried(calls, monkeypatch):
    log, state = calls
    monkeypatch.setenv("COMET_API_KEY", "cm")
    oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    log.clear()
    oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    assert [u.split("/")[2] for u, *_ in log] == ["api.cometapi.com"]  # прокси не ждём
    # окно прошло, прокси ожил — снова основной путь
    monkeypatch.setattr(oc, "_OR_DOWN", {})
    state["proxy"] = "up"
    log.clear()
    oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    assert [u.split("/")[2] for u, *_ in log] == ["proxy"]


def test_bad_request_does_not_trip_breaker(calls, monkeypatch):
    log, state = calls
    monkeypatch.setenv("COMET_API_KEY", "cm")
    state["proxy"] = "up"

    def post(url, key, payload, *, extra_headers, timeout):
        log.append(url)
        if "proxy" in url:
            raise RuntimeError("openrouter: пустой message.content")
        return OK

    monkeypatch.setattr(oc, "_post_json", post)
    oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    assert oc._OR_DOWN == {}


def test_no_comet_raises_original(calls):
    with pytest.raises(requests.ConnectTimeout):
        oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")


def test_backup_proxy_before_comet(calls, monkeypatch):
    log, _ = calls
    monkeypatch.setenv("COMET_API_KEY", "cm")
    monkeypatch.setenv("OPENROUTER_API_URLS", "http://proxy/v1/chat/completions, http://backup/v1/chat/completions")
    assert oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t") == OK
    assert [u.split("/")[2] for u, *_ in log] == ["proxy", "backup"]
    log.clear()
    oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    assert [u.split("/")[2] for u, *_ in log] == ["backup"]  # упавший основной не ждём


def test_backup_proxy_without_comet(calls, monkeypatch):
    log, _ = calls
    monkeypatch.setenv("OPENROUTER_API_URLS", "http://proxy/v1/chat/completions,http://backup/v1/chat/completions")
    assert oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t") == OK
    assert [u.split("/")[2] for u, *_ in log] == ["proxy", "backup"]


def test_bad_request_not_retried_on_backup_proxy(calls, monkeypatch):
    log, state = calls
    state["proxy"] = "up"
    monkeypatch.setenv("OPENROUTER_API_URLS", "http://proxy/v1/chat/completions,http://backup/v1/chat/completions")

    def post(url, key, payload, *, extra_headers, timeout):
        log.append((url, key, None, timeout))
        raise RuntimeError("openrouter: пустой message.content")

    monkeypatch.setattr(oc, "_post_json", post)
    with pytest.raises(RuntimeError):
        oc.openrouter_chat_completion({"model": "openai/gpt-4o", "messages": []}, operation="t")
    assert [u.split("/")[2] for u, *_ in log] == ["proxy"]
    assert oc._OR_DOWN == {}


def test_background_limit_does_not_block_user_calls(calls, monkeypatch):
    import threading

    _, state = calls
    state["proxy"] = "up"
    monkeypatch.setattr(oc, "_BG_SEM", threading.BoundedSemaphore(1))
    sem = oc._background_semaphore()
    assert sem.acquire(timeout=1)  # единственный фоновый слот занят
    try:
        # ответ пользователю идёт без ожидания
        assert oc.openrouter_chat_completion({"model": "m", "messages": []}, operation="ask") == OK
        # фоновый ждёт слот; с коротким лимитом ожидания всё равно выполняется
        monkeypatch.setattr(oc, "_BG_WAIT_MAX_SEC", 0.05)
        assert oc.openrouter_chat_completion({"model": "m", "messages": []}, operation="chat_alerts") == OK
    finally:
        sem.release()
    # слот освобождён и не «утёк»
    assert sem.acquire(timeout=0.1)
    sem.release()
