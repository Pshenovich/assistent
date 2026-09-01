"""Загрузка папки Яндекс Диска для базы знаний."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from assistant.integrations import yandex_disk_api, yandex_disk_oauth

_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}


@dataclass
class KbFetchedDocument:
    doc_id: str
    title: str
    text: str
    modified_at: str | None = None
    url: str = ""


def _disk_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"OAuth {access_token.strip()}"}


def parse_yandex_disk_path(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("disk:"):
        return raw
    parsed = urlparse(raw)
    path = unquote(parsed.path or "")
    if "/client/disk" in path:
        sub = path.split("/client/disk", 1)[-1].strip("/")
        if sub:
            return f"disk:/{sub}"
    if "disk.yandex" in (parsed.netloc or "").lower() or "yadi.sk" in (
        parsed.netloc or ""
    ).lower():
        return raw
    return None


def _public_resource_meta(public_url: str) -> dict[str, Any]:
    r = requests.get(
        "https://cloud-api.yandex.net/v1/disk/public/resources",
        params={"public_key": public_url, "limit": 500},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(
            f"Яндекс Диск: HTTP {r.status_code} для публичной ссылки"
        )
    data = r.json()
    return data if isinstance(data, dict) else {}


def _public_download_href(public_url: str, path: str) -> str:
    r = requests.get(
        "https://cloud-api.yandex.net/v1/disk/public/resources/download",
        params={"public_key": public_url, "path": path},
        timeout=60,
    )
    if not r.ok:
        raise RuntimeError(f"Яндекс Диск: не удалось скачать {path!r}")
    data = r.json()
    if not isinstance(data, dict):
        raise RuntimeError("Яндекс Диск: неожиданный ответ download")
    href = str(data.get("href") or "").strip()
    if not href:
        raise RuntimeError("Яндекс Диск: нет href для скачивания")
    return href


def _download_text(href: str) -> str:
    r = requests.get(href, timeout=120)
    if not r.ok:
        raise RuntimeError(f"Яндекс Диск: скачивание HTTP {r.status_code}")
    return (r.text or "").strip()


def _is_text_file(name: str) -> bool:
    low = (name or "").lower()
    return any(low.endswith(ext) for ext in _TEXT_EXTENSIONS)


def _walk_public_folder(
    public_url: str,
    *,
    base_path: str = "",
    docs: list[KbFetchedDocument] | None = None,
) -> list[KbFetchedDocument]:
    out = docs if docs is not None else []
    meta = _public_resource_meta(public_url if not base_path else public_url)
    if base_path:
        meta = requests.get(
            "https://cloud-api.yandex.net/v1/disk/public/resources",
            params={"public_key": public_url, "path": base_path, "limit": 500},
            timeout=60,
        ).json()
        if not isinstance(meta, dict):
            return out
    embedded = meta.get("_embedded")
    items = embedded.get("items") if isinstance(embedded, dict) else None
    if not isinstance(items, list):
        return out
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        item_path = str(item.get("path") or name)
        item_type = str(item.get("type") or "")
        modified = str(item.get("modified") or "") or None
        if item_type == "dir":
            _walk_public_folder(public_url, base_path=item_path, docs=out)
            continue
        if not _is_text_file(name):
            continue
        href = _public_download_href(public_url, item_path)
        text = _download_text(href)
        if not text:
            continue
        doc_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", item_path)[:120] or name
        out.append(
            KbFetchedDocument(
                doc_id=doc_id,
                title=name,
                text=text,
                modified_at=modified,
                url=public_url,
            )
        )
    return out


def _walk_private_folder(
    access_token: str,
    disk_path: str,
    *,
    docs: list[KbFetchedDocument] | None = None,
) -> list[KbFetchedDocument]:
    out = docs if docs is not None else []
    items = yandex_disk_api.list_folder(access_token, disk_path)
    for item in items:
        name = str(item.get("name") or "")
        item_path = str(item.get("path") or "")
        item_type = str(item.get("type") or "")
        modified = str(item.get("modified") or "") or None
        if item_type == "dir":
            _walk_private_folder(access_token, item_path, docs=out)
            continue
        if not _is_text_file(name):
            continue
        data = yandex_disk_api.download_file_bytes(access_token, item_path)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        text = text.strip()
        if not text:
            continue
        doc_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", item_path)[:120] or name
        out.append(
            KbFetchedDocument(
                doc_id=doc_id,
                title=name,
                text=text,
                modified_at=modified,
                url=disk_path,
            )
        )
    return out


def fetch_documents(
    url: str,
    *,
    owner_telegram_user_id: int,
) -> list[KbFetchedDocument]:
    raw = (url or "").strip()
    disk_path = parse_yandex_disk_path(raw)
    if disk_path and disk_path.lower().startswith("disk:"):
        token = yandex_disk_oauth.get_valid_access_token(int(owner_telegram_user_id))
        return _walk_private_folder(token, disk_path)
    public_url = raw
    meta = _public_resource_meta(public_url)
    rtype = str(meta.get("type") or "")
    if rtype == "file":
        name = str(meta.get("name") or "file")
        if not _is_text_file(name):
            raise RuntimeError("Поддерживаются только текстовые файлы (.txt, .md, .csv)")
        href = _public_download_href(public_url, "/")
        text = _download_text(href)
        doc_id = re.sub(r"[^a-zA-Z0-9_-]+", "_", name)[:120]
        return [
            KbFetchedDocument(
                doc_id=doc_id,
                title=name,
                text=text,
                modified_at=str(meta.get("modified") or "") or None,
                url=public_url,
            )
        ]
    docs = _walk_public_folder(public_url)
    if not docs:
        raise RuntimeError(
            "В папке нет текстовых файлов (.txt, .md) или нет доступа по ссылке"
        )
    return docs
