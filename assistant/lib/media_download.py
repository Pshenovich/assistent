"""Скачивание медиа по прямой HTTP(S)-ссылке."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

import requests

from assistant.config import URL_MAX_DOWNLOAD_BYTES

_MEDIA_CONTENT_PREFIXES = ("audio/", "video/", "application/octet-stream")
_HTML_MARKERS = ("text/html", "application/xhtml")
_YANDEX_DISK_API = "https://cloud-api.yandex.net/v1/disk/public/resources/download"
_YANDEX_DISK_HOSTS = frozenset(
    {
        "disk.yandex.ru",
        "disk.yandex.com",
        "disk.yandex.kz",
        "yadi.sk",
    }
)


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";\n]+)"?', header, re.I)
    if m:
        return unquote(m.group(1).strip().strip('"'))
    return None


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path or ""
    name = unquote(path.rsplit("/", 1)[-1]).strip()
    if name and "." in name:
        return name
    qs = parse_qs(urlparse(url).query or "")
    for key in ("filename", "name"):
        vals = qs.get(key) or []
        if vals and "." in str(vals[0]):
            return unquote(str(vals[0]).strip())
    return "media.bin"


def is_yandex_disk_public_url(url: str) -> bool:
    host = (urlparse((url or "").strip()).hostname or "").lower()
    if host not in _YANDEX_DISK_HOSTS:
        return False
    path = (urlparse(url).path or "").strip("/")
    return bool(re.match(r"^[id]/", path))


def resolve_yandex_disk_download_url(url: str) -> tuple[str, str | None]:
    """Публичная ссылка Яндекс.Диска → прямая ссылка на скачивание."""
    u = (url or "").strip()
    if not is_yandex_disk_public_url(u):
        raise ValueError("Не похоже на публичную ссылку Яндекс.Диска.")
    r = requests.get(
        _YANDEX_DISK_API,
        params={"public_key": u},
        timeout=60,
    )
    if r.status_code >= 400:
        raise ValueError(
            "Не удалось получить файл с Яндекс.Диска. "
            "Проверьте, что ссылка публичная и доступна без входа."
        )
    try:
        body = r.json()
    except ValueError as e:
        raise ValueError("Неверный ответ API Яндекс.Диска.") from e
    href = str(body.get("href") or "").strip()
    if not href.lower().startswith(("http://", "https://")):
        raise ValueError(
            "Яндекс.Диск не вернул прямую ссылку на файл. "
            "Для папок пришлите ссылку на конкретный файл."
        )
    fname = _filename_from_url(href)
    if fname == "media.bin":
        fname = None
    return href, fname


def resolve_download_url(url: str) -> tuple[str, str | None]:
    """Нормализует ссылку: для Яндекс.Диска возвращает прямой URL."""
    u = (url or "").strip()
    if is_yandex_disk_public_url(u):
        return resolve_yandex_disk_download_url(u)
    return u, None


def download_url_bytes(url: str) -> tuple[bytes, str]:
    """Скачивает файл по прямой ссылке. Возвращает (bytes, filename)."""
    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        raise ValueError("Нужна ссылка http:// или https://")

    fname_hint: str | None = None
    try:
        u, fname_hint = resolve_download_url(u)
    except ValueError:
        raise

    with requests.get(u, stream=True, timeout=120, allow_redirects=True) as r:
        r.raise_for_status()
        cl = r.headers.get("Content-Length")
        if cl:
            try:
                size = int(cl)
            except ValueError:
                size = None
            if size is not None and size > URL_MAX_DOWNLOAD_BYTES:
                raise ValueError("Файл слишком большой.")

        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype:
            if any(ctype.startswith(p) for p in _HTML_MARKERS):
                raise ValueError(
                    "По ссылке отдаётся веб-страница, а не файл. "
                    "Нужна прямая ссылка на аудио или видео."
                )
            if not any(ctype.startswith(p) for p in _MEDIA_CONTENT_PREFIXES):
                raise ValueError(
                    f"Неподдерживаемый тип: {ctype}. "
                    "Нужна прямая ссылка на аудио или видео."
                )

        disposition = r.headers.get("Content-Disposition")
        chunks: list[bytes] = []
        total = 0
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > URL_MAX_DOWNLOAD_BYTES:
                raise ValueError("Файл слишком большой.")
            chunks.append(chunk)

    data = b"".join(chunks)
    if not data:
        raise ValueError("Пустой файл по ссылке.")

    fname = _filename_from_disposition(disposition)
    if not fname:
        fname = fname_hint or _filename_from_url(u)
    return data, fname
