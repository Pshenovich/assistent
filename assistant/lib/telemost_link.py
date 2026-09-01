"""Парсинг ссылок Yandex Telemost."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

_TELEMOST_HOST_RE = re.compile(r"(^|\.)telemost\.yandex\.(ru|com)$", re.IGNORECASE)
_TELEMOST_PATH_RE = re.compile(r"/j/(?P<id>[A-Za-z0-9_-]+)", re.IGNORECASE)

_PUNCT_TAIL = ").,;]'\""
_MARKERS = ("telemost", "телемост")


@dataclass(frozen=True)
class TelemostMeetingLink:
    url: str
    conference_id: str


def is_telemost_url(url: str) -> bool:
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return False
    return bool(_TELEMOST_HOST_RE.search(host))


def parse_telemost_url(url: str) -> TelemostMeetingLink | None:
    raw = (url or "").strip().rstrip(_PUNCT_TAIL)
    if not raw.startswith("http") or not is_telemost_url(raw):
        return None
    try:
        parsed = urlparse(raw)
    except Exception:
        return None
    m = _TELEMOST_PATH_RE.search(parsed.path or "")
    if not m:
        return None
    conf_id = str(m.group("id") or "").strip()
    if not conf_id:
        return None
    return TelemostMeetingLink(url=raw, conference_id=conf_id)


def find_telemost_url(urls: Iterable[str]) -> TelemostMeetingLink | None:
    for u in urls:
        parsed = parse_telemost_url(str(u or ""))
        if parsed is not None:
            return parsed
    return None
