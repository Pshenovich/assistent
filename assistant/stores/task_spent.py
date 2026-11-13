"""«Сколько ушло» на задачу: минуты, которые исполнитель указал при сдаче (необязательно).

Отдельная таблица в базе задач (calendar_tasks), чтобы не трогать саму задачу: одна запись на задачу,
повторная сдача — перезаписывает. Читает учёт часов по проектам (services/project_hours.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from assistant.lib import sqlite_conn
from assistant.stores import calendar_tasks

MAX_MINUTES = 24 * 60  # больше суток на одну отметку — опечатка


@sqlite_conn.schema_once(lambda: calendar_tasks._conn())  # type: ignore[attr-defined]
def _conn():
    conn = calendar_tasks._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_spent (
            task_id INTEGER PRIMARY KEY,
            minutes INTEGER NOT NULL,
            user_id TEXT NOT NULL,
            at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _lock():
    return calendar_tasks._LOCK  # type: ignore[attr-defined]


def clean_minutes(raw: Any) -> Optional[int]:
    """Минуты из ввода: 90, «1,5», «1 ч 30 мин», «2ч», «45 мин», «день» (8 ч). None — пусто/непонятно."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        m = int(round(float(raw)))
    else:
        import re

        s = str(raw).strip().lower().replace(",", ".")
        if not s:
            return None
        if s in ("день", "1 день", "весь день"):
            return 8 * 60
        h = re.search(r"(\d+(?:\.\d+)?)\s*(?:ч|час)", s)
        mm = re.search(r"(\d+)\s*(?:м|мин)", s)
        if h or mm:
            m = int(round(float(h.group(1)) * 60)) if h else 0
            m += int(mm.group(1)) if mm else 0
        elif re.fullmatch(r"\d+(?:\.\d+)?", s):
            v = float(s)
            m = int(round(v * 60)) if v <= 12 else int(round(v))  # «2» — часы, «90» — минуты
        else:
            return None
    if m <= 0:
        return None
    return min(m, MAX_MINUTES)


def set_spent(task_id: Any, user_id: Any, minutes: Optional[int]) -> None:
    """None или 0 — убрать отметку."""
    with _lock():
        conn = _conn()
        if not minutes:
            conn.execute("DELETE FROM task_spent WHERE task_id = ?", (int(task_id),))
        else:
            conn.execute(
                "INSERT INTO task_spent(task_id, minutes, user_id, at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET minutes = excluded.minutes, user_id = excluded.user_id, at = excluded.at",
                (int(task_id), int(minutes), str(int(user_id)), datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            )
        conn.commit()


def get(task_id: Any) -> Optional[int]:
    with _lock():
        r = _conn().execute("SELECT minutes FROM task_spent WHERE task_id = ?", (int(task_id),)).fetchone()
    return int(r[0]) if r else None


def many(task_ids: Iterable[Any]) -> dict[int, int]:
    ids = sorted({int(t) for t in task_ids})
    out: dict[int, int] = {}
    if not ids:
        return out
    with _lock():
        conn = _conn()
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            q = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT task_id, minutes FROM task_spent WHERE task_id IN ({q})", chunk):
                out[int(r[0])] = int(r[1])
    return out


def recent_for_user(user_id: Any, *, limit: int = 30) -> list[int]:
    with _lock():
        rows = _conn().execute(
            "SELECT minutes FROM task_spent WHERE user_id = ? ORDER BY at DESC LIMIT ?", (str(int(user_id)), int(limit))
        ).fetchall()
    return [int(r[0]) for r in rows]
