"""Медленная модель не ставит в очередь весь API: запросы к ИИ — в своём лимите потоков."""

from __future__ import annotations

import asyncio
import threading
import time

import anyio
import anyio.to_thread

import usage_server


def test_llm_calls_do_not_exhaust_default_pool(monkeypatch):
    monkeypatch.setattr(usage_server, "_LLM_LIMITER", None)
    monkeypatch.setenv("LLM_THREADS", "3")
    release = threading.Event()

    def slow_model() -> str:
        release.wait(5)
        return "ответ"

    async def scenario() -> tuple[float, int, list[str]]:
        default = anyio.to_thread.current_default_thread_limiter()
        default.total_tokens = 4  # как будто пул почти исчерпан
        llm_tasks = [asyncio.create_task(usage_server.run_llm_in_threadpool(slow_model)) for _ in range(10)]
        await asyncio.sleep(0.2)
        t = time.perf_counter()
        quick = await anyio.to_thread.run_sync(lambda: "ok")  # обычный запрос API
        dt = time.perf_counter() - t
        busy = int(usage_server._llm_limiter().borrowed_tokens)
        release.set()
        res = await asyncio.gather(*llm_tasks)
        default.total_tokens = 40
        return dt, busy, [quick, *res]

    dt, busy, res = asyncio.run(scenario())
    assert dt < 1.0
    assert busy == 3
    assert res == ["ok"] + ["ответ"] * 10
