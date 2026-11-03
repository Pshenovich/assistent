"""Продуктовая аналитика: события и минуты активности.

Пишем только идентификаторы, типы и числа — никаких текстов заметок, промптов
и сообщений. База своя (data/analytics.sqlite): это первичная запись в РФ,
во внешний инструмент (PostHog) события досылает assistant/lib/analytics_forward.

Запись идёт через очередь и отдельный поток: track()/touch() не ждут диск и
никогда не бросают исключений — аналитика не должна ломать продукт.

Время в продукте считаем по минутам активности: одна строка на (пользователь,
минута, платформа). Сессия — подряд идущие минуты одной платформы с перерывом
не больше SESSION_GAP_MIN.
"""

from __future__ import annotations

import contextvars
import json
import os
import queue
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from assistant.lib import sqlite_conn

SESSION_GAP_MIN = 30
RETENTION_DAYS = 400
_MAX_PROPS = 20
_MAX_STR = 80

# web / pwa — браузер; tma-* — Telegram-миниапп (платформа из Telegram.WebApp.platform);
# tg_bot — чат с ботом; server — фоновые задачи (доставка дайджеста и т.п.).
_TMA_KINDS = ("ios", "android", "android_x", "tdesktop", "macos", "weba", "webk", "web", "unigram", "unknown")
PLATFORMS = frozenset(["web", "pwa", "tg_bot", "server"] + [f"tma-{k}" for k in _TMA_KINDS])

_EVENT_RE = re.compile(r"^[a-z0-9_.:\-]{1,64}$")
_KEY_RE = re.compile(r"^[a-z0-9_]{1,40}$")

current_platform: contextvars.ContextVar[str] = contextvars.ContextVar("leo_analytics_platform", default="")

_lock = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None

_queue: "queue.Queue[tuple]" = queue.Queue(maxsize=20_000)
_writer: threading.Thread | None = None
_writer_lock = threading.Lock()

# (user, platform) → последняя записанная минута: не пишем одну минуту дважды.
_last_minute: dict[tuple[str, str], int] = {}
# ключ прореживания → время последнего события
_throttle: dict[str, float] = {}


def normalize_platform(raw: Any) -> str:
    p = str(raw or "").strip().lower()
    return p if p in PLATFORMS else ""


def set_platform(raw: Any) -> str:
    """Ставит платформу текущего запроса/апдейта; возвращает принятое значение."""
    p = normalize_platform(raw)
    current_platform.set(p)
    return p


def db_path() -> Path:
    raw = os.getenv("ANALYTICS_DB_PATH", "").strip()
    from assistant.config import ROOT

    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    return ROOT / "data" / "analytics.sqlite"


