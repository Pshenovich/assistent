"""Yandex Disk REST API: загрузка записей встреч."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any
import requests

_DISK_API_BASE = "https://cloud-api.yandex.net/v1/disk"
_UPLOAD_TIMEOUT_SEC = int(os.getenv("YANDEX_DISK_UPLOAD_TIMEOUT_SEC", "900") or "900")

_INVALID_PATH_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def upload_root() -> str:
    raw = (
        os.getenv("YANDEX_DISK_UPLOAD_ROOT", "disk:/Leo/Записи встреч") or ""
    ).strip()
    return raw or "disk:/Leo/Записи встреч"


def sanitize_folder_name(topic: str, *, date: datetime | None = None) -> str:
    dt = date or datetime.now(timezone.utc)
    date_part = dt.strftime("%Y-%m-%d")
    slug = (topic or "Встреча").strip()
    slug = _INVALID_PATH_CHARS_RE.sub(" ", slug)
    slug = re.sub(r"\s+", " ", slug).strip()
    slug = slug[:80].strip(" .")
    if not slug:
        slug = "Встреча"
    return f"{date_part} — {slug}"


def _join_disk_path(parent: str, child: str) -> str:
    base = (parent or "").rstrip("/")
    name = (child or "").strip().lstrip("/")
    if not base:
        return f"disk:/{name}"
    return f"{base}/{name}"


def _disk_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"OAuth {access_token.strip()}"}


def _disk_path_segments(path: str) -> list[str]:
    """Разбивает disk:/a/b/c на [disk:/a, disk:/a/b, disk:/a/b/c]."""
    raw = (path or "").strip()
    if raw.lower().startswith("disk:"):
        raw = raw[5:]
    raw = raw.lstrip("/")
    if not raw:
        return []
    parts = [p for p in raw.split("/") if p]
    segments: list[str] = []
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        segments.append(f"disk:/{current}")
    return segments


def ensure_folder(access_token: str, path: str) -> None:
    """Создаёт папку на диске (идемпотентно)."""
    url = f"{_DISK_API_BASE}/resources"
    params = {"path": path}
    r = requests.put(
        url,
        headers=_disk_headers(access_token),
        params=params,
        timeout=60,
    )
    if r.status_code in (201, 409):
        return
    if r.ok:
        return
    try:
        body: Any = r.json()
    except Exception:
        body = (r.text or "")[:500]
    raise RuntimeError(f"Yandex Disk: не удалось создать папку {path!r}: HTTP {r.status_code}: {body!r}")


def ensure_folder_hierarchy(access_token: str, path: str) -> None:
    """Создаёт папку и все родительские каталоги по пути (API Яндекса — по одному уровню)."""
    for segment in _disk_path_segments(path):
        ensure_folder(access_token, segment)


def upload_bytes(
    access_token: str,
    disk_path: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> str:
    """Загружает файл на Яндекс Диск. Возвращает disk_path."""
    if not data:
        raise RuntimeError(f"Yandex Disk: пустые данные для {disk_path!r}")
    url = f"{_DISK_API_BASE}/resources/upload"
    params: dict[str, Any] = {
        "path": disk_path,
        "overwrite": "true",
    }
    size = len(data)
    if size > 0:
        params["content_length"] = size
    r = requests.get(
        url,
        headers=_disk_headers(access_token),
        params=params,
        timeout=60,
    )
    if not r.ok:
        try:
            body: Any = r.json()
        except Exception:
            body = (r.text or "")[:500]
        raise RuntimeError(
            f"Yandex Disk: upload URL HTTP {r.status_code} для {disk_path!r}: {body!r}"
        )
    try:
        payload = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex Disk: upload URL не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(payload, dict):
        raise RuntimeError(f"Yandex Disk: неожиданный upload URL: {payload!r}")
    href = str(payload.get("href") or "").strip()
    if not href:
        raise RuntimeError(f"Yandex Disk: нет href в upload URL: {payload!r}")

    headers: dict[str, str] = {}
    if content_type:
        headers["Content-Type"] = content_type
    put = requests.put(href, data=data, headers=headers, timeout=_UPLOAD_TIMEOUT_SEC)
    if not put.ok:
        raise RuntimeError(
            f"Yandex Disk: PUT upload HTTP {put.status_code} для {disk_path!r}: "
            f"{(put.text or '')[:300]!r}"
        )
    return disk_path


def _guess_content_type(filename: str) -> str | None:
    low = (filename or "").lower()
    if low.endswith(".wav"):
        return "audio/wav"
    if low.endswith(".mp3"):
        return "audio/mpeg"
    if low.endswith(".mp4"):
        return "video/mp4"
    if low.endswith(".pdf"):
        return "application/pdf"
    return None


def upload_meeting_artifacts(
    access_token: str,
    *,
    media_bytes: bytes,
    media_filename: str,
    topic: str,
    transcript_pdf: tuple[bytes, str],
    summary_pdf: tuple[bytes, str],
    meeting_date: datetime | None = None,
) -> dict[str, str]:
    """Загружает папку встречи: медиа + PDF транскрипции и саммари."""
    root = upload_root()
    folder_name = sanitize_folder_name(topic, date=meeting_date)
    meeting_folder = _join_disk_path(root, folder_name)

    ensure_folder_hierarchy(access_token, meeting_folder)

    paths: dict[str, str] = {}

    media_name = (media_filename or "meeting.wav").strip() or "meeting.wav"
    media_path = _join_disk_path(meeting_folder, media_name)
    upload_bytes(
        access_token,
        media_path,
        media_bytes,
        content_type=_guess_content_type(media_name),
    )
    paths["media"] = media_path

    tr_data, tr_name = transcript_pdf
    tr_path = _join_disk_path(meeting_folder, tr_name)
    upload_bytes(
        access_token,
        tr_path,
        tr_data,
        content_type="application/pdf",
    )
    paths["transcript_pdf"] = tr_path

    sm_data, sm_name = summary_pdf
    sm_path = _join_disk_path(meeting_folder, sm_name)
    upload_bytes(
        access_token,
        sm_path,
        sm_data,
        content_type="application/pdf",
    )
    paths["summary_pdf"] = sm_path
    paths["folder"] = meeting_folder
    public_url = publish_folder_public_url(access_token, meeting_folder)
    if public_url:
        paths["folder_public_url"] = public_url
    return paths


def _public_url_from_resource_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    public_url = str(payload.get("public_url") or "").strip()
    if public_url:
        return public_url
    public_key = str(payload.get("public_key") or "").strip()
    if public_key.startswith("https://"):
        return public_key
    if public_key:
        return f"https://disk.yandex.ru/d/{public_key.lstrip('/')}"
    return None


def _fetch_published_resource_meta(
    access_token: str,
    *,
    disk_path: str,
    meta_href: str | None = None,
) -> str | None:
    """Читает public_url/public_key после publish (нужен scope cloud_api:disk.read)."""
    headers = _disk_headers(access_token)
    params = {"fields": "public_url,public_key"}
    href = (meta_href or "").strip()
    if href:
        meta = requests.get(href, headers=headers, params=params, timeout=60)
    else:
        meta = requests.get(
            f"{_DISK_API_BASE}/resources",
            headers=headers,
            params={"path": disk_path, **params},
            timeout=60,
        )
    if not meta.ok:
        try:
            body: Any = meta.json()
        except Exception:
            body = (meta.text or "")[:500]
        print(
            f"[yandex_disk_api] public_url meta HTTP {meta.status_code} "
            f"for {disk_path!r}: {body!r}"
        )
        return None
    try:
        payload = meta.json()
    except Exception:
        return None
    return _public_url_from_resource_payload(payload)


def publish_folder_public_url(access_token: str, disk_path: str) -> str | None:
    """Публикует папку на Яндекс Диске и возвращает ссылку вида https://disk.yandex.ru/d/…"""
    path = (disk_path or "").strip()
    if not path:
        return None
    pub = requests.put(
        f"{_DISK_API_BASE}/resources/publish",
        headers=_disk_headers(access_token),
        params={"path": path},
        timeout=60,
    )
    if pub.status_code not in (200, 201, 202, 409):
        try:
            body: Any = pub.json()
        except Exception:
            body = (pub.text or "")[:500]
        print(f"[yandex_disk_api] publish HTTP {pub.status_code} for {path!r}: {body!r}")
        return None
    meta_href: str | None = None
    try:
        pub_payload = pub.json()
        if isinstance(pub_payload, dict):
            meta_href = str(pub_payload.get("href") or "").strip() or None
    except Exception:
        meta_href = None
    url = _fetch_published_resource_meta(
        access_token, disk_path=path, meta_href=meta_href
    )
    if url:
        return url
    # Иногда public_url появляется с небольшой задержкой после publish.
    time.sleep(0.5)
    return _fetch_published_resource_meta(
        access_token, disk_path=path, meta_href=meta_href
    )


