"""Google Календарь без поминутного опроса: подписка на изменения + копия событий.

Раньше напоминания, приглашения и брифы раз в минуту спрашивали Google о каждом
человеке по каждому календарю (~3 запроса × календари в минуту) — при сотнях людей это
упирается в дневную квоту Google. Теперь (GCAL_PUSH_ENABLED=1):

- на каждый календарь оформлена подписка (events.watch, ~неделя; продлевает воркер);
- Google стучится в /webhook/google-calendar при любом изменении → «календарь изменился»
  (отметка в общей базе: веб и воркер — разные процессы);
- фоновые задачи читают копию событий за [−1 ч, +15 дн]; копия перечитывается из Google,
  только если пришло изменение, и для страховки раз в GCAL_CACHE_TTL_SEC (30 мин).

У кого подписки нет (флаг выключен, Google её не принял, нет HTTPS-адреса) — всё как
раньше: живой запрос. Хуже не становится ни в каком случае.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from assistant.lib import sqlite_conn

WINDOW_BEFORE = timedelta(hours=1)
WINDOW_AFTER = timedelta(days=15)
CHANNEL_TTL_SEC = 7 * 24 * 3600
RENEW_BEFORE_SEC = 24 * 3600
WEBHOOK_PATH = "/webhook/google-calendar"

_lock = threading.Lock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None


def enabled() -> bool:
    return os.getenv("GCAL_PUSH_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def cache_ttl_sec() -> float:
    try:
        return max(60.0, float(os.getenv("GCAL_CACHE_TTL_SEC", "1800") or "1800"))
    except ValueError:
        return 1800.0


def db_path() -> Path:
    raw = os.getenv("GCAL_PUSH_DB_PATH", "").strip()
    from assistant.config import ROOT

    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (ROOT / p).resolve()
    return ROOT / "data" / "gcal_push.sqlite"


def _conn() -> sqlite3.Connection:
    """Под _lock."""
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
        CREATE TABLE IF NOT EXISTS gcal_channels (
            channel_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            calendar_id TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            expiration REAL NOT NULL,
            created REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_gcal_ch_user ON gcal_channels(user_id, calendar_id);
        CREATE TABLE IF NOT EXISTS gcal_cache (
            user_id TEXT PRIMARY KEY,
            fetched_at REAL NOT NULL,
            win_from TEXT NOT NULL,
            win_to TEXT NOT NULL,
            clean_seq INTEGER NOT NULL DEFAULT 0,
            events TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gcal_dirty (
            user_id TEXT PRIMARY KEY,
            seq INTEGER NOT NULL
        );
        """
    )
    conn.commit()
    _CONN, _CONN_PATH = conn, path
    return conn


# ── токен канала: Google вернёт его в заголовке, по нему отличаем свои уведомления ──

def _secret() -> bytes:
    raw = os.getenv("GCAL_PUSH_SECRET", "").strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    return hashlib.sha256(("gcal-push:" + raw).encode()).digest()


def channel_token(channel_id: str) -> str:
    return hmac.new(_secret(), channel_id.encode(), hashlib.sha256).hexdigest()


# ── отметка «изменилось» ─────────────────────────────────────────────────────

def mark_dirty(user_id: str | int) -> None:
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO gcal_dirty(user_id, seq) VALUES (?, 1) "
            "ON CONFLICT(user_id) DO UPDATE SET seq = seq + 1",
            (str(user_id),),
        )
        conn.commit()


def _dirty_seq(conn: sqlite3.Connection, uid: str) -> int:
    row = conn.execute("SELECT seq FROM gcal_dirty WHERE user_id = ?", (uid,)).fetchone()
    return int(row["seq"]) if row else 0


# ── каналы ───────────────────────────────────────────────────────────────────

def active_channel_calendars(user_id: str | int, *, min_left_sec: float = 300) -> set[str]:
    with _lock:
        rows = _conn().execute(
            "SELECT calendar_id FROM gcal_channels WHERE user_id = ? AND expiration > ?",
            (str(user_id), time.time() + min_left_sec),
        ).fetchall()
    return {str(r["calendar_id"]) for r in rows}


