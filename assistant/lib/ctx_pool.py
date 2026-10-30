"""Пул потоков, который переносит контекст вызывающего (contextvars) в каждую задачу.

Обычный ThreadPoolExecutor запускает задачи с пустым контекстом — и, например, расход ИИ
в саммари встречи записывался без пользователя.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future:
        # Своя копия на каждую задачу: один Context нельзя войти из двух потоков сразу.
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)
