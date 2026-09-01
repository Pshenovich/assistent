"""Извлечение ссылок на онлайн-встречи из события Google Calendar."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_ONLINE_HOST_HINTS = (
    "zoom.us",
    "zoom.com",
    "telemost.yandex.ru",
    "telemost.yandex.com",
    "meet.google.com",
    "teams.microsoft.com",
    "teams.live.com",
    "webex.com",
    "gotomeeting.com",
    "whereby.com",
    "jitsi",
    "kontur.ru",
    "trueconf",
)

_VIDEO_ENTRY_TYPES = frozenset({"video", "phone", "sip", "more"})


def _is_online_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    return any(h in host for h in _ONLINE_HOST_HINTS)


def _normalize_url(url: str) -> str:
    return (url or "").strip().rstrip(").,;]'\"")


def _dedupe_key(url: str) -> str:
    return _normalize_url(url).lower().rstrip("/")


def extract_online_meeting_urls(event: dict[str, Any]) -> list[str]:
    """Все уникальные ссылки на онлайн-звонок (Zoom, Meet, Teams и т.д.)."""
    if not isinstance(event, dict):
        return []
    seen: set[str] = set()
    out: list[str] = []

    def add(url: str) -> None:
        u = _normalize_url(url)
        if not u.startswith("http") or not _is_online_url(u):
            return
        key = _dedupe_key(u)
        if key in seen:
            return
        seen.add(key)
        out.append(u)

    hangout = str(event.get("hangoutLink") or "").strip()
    if hangout.startswith("http"):
        add(hangout)

    conf = event.get("conferenceData") or {}
    if isinstance(conf, dict):
        for ep in conf.get("entryPoints") or []:
            if not isinstance(ep, dict):
                continue
            if str(ep.get("entryPointType") or "").lower() not in _VIDEO_ENTRY_TYPES:
                continue
            uri = str(ep.get("uri") or "").strip()
            if uri.startswith("http"):
                add(uri)

    blobs: list[str] = []
    for key in ("description", "location"):
        blobs.append(str(event.get(key) or ""))
    priv = (event.get("extendedProperties") or {}).get("private") or {}
    if isinstance(priv, dict):
        for v in priv.values():
            blobs.append(str(v or ""))
    for blob in blobs:
        for m in _URL_RE.finditer(blob):
            add(m.group(0))
    return out


_ZOOM_MEETING_ID_RE = re.compile(
    r"(?:zoom\.us|zoom\.com)/(?:j|wc/join)/(\d{9,15})\b",
    re.IGNORECASE,
)


def meeting_id_from_zoom_url(url: str) -> str:
    """Извлечь numeric meeting id из join URL Zoom."""
    m = _ZOOM_MEETING_ID_RE.search((url or "").strip())
    return m.group(1) if m else ""


def extract_online_meeting_url(event: dict[str, Any]) -> str | None:
    """Первая ссылка на онлайн-звонок (для обратной совместимости)."""
    urls = extract_online_meeting_urls(event)
    return urls[0] if urls else None


def online_link_label(url: str) -> str:
    low = (url or "").lower()
    if "zoom" in low:
        return "Zoom"
    if "telemost" in low or "телемост" in low:
        return "Телемост"
    if "meet.google" in low:
        return "Google Meet"
    if "teams" in low:
        return "Microsoft Teams"
    if "webex" in low:
        return "Webex"
    return "онлайн-встречу"


def online_link_button_text(url: str, *, index: int, total: int) -> str:
    """Подпись кнопки «Подключиться» в напоминании."""
    label = online_link_label(url)
    if total == 1:
        return "Подключиться"
    return label if total <= 3 else f"{label} ({index})"
