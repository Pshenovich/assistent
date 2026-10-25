"""Хаб WebSocket-подписок веб-процесса.

Темы: ``note:{id}`` (заметка и её листы/комментарии), ``user:{uid}`` (списки пользователя).
В сокет уходят только сигналы «что изменилось» — данные клиент берёт обычными запросами
со своей авторизацией, поэтому событие не раскрывает содержимое.

``publish`` можно звать из любого потока: хранилища работают в ``run_in_threadpool``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Callable, Optional

_SEND_QUEUE_MAX = 200


class Connection:
    """Один сокет: своя очередь отправки, чтобы медленный клиент не тормозил остальных."""

    def __init__(self, user_id: str) -> None:
        self.user_id = str(user_id)
        self.topics: set[str] = set()
        self.queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=_SEND_QUEUE_MAX)
        self.closed = False

    def push(self, text: str) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(text)
        except asyncio.QueueFull:
            # Клиент не читает — закрываем; переподключится и догонит запросом collab.
            self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.queue.put_nowait(None)
        except asyncio.QueueFull:
            pass


class Hub:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._subs: dict[str, set[Connection]] = {}
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []

    def add_listener(self, fn: Callable[[str, dict[str, Any]], None]) -> None:
        """Наблюдатель событий (в цикле событий): комнаты Yjs ловят правки мимо Yjs."""
        if fn not in self._listeners:
            self._listeners.append(fn)

    # ---- жизненный цикл ----
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def active(self) -> bool:
        loop = self._loop
        return loop is not None and not loop.is_closed()

    # ---- подписки (только из цикла событий) ----
    def subscribe(self, conn: Connection, topic: str) -> None:
        with self._lock:
            self._subs.setdefault(topic, set()).add(conn)
        conn.topics.add(topic)

    def unsubscribe(self, conn: Connection, topic: str) -> None:
        with self._lock:
            subs = self._subs.get(topic)
            if subs is not None:
                subs.discard(conn)
                if not subs:
                    self._subs.pop(topic, None)
        conn.topics.discard(topic)

    def drop(self, conn: Connection) -> None:
        for topic in list(conn.topics):
            self.unsubscribe(conn, topic)
        conn.close()

    def subscriber_count(self, topic: str | None = None) -> int:
        with self._lock:
            if topic is not None:
                return len(self._subs.get(topic) or ())
            conns: set[Connection] = set()
            for subs in self._subs.values():
                conns |= subs
            return len(conns)

    # ---- рассылка ----
    def _deliver(
        self, topic: str, text: str, payload: dict[str, Any], exclude: Optional[Connection]
    ) -> None:
        with self._lock:
            targets = list(self._subs.get(topic) or ())
        for conn in targets:
            if conn is not exclude:
                conn.push(text)
        for fn in list(self._listeners):
            try:
                fn(topic, payload)
            except Exception as exc:
                print(f"[realtime] listener failed topic={topic} err={exc!r}")

    def publish(
        self,
        topic: str,
        event: dict[str, Any],
        *,
        exclude: Optional[Connection] = None,
    ) -> bool:
        """Разослать событие подписчикам темы. False — хаб не запущен (не веб-процесс)."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return False
        payload = dict(event)
        payload.setdefault("topic", topic)
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._deliver(topic, text, payload, exclude)
        else:
            try:
                loop.call_soon_threadsafe(self._deliver, topic, text, payload, exclude)
            except RuntimeError:
                return False
        return True


HUB = Hub()


def note_topic(note_id: int | str) -> str:
    return f"note:{int(note_id)}"


def user_topic(user_id: int | str) -> str:
    return f"user:{int(user_id)}"
