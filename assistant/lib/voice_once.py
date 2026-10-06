"""Однократная обработка голосового сообщения (защита от двойного handler/update)."""

from __future__ import annotations

import asyncio
import time

_LOCK = asyncio.Lock()
_SEEN: dict[str, float] = {}
_TTL_SEC = 600.0


def _prune(now: float) -> None:
    stale = [k for k, ts in _SEEN.items() if now - ts > _TTL_SEC]
    for k in stale:
        _SEEN.pop(k, None)


async def claim_voice_once(
    *,
    chat_id: int | str,
    message_id: int | str,
    update_id: int | str | None = None,
) -> bool:
    """True — обрабатываем; False — уже взяли это message_id."""
    key = f"{chat_id}:{message_id}"
    async with _LOCK:
        now = time.monotonic()
        _prune(now)
        if key in _SEEN:
            print(
                f"[voice] skip_duplicate chat={chat_id} mid={message_id} "
                f"update={update_id}"
            )
            return False
        _SEEN[key] = now
        print(
            f"[voice] claim chat={chat_id} mid={message_id} update={update_id}"
        )
        return True
