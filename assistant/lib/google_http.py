"""HTTP для Google API с коротким таймаутом и повтором.

Из российского ДЦ часть TCP-подключений к Google не устанавливается (~5–10%): соединение
висит без ответа. По умолчанию httplib2 ждёт 60 с — календарь «грузится» минуту. Здесь
ждём недолго и повторяем на новом соединении. Повторяем только запросы, которые безопасно
отправить дважды (чтение/правка/удаление и обновление токена); создание (POST) — нет,
иначе можно задвоить встречу.
"""

from __future__ import annotations

import os
import socket
import time
from typing import Any

import httplib2

_SAFE_METHODS = {"GET", "HEAD", "PUT", "PATCH", "DELETE", "OPTIONS"}
_TOKEN_URLS = ("https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token")


def _timeout_sec() -> float:
    try:
        return float(os.getenv("GOOGLE_HTTP_TIMEOUT_SEC", "12") or "12")
    except ValueError:
        return 12.0


def _retries() -> int:
    try:
        return max(0, int(os.getenv("GOOGLE_HTTP_RETRIES", "2") or "2"))
    except ValueError:
        return 2


def _retryable(uri: str, method: str) -> bool:
    return method.upper() in _SAFE_METHODS or str(uri).startswith(_TOKEN_URLS)


class RetryingHttp(httplib2.Http):
    def request(self, uri: Any, method: str = "GET", *args: Any, **kwargs: Any):  # type: ignore[override]
        attempts = 1 + (_retries() if _retryable(str(uri), method) else 0)
        for i in range(attempts):
            try:
                return super().request(uri, method, *args, **kwargs)
            except (socket.timeout, TimeoutError, ConnectionError) as e:
                if i + 1 >= attempts:
                    raise
                # Висящее соединение не переиспользуем — следующее пойдёт новым (часто на другой IP Google).
                self.connections.clear()
                print(f"[google_http] {method} retry {i + 1}/{attempts - 1} after {e!r}")
                time.sleep(0.3)
        raise RuntimeError("unreachable")


def authorized_http(creds: Any, timeout: float | None = None):
    from google_auth_httplib2 import AuthorizedHttp

    return AuthorizedHttp(creds, http=RetryingHttp(timeout=float(timeout or _timeout_sec())))


def build_service(name: str, version: str, creds: Any, timeout: float | None = None):
    from googleapiclient.discovery import build

    return build(name, version, http=authorized_http(creds, timeout), cache_discovery=False)
