"""HTTP-запросы по адресам от пользователя (ссылка на запись, вебхук, RSS): только в публичный интернет.

Запрос с сервера на 127.0.0.1, во внутреннюю сеть или на метаданные облака (169.254.169.254) — SSRF:
так можно достучаться до /dashboard, embed-сервера, прокси LLM.

- До соединения имя резолвится и проверяется: внутренний адрес не получает даже попытки
  подключения (иначе по «отклонено / таймаут» можно сканировать порты).
- После соединения проверяется адрес самого сокета — DNS rebinding (второй ответ DNS) не проходит.
- Редиректы — вручную, каждый шаг через обе проверки.
- Прокси из окружения не используем: иначе проверялся бы адрес прокси.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

MAX_REDIRECTS = 5


class BlockedAddress(requests.ConnectionError):
    """Адрес внутри сети сервера — запрос не отправлен."""


def is_public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(raw).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_global) and not ip.is_multicast


def _checked(sock: Any) -> Any:
    try:
        peer = sock.getpeername()[0]
    except OSError:
        peer = ""
    if not is_public_ip(peer):
        sock.close()
        raise BlockedAddress("Адрес во внутренней сети — такие ссылки не открываем.")
    return sock


class _GuardedHTTPConnection(HTTPConnection):
    def _new_conn(self) -> Any:
        return _checked(super()._new_conn())


class _GuardedHTTPSConnection(HTTPSConnection):
    def _new_conn(self) -> Any:
        return _checked(super()._new_conn())


class _GuardedHTTPPool(HTTPConnectionPool):
    ConnectionCls = _GuardedHTTPConnection


class _GuardedHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _GuardedHTTPSConnection


class _GuardedAdapter(HTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _GuardedHTTPPool, "https": _GuardedHTTPSPool}


def session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.max_redirects = MAX_REDIRECTS
    adapter = _GuardedAdapter()
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


def check_public_host(host: str, port: int) -> None:
    """Все адреса имени — публичные, иначе BlockedAddress (и для не-HTTP: свой IMAP/SMTP-сервер)."""
    try:
        infos = socket.getaddrinfo(str(host or "").strip(), int(port), type=socket.SOCK_STREAM)
    except (socket.gaierror, ValueError, UnicodeError) as e:
        raise requests.ConnectionError("Не удалось найти адрес.") from e
    if not infos or any(not is_public_ip(info[4][0]) for info in infos):
        raise BlockedAddress("Адрес во внутренней сети — такие ссылки не открываем.")


def _precheck(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise requests.exceptions.InvalidURL("Нужна ссылка http:// или https://")
    try:
        port = p.port or (443 if p.scheme == "https" else 80)
    except ValueError as e:
        raise requests.exceptions.InvalidURL("Нужна ссылка http:// или https://") from e
    check_public_host(p.hostname, port)


def _blocked_in_chain(exc: BaseException) -> bool:
    seen = 0
    cur: BaseException | None = exc
    while cur is not None and seen < 10:
        if isinstance(cur, BlockedAddress) or any(isinstance(a, BlockedAddress) for a in getattr(cur, "args", ())):
            return True
        cur = cur.__cause__ or cur.__context__
        seen += 1
    return False


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """Как requests.request, но только в публичные адреса; редиректы — с проверкой каждого шага.

    stream=True — ответ закрыть самому (with).
    """
    u = str(url or "").strip()
    kwargs.setdefault("timeout", 30)
    kwargs.pop("allow_redirects", None)
    method = method.upper()
    s = session()
    try:
        for _ in range(MAX_REDIRECTS + 1):
            _precheck(u)
            try:
                resp = s.request(method, u, allow_redirects=False, **kwargs)
            except requests.RequestException as e:
                if _blocked_in_chain(e):
                    raise BlockedAddress("Адрес во внутренней сети — такие ссылки не открываем.") from e
                raise
            if not resp.is_redirect:
                if not kwargs.get("stream"):
                    s.close()
                return resp
            u = urljoin(u, resp.headers.get("location") or "")
            resp.close()
            if resp.status_code == 303 or (resp.status_code in (301, 302) and method == "POST"):
                method = "GET"
                for k in ("data", "json", "files"):
                    kwargs.pop(k, None)
        raise requests.TooManyRedirects("Слишком много перенаправлений.")
    except BaseException:
        s.close()
        raise


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)
