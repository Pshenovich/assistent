"""Общее открытие баз SQLite: WAL и ожидание блокировки.

Бот и веб — разные процессы и пишут в одни файлы. В режиме WAL чтение не ждёт
записи, а busy_timeout даёт писателю подождать соседа вместо «database is locked».
Режим WAL сохраняется в самом файле базы, старый код с ним тоже работает.
"""

from __future__ import annotations

import functools
import sqlite3
from pathlib import Path
from typing import Callable

BUSY_TIMEOUT_MS = 30_000


def connect(path: str | Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=timeout)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode and str(mode[0]).lower() == "wal":
            # В WAL так безопасно: после сбоя питания теряется разве что последняя
            # транзакция, база не портится.
            conn.execute("PRAGMA synchronous=NORMAL")
        else:
            print(f"[sqlite] WAL не включился path={path} mode={mode!r}")
    except sqlite3.DatabaseError as e:
        # Например, база занята соседом дольше таймаута — работаем в прежнем режиме.
        print(f"[sqlite] WAL не включился path={path} err={e!r}")
    return conn


def schema_once(base: Callable[[], sqlite3.Connection]):
    """Декоратор для _conn() хранилищ поверх общего соединения: схема (CREATE … IF NOT EXISTS, ALTER) —
    один раз на соединение, а не на каждый вызов. base() — общее соединение; сменилось (другая база,
    тесты) — схема прогоняется заново."""

    def deco(init: Callable[[], sqlite3.Connection]) -> Callable[[], sqlite3.Connection]:
        ready: list[sqlite3.Connection | None] = [None]

        @functools.wraps(init)
        def wrapper() -> sqlite3.Connection:
            conn = base()
            if ready[0] is conn:
                return conn
            out = init()
            ready[0] = out
            return out

        return wrapper

    return deco
