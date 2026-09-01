"""Парсинг Zoom-ссылок для meeting bot."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlparse

_ZOOM_HOST_RE = re.compile(r"(^|\.)zoom\.(us|com)$", re.IGNORECASE)
_ZOOM_PATH_RE = re.compile(r"/j/(?P<id>\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ZoomMeetingLink:
    url: str
    native_meeting_id: str
    passcode: str | None = None


def is_zoom_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    return bool(_ZOOM_HOST_RE.search(host))


_PUNCT_TAIL = ").,;]'\""


def parse_zoom_url(url: str) -> ZoomMeetingLink | None:
    raw = (url or "").strip().rstrip(_PUNCT_TAIL)
    if not raw.startswith("http") or not is_zoom_url(raw):
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    m = _ZOOM_PATH_RE.search(parsed.path or "")
    if not m:
        return None
    meeting_id = str(m.group("id") or "").strip()
    if not meeting_id:
        return None
    qs = parse_qs(parsed.query or "")
    pwd_vals = qs.get("pwd") or []
    passcode = str(pwd_vals[0]).strip() if pwd_vals else None
    return ZoomMeetingLink(url=raw, native_meeting_id=meeting_id, passcode=passcode or None)


def find_zoom_url(urls: Iterable[str]) -> ZoomMeetingLink | None:
    for u in urls:
        parsed = parse_zoom_url(str(u or ""))
        if parsed is not None:
            return parsed
    return None