def list_folder(access_token: str, disk_path: str) -> list[dict[str, Any]]:
    """Список элементов папки на Яндекс Диске."""
    path = (disk_path or "").strip()
    if not path:
        return []
    r = requests.get(
        f"{_DISK_API_BASE}/resources",
        headers=_disk_headers(access_token),
        params={"path": path, "limit": 500},
        timeout=60,
    )
    if not r.ok:
        try:
            body: Any = r.json()
        except Exception:
            body = (r.text or "")[:500]
        raise RuntimeError(
            f"Yandex Disk: list HTTP {r.status_code} для {path!r}: {body!r}"
        )
    try:
        payload = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex Disk: list не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(payload, dict):
        return []
    embedded = payload.get("_embedded")
    if not isinstance(embedded, dict):
        return []
    items = embedded.get("items")
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def download_file_bytes(access_token: str, disk_path: str) -> bytes:
    """Скачивает файл с Яндекс Диска по disk:/path."""
    path = (disk_path or "").strip()
    if not path:
        raise ValueError("Пустой путь Яндекс Диска")
    r = requests.get(
        f"{_DISK_API_BASE}/resources/download",
        headers=_disk_headers(access_token),
        params={"path": path},
        timeout=60,
    )
    if not r.ok:
        try:
            body: Any = r.json()
        except Exception:
            body = (r.text or "")[:500]
        raise RuntimeError(
            f"Yandex Disk: download URL HTTP {r.status_code} для {path!r}: {body!r}"
        )
    try:
        payload = r.json()
    except Exception as e:
        raise RuntimeError(f"Yandex Disk: download URL не JSON: {(r.text or '')[:400]!r}") from e
    if not isinstance(payload, dict):
        raise RuntimeError(f"Yandex Disk: неожиданный download URL: {payload!r}")
    href = str(payload.get("href") or "").strip()
    if not href:
        raise RuntimeError(f"Yandex Disk: нет href для скачивания {path!r}")
    got = requests.get(href, timeout=_UPLOAD_TIMEOUT_SEC)
    if not got.ok:
        raise RuntimeError(
            f"Yandex Disk: GET file HTTP {got.status_code} для {path!r}"
        )
    return got.content or b""
