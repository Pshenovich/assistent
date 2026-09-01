"""Недавние Zoom-встречи пользователя (instant + scheduled), созданные через бота."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from assistant.config import ROOT

_lock = threading.RLock()
_MAX_ITEMS = 30
_MAX_AGE_SEC = 86400 * 14


def _path(user_id: int) -> Path:
    d = (ROOT / "data" / "zoom_recent_meetings").resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{int(user_id)}.json"


def _load(user_id: int) -> list[dict[str, Any]]:
    path = _path(user_id)
    with _lock:
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return []
    items = data.get("meetings") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict)]


def _save(user_id: int, items: list[dict[str, Any]]) -> None:
    path = _path(user_id)
    payload = {"meetings": items, "_updated_unix": int(time.time())}
    with _lock:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _prune(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.time()
    out: list[dict[str, Any]] = []
    for rec in items:
        try:
            created = float(rec.get("created_at") or 0)
        except (TypeError, ValueError):
            created = 0
        if created and now - created > _MAX_AGE_SEC:
            continue
        mid = str(rec.get("id") or "").strip()
        if not mid:
            continue
        out.append(rec)
    out.sort(key=lambda x: float(x.get("created_at") or 0), reverse=True)
    return out[:_MAX_ITEMS]


def register_meeting(user_id: int, meeting: dict[str, Any], *, source: str = "bot") -> None:
    mid = str(meeting.get("id") or "").strip()
    if not mid:
        return
    topic = str(meeting.get("topic") or "Встреча").strip() or "Встреча"
    start_time = str(meeting.get("start_time") or "").strip()
    try:
        mtype = int(meeting.get("type") or 0)
    except (TypeError, ValueError):
        mtype = 0
    join_url = str(meeting.get("join_url") or "").strip()
    rec = {
        "id": mid,
        "topic": topic,
        "start_time": start_time,
        "type": mtype,
        "join_url": join_url,
        "created_at": time.time(),
        "source": source,
    }
    items = _prune(_load(user_id))
    items = [x for x in items if str(x.get("id") or "") != mid]
    items.insert(0, rec)
    _save(user_id, _prune(items))


def remove_meeting(user_id: int, meeting_id: str) -> None:
    mid = str(meeting_id or "").strip()
    if not mid:
        return
    items = [x for x in _load(user_id) if str(x.get("id") or "") != mid]
    _save(user_id, items)


def _match_topic(meeting: dict[str, Any], query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    topic = str(meeting.get("topic") or "").lower()
    return q in topic or topic in q


def _match_day(meeting: dict[str, Any], day: str, tz) -> bool:
    d = (day or "").strip()[:10]
    if not d:
        return True
    st_s = str(meeting.get("start_time") or "").strip()
    if st_s:
        try:
            st = datetime.fromisoformat(st_s.replace("Z", "+00:00")).astimezone(tz)
            if st.date().isoformat() == d:
                return True
        except ValueError:
            pass
    try:
        created = float(meeting.get("created_at") or 0)
    except (TypeError, ValueError):
        created = 0
    if created:
        from datetime import timezone as tz_mod

        cdt = datetime.fromtimestamp(created, tz=tz_mod.utc).astimezone(tz)
        if cdt.date().isoformat() == d:
            return True
    return False


def list_recent(
    user_id: int,
    match_query: str,
    match_date: str | None,
    *,
    tz,
) -> list[dict[str, Any]]:
    items = _prune(_load(user_id))
    out: list[dict[str, Any]] = []
    for m in items:
        if not _match_topic(m, match_query):
            continue
        if not _match_day(m, match_date or "", tz):
            continue
        out.append(dict(m))
    return out
