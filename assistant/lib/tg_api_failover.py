"""Запасное зеркало Bot API: основное не отвечает — тот же запрос через запасное.

TELEGRAM_BOT_API_BASE_URL — основное зеркало (прокси Obuchat в Найроби),
TELEGRAM_BOT_API_FALLBACK_URLS — запасные через запятую (КЗ). Упавший адрес
пропускаем TELEGRAM_BOT_API_DOWN_SEC секунд, потом снова пробуем первым.

Повторяем только то, что точно не дошло до Telegram: нет соединения или 502/503/504
от прокси. Таймаут чтения повторяем лишь у чтений (get*, файлы) — sendMessage мог
уже уйти, и повтор дал бы дубль. Локальный telegram-bot-api (127.0.0.1) не трогаем.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from telegram.error import NetworkError
from telegram.request import HTTPXRequest, RequestData

from assistant.config import telegram_bot_api_base_url, telegram_bot_api_is_loopback

_DOWN: dict[str, float] = {}
_PROXY_DOWN_STATUSES = (502, 503, 504)


def _down_sec() -> float:
    try:
        return max(10.0, float(os.getenv("TELEGRAM_BOT_API_DOWN_SEC", "120") or "120"))
    except ValueError:
        return 120.0


def api_bases() -> list[str]:
    """Основное зеркало и запасные по порядку; пусто — без переключения."""
    primary = telegram_bot_api_base_url()
    if not primary or telegram_bot_api_is_loopback(primary):
        return []
    raw = os.getenv("TELEGRAM_BOT_API_FALLBACK_URLS", "") or ""
    extra = [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]
    bases = list(dict.fromkeys([primary, *extra]))
    return bases if len(bases) > 1 else []


def candidate_urls(url: str) -> list[str]:
    """Тот же путь на каждом зеркале: живые первыми, упавшие — в конце."""
    bases = api_bases()
    own = next((b for b in bases if url.startswith(b + "/")), None)
    if own is None:
        return [url]
    tail = url[len(own):]
    now = time.monotonic()
    live = [b for b in bases if now >= _DOWN.get(b, 0.0)]
    down = [b for b in bases if b not in live]
    return [b + tail for b in (*live, *down)]


def _base_of(url: str) -> Optional[str]:
    return next((b for b in api_bases() if url.startswith(b + "/")), None)


def mark_down(url: str, why: str) -> None:
    base = _base_of(url)
    if base is None:
        return
    if _DOWN.get(base, 0.0) <= time.monotonic():
        print(f"[tg_api_failover] {base} недоступен ({why}) — на {int(_down_sec())} с на запасное зеркало")
    _DOWN[base] = time.monotonic() + _down_sec()


def mark_up(url: str) -> None:
    base = _base_of(url)
    if base is not None:
        _DOWN.pop(base, None)


def _is_read(url: str) -> bool:
    if "/file/bot" in url:
        return True
    method = url.rsplit("/", 1)[-1].split("?", 1)[0]
    return method.lower().startswith("get")


def _not_delivered(err: BaseException, url: str) -> bool:
    """Ошибка значит, что запрос не дошёл до Telegram (или его можно безопасно повторить)."""
    import requests

    cause = err.__cause__ if isinstance(err, NetworkError) else err
    if isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout, requests.ConnectionError)):
        return True
    if isinstance(cause, (httpx.ReadTimeout, requests.ReadTimeout)):
        return _is_read(url)
    return False


class FailoverRequest(HTTPXRequest):
    """HTTPXRequest, который при недоступном зеркале повторяет запрос на запасном."""

    async def do_request(self, url: str, method: str, request_data: Optional[RequestData] = None, **kw: Any):
        urls = candidate_urls(url)
        for i, u in enumerate(urls):
            last = i + 1 == len(urls)
            try:
                status, body = await super().do_request(u, method, request_data, **kw)
            except NetworkError as e:
                if last or not _not_delivered(e, u):
                    raise
                mark_down(u, repr(e.__cause__ or e))
                continue
            if status in _PROXY_DOWN_STATUSES and not last:
                mark_down(u, f"HTTP {status}")
                continue
            mark_up(u)
            return status, body
        raise AssertionError("unreachable")


def request(http_method: str, url: str, **kwargs: Any):
    """``requests.request`` к Bot API с тем же переключением (уведомления мимо PTB)."""
    import requests

    urls = candidate_urls(url)
    if len(urls) == 1:
        return getattr(requests, http_method.lower())(url, **kwargs)
    for i, u in enumerate(urls):
        last = i + 1 == len(urls)
        try:
            resp = getattr(requests, http_method.lower())(u, **kwargs)
        except requests.RequestException as e:
            if last or not _not_delivered(e, u):
                raise
            mark_down(u, repr(e))
            continue
        if resp.status_code in _PROXY_DOWN_STATUSES and not last:
            mark_down(u, f"HTTP {resp.status_code}")
            continue
        mark_up(u)
        return resp
    raise AssertionError("unreachable")
