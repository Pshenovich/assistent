"""Загрузка Google Docs для базы знаний."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import requests

_DOC_ID_RE = re.compile(
    r"docs\.google\.com/(?:document|spreadsheets)/d/([a-zA-Z0-9_-]+)"
)


@dataclass
class KbFetchedDocument:
    doc_id: str
    title: str
    text: str
    modified_at: str | None = None
    url: str = ""


def parse_google_doc_id(url: str) -> str | None:
    m = _DOC_ID_RE.search(url or "")
    return m.group(1) if m else None


_GID_RE = re.compile(r"[#?&]gid=(\d+)")


class GoogleDocNoAccess(RuntimeError):
    """Документ не открыт по ссылке: Google отдал страницу входа вместо содержимого."""


def parse_gid(url: str) -> str | None:
    """Лист таблицы из ссылки (…#gid=123 или ?gid=123)."""
    m = _GID_RE.search(url or "")
    return m.group(1) if m else None


def export_url(document_id: str, *, spreadsheet: bool = False, gid: str | None = None) -> str:
    kind = "spreadsheets" if spreadsheet else "document"
    fmt = "csv" if spreadsheet else "txt"
    out = f"https://docs.google.com/{kind}/d/{document_id}/export?format={fmt}"
    if spreadsheet and gid:
        out += f"&gid={gid}"
    return out


def fetch_google_doc(url: str, *, timeout: float = 60) -> KbFetchedDocument:
    doc_id = parse_google_doc_id(url)
    if not doc_id:
        raise ValueError("Не удалось распознать ссылку Google Docs")
    spreadsheet = "spreadsheets" in (url or "").lower()
    exp = export_url(doc_id, spreadsheet=spreadsheet, gid=parse_gid(url) if spreadsheet else None)
    r = requests.get(exp, timeout=timeout, allow_redirects=True)
    if r.status_code in (401, 403, 404):
        raise GoogleDocNoAccess(f"Google Docs: HTTP {r.status_code} — документ не открыт по ссылке")
    if not r.ok:
        raise RuntimeError(
            f"Google Docs: HTTP {r.status_code}. Убедитесь, что документ доступен по ссылке."
        )
    # Закрытый документ: Google перенаправляет на страницу входа и отвечает 200 с HTML.
    ctype = str(r.headers.get("Content-Type") or "").lower()
    if "text/html" in ctype or "accounts.google.com" in str(getattr(r, "url", "") or ""):
        raise GoogleDocNoAccess("Google Docs: документ не открыт по ссылке")
    r.encoding = "utf-8"
    text = (r.text or "").strip()
    if not text:
        raise RuntimeError("Google Docs: пустой документ или нет доступа по ссылке")
    title = doc_id
    doc_url = (url or "").split("?")[0].rstrip("/")
    if not doc_url.endswith("/edit"):
        doc_url = f"{doc_url}/edit"
    return KbFetchedDocument(
        doc_id=doc_id,
        title=title,
        text=text,
        modified_at=None,
        url=doc_url,
    )


def fetch_documents(url: str) -> list[KbFetchedDocument]:
    return [fetch_google_doc(url)]


MAX_XLSX_BYTES = 30 * 1024 * 1024


def fetch_google_sheet_xlsx(url: str, *, timeout: float = 60) -> bytes:
    """Google Таблица целиком (все листы) файлом .xlsx; закрытая — GoogleDocNoAccess."""
    doc_id = parse_google_doc_id(url)
    if not doc_id or "/spreadsheets/" not in (url or "").lower():
        raise ValueError("Не удалось распознать ссылку Google Таблицы")
    r = requests.get(
        f"https://docs.google.com/spreadsheets/d/{doc_id}/export?format=xlsx",
        timeout=timeout,
        allow_redirects=True,
    )
    if r.status_code in (401, 403, 404):
        raise GoogleDocNoAccess(f"Google Таблица: HTTP {r.status_code} — не открыта по ссылке")
    if not r.ok:
        raise RuntimeError(f"Google Таблица: HTTP {r.status_code}")
    ctype = str(r.headers.get("Content-Type") or "").lower()
    if "text/html" in ctype or "accounts.google.com" in str(getattr(r, "url", "") or ""):
        raise GoogleDocNoAccess("Google Таблица не открыта по ссылке")
    data = r.content or b""
    if not data:
        raise RuntimeError("Google Таблица: пустой ответ")
    if len(data) > MAX_XLSX_BYTES:
        raise RuntimeError("Google Таблица больше 30 МБ")
    return data
