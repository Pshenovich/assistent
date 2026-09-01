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


def export_url(document_id: str, *, spreadsheet: bool = False) -> str:
    kind = "spreadsheets" if spreadsheet else "document"
    fmt = "csv" if spreadsheet else "txt"
    return f"https://docs.google.com/{kind}/d/{document_id}/export?format={fmt}"


def fetch_google_doc(url: str, *, timeout: float = 60) -> KbFetchedDocument:
    doc_id = parse_google_doc_id(url)
    if not doc_id:
        raise ValueError("Не удалось распознать ссылку Google Docs")
    spreadsheet = "spreadsheets" in (url or "").lower()
    exp = export_url(doc_id, spreadsheet=spreadsheet)
    r = requests.get(exp, timeout=timeout, allow_redirects=True)
    if not r.ok:
        raise RuntimeError(
            f"Google Docs: HTTP {r.status_code}. Убедитесь, что документ доступен по ссылке."
        )
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