def _conn() -> sqlite3.Connection:
    """Соединение текущего пути; вызывать под _lock."""
    global _CONN, _CONN_PATH
    path = db_path()
    if _CONN is not None and _CONN_PATH == path:
        return _CONN
    if _CONN is not None:
        _CONN.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite_conn.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            day TEXT NOT NULL,
            user_id TEXT NOT NULL,
            event TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT '',
            props_json TEXT,
            forwarded INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_events_day_user ON events(day, user_id);
        CREATE INDEX IF NOT EXISTS idx_events_event_day ON events(event, day);
        CREATE INDEX IF NOT EXISTS idx_events_fwd ON events(forwarded, id);
        CREATE TABLE IF NOT EXISTS activity_minutes (
            user_id TEXT NOT NULL,
            minute INTEGER NOT NULL,
            platform TEXT NOT NULL,
            PRIMARY KEY (user_id, minute, platform)
        );
        CREATE INDEX IF NOT EXISTS idx_activity_minute ON activity_minutes(minute);
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            platform TEXT NOT NULL,
            start_minute INTEGER NOT NULL,
            end_minute INTEGER NOT NULL,
            minutes INTEGER NOT NULL DEFAULT 1,
            emitted INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id, platform, end_minute);
        CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions(emitted, end_minute);
        CREATE TABLE IF NOT EXISTS user_daily_snapshot (
            day TEXT NOT NULL,
            user_id TEXT NOT NULL,
            metric TEXT NOT NULL,
            value REAL NOT NULL,
            PRIMARY KEY (day, user_id, metric)
        );
        """
    )
    conn.commit()
    _CONN, _CONN_PATH = conn, path
    return conn


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _clean_props(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in props.items():
        if len(out) >= _MAX_PROPS:
            break
        key = str(k)
        if not _KEY_RE.match(key) or v is None:
            continue
        if isinstance(v, bool) or isinstance(v, int):
            out[key] = v
        elif isinstance(v, float):
            out[key] = round(v, 3)
        elif isinstance(v, str):
            out[key] = v[:_MAX_STR]
    return out


def _uid(user_id: Any) -> str:
    s = str(user_id or "").strip()
    return s if s and s != "__unknown__" else ""


# --- запись --------------------------------------------------------------------


def track(user_id: Any, event: str, *, platform: str | None = None, **props: Any) -> None:
    """Событие продукта. Не бросает исключений и не ждёт диск."""
    try:
        uid = _uid(user_id)
        if not uid or not _EVENT_RE.match(str(event or "")):
            return
        p = normalize_platform(platform) if platform is not None else current_platform.get()
        _enqueue(("event", time.time(), uid, str(event), p, _clean_props(props)))
    except Exception as e:
        print(f"[analytics] track_fail event={event!r} err={e!r}")


def track_throttled(key: str, seconds: float, user_id: Any, event: str, **kw: Any) -> None:
    """Как track, но не чаще раза в `seconds` на ключ (правки идут потоком)."""
    now = time.time()
    k = f"{user_id}|{key}"
    last = _throttle.get(k)
    if last is not None and now - last < seconds:
        return
    _throttle[k] = now
    if len(_throttle) > 50_000:
        _throttle.clear()
    track(user_id, event, **kw)


def touch(user_id: Any, platform: str | None = None) -> None:
    """Отмечает минуту активности пользователя на платформе."""
    try:
        uid = _uid(user_id)
        p = normalize_platform(platform) if platform is not None else current_platform.get()
        if not uid or not p:
            return
        now = time.time()
        minute = int(now // 60)
        key = (uid, p)
        if _last_minute.get(key) == minute:
            return
        _last_minute[key] = minute
        if len(_last_minute) > 50_000:
            _last_minute.clear()
        _enqueue(("touch", now, uid, p, minute))
    except Exception as e:
        print(f"[analytics] touch_fail err={e!r}")


def _enqueue(item: tuple) -> None:
    _ensure_writer()
    try:
        _queue.put_nowait(item)
    except queue.Full:
        print("[analytics] queue_full drop")


def _ensure_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(target=_writer_loop, name="analytics-writer", daemon=True)
        _writer.start()


def _writer_loop() -> None:
    while True:
        item = _queue.get()
        batch = [item]
        try:
            while len(batch) < 500:
                batch.append(_queue.get_nowait())
        except queue.Empty:
            pass
        try:
            _write_batch(batch)
        except Exception as e:
            print(f"[analytics] write_fail n={len(batch)} err={e!r}")
        finally:
            for _ in batch:
                _queue.task_done()


def _write_batch(batch: list[tuple]) -> None:
    with _lock:
        conn = _conn()
        with conn:
            for item in batch:
                if item[0] == "event":
                    _, ts, uid, event, p, props = item
                    conn.execute(
                        "INSERT INTO events(ts, day, user_id, event, platform, props_json) VALUES (?,?,?,?,?,?)",
                        (ts, _day(ts), uid, event, p, json.dumps(props, ensure_ascii=False) if props else None),
                    )
                elif item[0] == "touch":
                    _, ts, uid, p, minute = item
                    _write_touch(conn, uid, p, minute)


def _write_touch(conn: sqlite3.Connection, uid: str, p: str, minute: int) -> None:
    cur = conn.execute(
        "INSERT OR IGNORE INTO activity_minutes(user_id, minute, platform) VALUES (?,?,?)",
        (uid, minute, p),
    )
    if cur.rowcount == 0:
        return
    row = conn.execute(
        "SELECT id, end_minute FROM sessions WHERE user_id=? AND platform=? AND end_minute>=? "
        "ORDER BY end_minute DESC LIMIT 1",
        (uid, p, minute - SESSION_GAP_MIN),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO sessions(user_id, platform, start_minute, end_minute, minutes) VALUES (?,?,?,?,1)",
            (uid, p, minute, minute),
        )
    else:
        conn.execute(
            "UPDATE sessions SET end_minute=MAX(end_minute, ?), minutes=minutes+1 WHERE id=?",
            (minute, int(row["id"])),
        )


def flush(timeout: float = 5.0) -> None:
    """Дождаться записи очереди (тесты, остановка процесса)."""
    if _writer is None:
        return
    deadline = time.time() + timeout
    while _queue.unfinished_tasks and time.time() < deadline:
        time.sleep(0.01)


# --- сессии и пересылка --------------------------------------------------------


def emit_closed_sessions(now: float | None = None) -> int:
    """Закрытые сессии → событие `session` (для внешнего инструмента). Возвращает число."""
    cutoff = int((now if now is not None else time.time()) // 60) - SESSION_GAP_MIN
    with _lock:
        conn = _conn()
        rows = conn.execute(
            "SELECT id, user_id, platform, start_minute, end_minute, minutes FROM sessions "
            "WHERE emitted=0 AND end_minute<? ORDER BY id LIMIT 2000",
            (cutoff,),
        ).fetchall()
        if not rows:
            return 0
        with conn:
            for r in rows:
                ts = float(r["start_minute"]) * 60
                props = {"duration_min": int(r["minutes"]), "session_id": f"s{int(r['id'])}"}
                conn.execute(
                    "INSERT INTO events(ts, day, user_id, event, platform, props_json) VALUES (?,?,?,?,?,?)",
                    (ts, _day(ts), r["user_id"], "session", r["platform"], json.dumps(props)),
                )
                conn.execute("UPDATE sessions SET emitted=1 WHERE id=?", (int(r["id"]),))
        return len(rows)


def pending_forward(limit: int = 500) -> list[dict[str, Any]]:
    with _lock:
        rows = _conn().execute(
            "SELECT id, ts, user_id, event, platform, props_json FROM events WHERE forwarded=0 ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["props"] = json.loads(d.pop("props_json") or "{}")
        except ValueError:
            d["props"] = {}
        out.append(d)
    return out


def mark_forwarded(ids: Iterable[int]) -> None:
    ids = [int(i) for i in ids]
    if not ids:
        return
    with _lock:
        conn = _conn()
        with conn:
            conn.executemany("UPDATE events SET forwarded=1 WHERE id=?", [(i,) for i in ids])


def purge_old(now: float | None = None) -> None:
    """Сырые события и минуты старше RETENTION_DAYS."""
    t = (now if now is not None else time.time()) - RETENTION_DAYS * 86400
    with _lock:
        conn = _conn()
        with conn:
            conn.execute("DELETE FROM events WHERE ts<?", (t,))
            conn.execute("DELETE FROM activity_minutes WHERE minute<?", (int(t // 60),))
            conn.execute("DELETE FROM sessions WHERE end_minute<? AND emitted=1", (int(t // 60),))


# --- снимки сущностей ----------------------------------------------------------


def save_snapshot(day: str, rows: Iterable[tuple[str, str, float]]) -> None:
    """rows: (user_id, metric, value)."""
    with _lock:
        conn = _conn()
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO user_daily_snapshot(day, user_id, metric, value) VALUES (?,?,?,?)",
                [(day, str(u), str(m), float(v)) for u, m, v in rows],
            )


# --- чтение для /dashboard -----------------------------------------------------


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _conn().execute(sql, params).fetchall()