def _webhook_url() -> str | None:
    from assistant.lib.webapp_public import public_base_url

    base = public_base_url()
    if not base.startswith("https://"):
        return None  # Google шлёт уведомления только на HTTPS
    return base + WEBHOOK_PATH


def _mark_no_push(uid: str, cal_id: str) -> None:
    """Запись-заглушка «подписка невозможна» — раз в неделю проверим снова."""
    with _lock:
        conn = _conn()
        conn.execute("DELETE FROM gcal_channels WHERE user_id = ? AND calendar_id = ?", (uid, cal_id))
        conn.execute(
            "INSERT INTO gcal_channels(channel_id, user_id, calendar_id, resource_id, expiration, created) "
            "VALUES (?, ?, ?, '', ?, ?)",
            (f"nopush:{uid}:{cal_id}", uid, cal_id, time.time() + CHANNEL_TTL_SEC, time.time()),
        )
        conn.commit()


def ensure_channels(user_id: int) -> dict[str, Any]:
    """Подписки на все активные календари человека; продлевает истекающие. Возвращает счётчики."""
    from assistant.services import calendar_sources as cal_sources

    out = {"created": 0, "kept": 0, "failed": 0, "stopped": 0}
    url = _webhook_url()
    if not url:
        out["failed"] = -1
        return out
    svc = cal_sources._service(user_id)
    uid = str(user_id)
    cal_ids = cal_sources.get_active_calendar_ids(user_id)
    fresh = active_channel_calendars(uid, min_left_sec=RENEW_BEFORE_SEC)
    for cal_id in cal_ids:
        if cal_id in fresh:
            out["kept"] += 1
            continue
        ch_id = uuid.uuid4().hex
        try:
            res = svc.events().watch(
                calendarId=cal_id,
                body={
                    "id": ch_id,
                    "type": "web_hook",
                    "address": url,
                    "token": channel_token(ch_id),
                    "params": {"ttl": str(CHANNEL_TTL_SEC)},
                },
            ).execute()
        except Exception as e:
            if "pushNotSupportedForRequestedResource" in repr(e):
                # Праздники и другие календари Google без подписок: почти не меняются —
                # считаем покрытыми, их хватает страховочного перечитывания копии.
                _mark_no_push(uid, cal_id)
                out["kept"] += 1
                continue
            out["failed"] += 1
            print(f"[gcal_push] watch failed uid={uid} cal={cal_id} err={e!r}")
            continue
        exp_ms = float(res.get("expiration") or 0) or (time.time() + CHANNEL_TTL_SEC) * 1000
        with _lock:
            conn = _conn()
            old = conn.execute(
                "SELECT channel_id, resource_id FROM gcal_channels WHERE user_id = ? AND calendar_id = ?",
                (uid, cal_id),
            ).fetchall()
            conn.execute(
                "INSERT INTO gcal_channels(channel_id, user_id, calendar_id, resource_id, expiration, created) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ch_id, uid, cal_id, str(res.get("resourceId") or ""), exp_ms / 1000.0, time.time()),
            )
            conn.commit()
        out["created"] += 1
        for o in old:  # старая подписка больше не нужна — снимаем, чтобы не приходили дубли
            if str(o["channel_id"]).startswith("nopush:"):
                continue
            try:
                svc.channels().stop(body={"id": o["channel_id"], "resourceId": o["resource_id"]}).execute()
            except Exception as e:
                print(f"[gcal_push] stop failed uid={uid} ch={o['channel_id']} err={e!r}")
            with _lock:
                conn = _conn()
                conn.execute("DELETE FROM gcal_channels WHERE channel_id = ?", (o["channel_id"],))
                conn.commit()
            out["stopped"] += 1
    if out["created"]:
        mark_dirty(uid)  # пока подписки не было, изменения могли пройти мимо
    return out


# ── копия событий ────────────────────────────────────────────────────────────

