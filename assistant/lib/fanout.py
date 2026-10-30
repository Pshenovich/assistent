"""Обход пользователей в фоновых задачах не по одному, а пачкой с ограничением.

Раньше задачи раз в минуту шли по людям последовательно: при сотнях пользователей
проход не укладывался в интервал. Теперь до FANOUT_CONCURRENCY (20) человек
одновременно; ошибка одного не мешает остальным.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable, Iterable


def concurrency() -> int:
    try:
        return max(1, int(os.getenv("FANOUT_CONCURRENCY", "20") or "20"))
    except ValueError:
        return 20


async def for_each_user(
    user_ids: Iterable[Any],
    fn: Callable[[Any], Awaitable[None]],
    *,
    label: str,
    limit: int | None = None,
) -> None:
    sem = asyncio.Semaphore(limit or concurrency())

    async def one(uid: Any) -> None:
        async with sem:
            try:
                await fn(uid)
            except Exception as e:
                print(f"[{label}] user={uid} err={e!r}")

    await asyncio.gather(*(one(u) for u in list(user_ids)))
