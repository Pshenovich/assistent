"""Однократная обработка Telegram update_id (антидубль ответов)."""

from __future__ import annotations

import time
from threading import Lock

_LOCK = Lock()
_SEEN: dict[int, float] = {}
_TTL_SEC = 600.0


def _prune(now: float) -> None:
    stale = [k for k, ts in _SEEN.items() if now - ts > _TTL_SEC]
    for k in stale:
        _SEEN.pop(k, None)


def claim_update_once(update_id: int | None) -> bool:
    """True — обрабатываем; False — этот update_id уже брали."""
    if update_id is None:
        return True
    try:
        uid = int(update_id)
    except (TypeError, ValueError):
        return True
    with _LOCK:
        now = time.monotonic()
        _prune(now)
        if uid in _SEEN:
            print(f"[bot] skip_duplicate_update id={uid}")
            return False
        _SEEN[uid] = now
        return True
