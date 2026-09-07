"""Живой документ компании из публичной share-ссылки миниаппа Leo."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from assistant.lib.telegram_html import html_to_plain, uses_html_markup
from assistant.lib.webapp_public import public_base_url

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,80}$")
_CACHE_TTL_SEC = 60.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_stale: dict[str, dict[str, Any]] = {}

DEFAULT_SHARE_URL = (
    "https://assistent.networ.ru/share/lYS_LYaj9MI3aIGHJib2Tyn1nNLAZ-eO"
)


def default_company_share_url() -> str:
    raw = (os.getenv("BOARD_COMPANY_SHARE_URL") or "").strip()
    if raw.lower() in {"0", "off", "none", "false"}:
        return ""
    return raw or DEFAULT_SHARE_URL


def parse_share_token(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if _TOKEN_RE.match(text) and "://" not in text and "/" not in text:
        return text
    parsed = urlparse(text)
    path = parsed.path or ""
    if "/share/" not in path:
        return None
    token = path.rsplit("/share/", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if _TOKEN_RE.match(token):
        return token
    return None


def looks_like_share_source(raw: str) -> bool:
    return parse_share_token(raw) is not None


def normalize_share_url(raw: str) -> str:
    token = parse_share_token(raw)
    if not token:
        return (raw or "").strip()
    parsed = urlparse((raw or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/share/{token}"
    return f"{public_base_url()}/share/{token}"


def share_api_url(token: str, source: str = "") -> str:
    parsed = urlparse((source or "").strip())
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/api/public/share/{token}"
    return f"{public_base_url()}/api/public/share/{token}"


def share_body_to_text(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if not uses_html_markup(s):
        return s
    s = re.sub(r"</(?:p|div|h[1-6]|li|tr)\s*>", "\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<li\b[^>]*>", "- ", s, flags=re.I)
    return html_to_plain(s)


def _doc_from_payload(url: str, payload: dict[str, Any], *, stale: bool = False) -> dict[str, Any]:
    title = str(payload.get("title") or "Документ компании").strip() or "Документ компании"
    body = str(payload.get("body") or "")
    text = share_body_to_text(body)
    return {
        "url": url,
        "title": title,
        "html": body if uses_html_markup(body) else "",
        "text": text,
        "updated_at": payload.get("updated_at"),
        "kind": payload.get("kind"),
        "stale": stale,
    }


def _fetch_http(token: str, source: str = "") -> dict[str, Any] | None:
    req = urllib.request.Request(
        share_api_url(token, source),
        headers={"Accept": "application/json", "User-Agent": "LeoExecutiveBoard/1.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw else {}
    if not isinstance(data, dict) or not (data.get("body") or data.get("title")):
        return None
    return data


def _fetch_local(token: str) -> dict[str, Any] | None:
    try:
        from assistant.stores import notes as notes_store
        from assistant.stores import share_links
    except Exception:
        return None
    link = share_links.resolve_share(token)
    if not link or str(link.get("item_kind") or "") != "local":
        return None
    try:
        note = notes_store.get_note(link["user_id"], int(link["item_id"]))
    except (TypeError, ValueError):
        return None
    if not note:
        return None
    return {
        "kind": "local",
        "title": str(note.get("title") or "").strip() or "Без названия",
        "body": str(note.get("body") or ""),
        "updated_at": note.get("updated_at"),
    }


def fetch_share_document(url_or_token: str) -> dict[str, Any]:
    url = normalize_share_url(url_or_token)
    token = parse_share_token(url_or_token)
    if not token:
        raise ValueError("Это не share-ссылка миниаппа Leo")
    now = time.monotonic()
    with _lock:
        hit = _cache.get(token)
        if hit and now - hit[0] < _CACHE_TTL_SEC:
            return dict(hit[1])
    payload: dict[str, Any] | None = None
    last_err: Exception | None = None
    try:
        payload = _fetch_http(token, url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as e:
        last_err = e
        print(f"[board.share] http_fail token={token[:8]}… err={e!r}")
        try:
            payload = _fetch_local(token)
        except Exception as e2:
            last_err = e2
            print(f"[board.share] local_fail token={token[:8]}… err={e2!r}")
    if payload:
        doc = _doc_from_payload(url, payload, stale=False)
        with _lock:
            _cache[token] = (time.monotonic(), doc)
            _stale[token] = doc
        return dict(doc)
    with _lock:
        old = _stale.get(token)
    if old:
        out = dict(old)
        out["stale"] = True
        return out
    raise RuntimeError(str(last_err) if last_err else "Документ недоступен")


def resolve_company_share(
    stored_url: str | None = None,
) -> tuple[str, dict[str, Any] | None, str | None]:
    url = (stored_url or "").strip() or default_company_share_url()
    if not url:
        return "", None, None
    try:
        return url, fetch_share_document(url), None
    except Exception as e:
        return url, None, str(e)[:240]