def _covered(user_id: int) -> bool:
    """Все активные календари человека под подпиской."""
    from assistant.services import calendar_sources as cal_sources

    have = active_channel_calendars(user_id)
    if not have:
        return False
    try:
        need = set(cal_sources.get_active_calendar_ids(user_id))
    except Exception:
        return False
    return bool(need) and need <= have


def _refresh(user_id: int, now: datetime, need_to: datetime) -> list[dict[str, Any]]:
    from assistant.services import calendar_sources as cal_sources

    uid = str(user_id)
    with _lock:
        seq = _dirty_seq(_conn(), uid)
    win_from, win_to = now - WINDOW_BEFORE, max(now + WINDOW_AFTER, need_to)
    events = cal_sources.list_events_in_window(user_id, win_from, win_to)
    raw = [{"calendar_id": e.get("calendar_id"), "raw": e.get("raw")} for e in events if isinstance(e.get("raw"), dict)]
    with _lock:
        conn = _conn()
        conn.execute(
            "INSERT INTO gcal_cache(user_id, fetched_at, win_from, win_to, clean_seq, events) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET fetched_at = excluded.fetched_at, win_from = excluded.win_from, "
            "win_to = excluded.win_to, clean_seq = excluded.clean_seq, events = excluded.events",
            (uid, time.time(), win_from.isoformat(), win_to.isoformat(), seq, json.dumps(raw, ensure_ascii=False)),
        )
        conn.commit()
    return events


def cached_events(user_id: int, time_min: datetime, time_max: datetime) -> list[dict[str, Any]] | None:
    """События из копии, пересекающие [time_min, time_max]; None — копией пользоваться нельзя."""
    if not enabled() or not _covered(user_id):
        return None
    from assistant.services import calendar_sources as cal_sources

    uid = str(user_id)
    with _lock:
        conn = _conn()
        row = conn.execute("SELECT * FROM gcal_cache WHERE user_id = ?", (uid,)).fetchone()
        seq = _dirty_seq(conn, uid)
    fresh = (
        row is not None
        and int(row["clean_seq"]) >= seq
        and time.time() - float(row["fetched_at"]) < cache_ttl_sec()
        and datetime.fromisoformat(row["win_from"]) <= time_min
        and datetime.fromisoformat(row["win_to"]) >= time_max
    )
    if fresh:
        events = []
        for it in json.loads(row["events"]):
            norm = cal_sources._normalize_event(it["raw"], calendar_id=str(it["calendar_id"]), user_id=user_id)
            if norm:
                events.append(norm)
        events = cal_sources.dedupe_events(events)
    else:
        try:
            events = _refresh(user_id, datetime.now(time_min.tzinfo), time_max)
        except Exception as e:
            print(f"[gcal_push] refresh failed uid={uid} err={e!r} -> live")
            return None
    return [e for e in events if e["start"] < time_max and e["end"] > time_min]


# ── уведомление от Google ────────────────────────────────────────────────────

def handle_notification(headers: dict[str, str]) -> int:
    """Заголовки X-Goog-* → отметка «календарь изменился». Возвращает HTTP-код."""
    h = {k.lower(): v for k, v in headers.items()}
    ch_id = str(h.get("x-goog-channel-id") or "").strip()
    token = str(h.get("x-goog-channel-token") or "").strip()
    state = str(h.get("x-goog-resource-state") or "").strip().lower()
    if not ch_id or not hmac.compare_digest(token, channel_token(ch_id)):
        return 403
    with _lock:
        row = _conn().execute("SELECT user_id FROM gcal_channels WHERE channel_id = ?", (ch_id,)).fetchone()
    if row is None:
        return 200  # старая или снятая подписка — Google перестанет слать после stop/истечения
    if state != "sync":  # sync — проверка при оформлении подписки, событий ещё нет
        mark_dirty(row["user_id"])
        try:
            from assistant.realtime import notify as realtime_notify

            realtime_notify.calendar_changed(row["user_id"])  # открытый экран обновится сам
        except Exception as e:
            print(f"[gcal_push] realtime notify failed uid={row['user_id']} err={e!r}")
    return 200
