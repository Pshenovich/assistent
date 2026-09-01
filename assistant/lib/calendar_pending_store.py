"""Персистентное состояние диалога календаря (переживает рестарт и несколько воркеров)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_HERE = Path(__file__).resolve().parent


def store_path() -> Path:
    raw = os.getenv("CALENDAR_PENDING_STORE_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_HERE / p).resolve()
        return p
    return _HERE / "calendar_pending.json"


def _load_all() -> dict[str, Any]:
    path = store_path()
    try:
        if not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[calendar_pending_store] load_failed err={e!r}")
        return {}


def _save_all(data: dict[str, Any]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _chat_key(chat_id: int, user_id: int | None = None) -> str:
    if user_id is not None:
        return f"{int(chat_id)}:{int(user_id)}"
    return str(int(chat_id))


def get_pending(
    chat_id: int, *, ttl_sec: float, user_id: int | None = None
) -> dict[str, Any] | None:
    key = _chat_key(chat_id, user_id)
    with _lock:
        bucket = _load_all()
        st = bucket.get(key)
    if not isinstance(st, dict):
        return None
    try:
        ts = float(st.get("updated_at") or 0.0)
    except (TypeError, ValueError):
        ts = 0.0
    if ts > 0 and (time.time() - ts) > ttl_sec:
        clear_pending(chat_id, user_id=user_id)
        print(f"[calendar_pending_store] expired chat_id={chat_id} user_id={user_id}")
        return None
    return dict(st)


def set_pending(
    chat_id: int, state: dict[str, Any], *, user_id: int | None = None
) -> None:
    uid = user_id if user_id is not None else int(state.get("user_id") or 0)
    key = _chat_key(chat_id, uid or None)
    st = dict(state)
    st["updated_at"] = time.time()
    with _lock:
        bucket = _load_all()
        bucket[key] = st
        _save_all(bucket)


def clear_pending(chat_id: int, *, user_id: int | None = None) -> bool:
    key = _chat_key(chat_id, user_id)
    with _lock:
        bucket = _load_all()
        if key not in bucket:
            return False
        bucket.pop(key, None)
        _save_all(bucket)
        return True


def put_action_token(
    user_id: int,
    event_id: str,
    *,
    calendar_id: str = "primary",
    ttl_sec: float = 86400.0,
) -> str:
    import secrets

    from assistant.config import GOOGLE_CALENDAR_ID

    token = secrets.token_urlsafe(8)
    key = f"act:{token}"
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    with _lock:
        bucket = _load_all()
        bucket[key] = {
            "user_id": int(user_id),
            "event_id": str(event_id),
            "calendar_id": cal_id,
            "expires_at": time.time() + ttl_sec,
        }
        _save_all(bucket)
    return token


def put_slot_pick_token(user_id: int, start_iso: str) -> str:
    import secrets

    token = secrets.token_urlsafe(6)
    key = f"stp:{token}"
    with _lock:
        bucket = _load_all()
        bucket[key] = {
            "user_id": int(user_id),
            "start_iso": str(start_iso),
            "expires_at": time.time() + 3600,
        }
        _save_all(bucket)
    return token


def put_force_create_token(user_id: int, start_iso: str) -> str:
    import secrets

    token = secrets.token_urlsafe(8)
    key = f"fcf:{token}"
    with _lock:
        bucket = _load_all()
        bucket[key] = {
            "user_id": int(user_id),
            "start_iso": str(start_iso),
            "expires_at": time.time() + 3600,
        }
        _save_all(bucket)
    return token


def pop_force_create_token(token: str, *, user_id: int) -> str | None:
    key = f"fcf:{(token or '').strip()}"
    if not key.startswith("fcf:") or len(key) < 6:
        return None
    with _lock:
        bucket = _load_all()
        rec = bucket.get(key)
        if not isinstance(rec, dict):
            return None
        try:
            if float(rec.get("expires_at") or 0) < time.time():
                bucket.pop(key, None)
                _save_all(bucket)
                return None
        except (TypeError, ValueError):
            pass
        if int(rec.get("user_id") or 0) != int(user_id):
            return None
        start_iso = str(rec.get("start_iso") or "").strip()
        bucket.pop(key, None)
        _save_all(bucket)
        return start_iso or None


def pop_slot_pick_token(token: str, *, user_id: int) -> str | None:
    key = f"stp:{(token or '').strip()}"
    with _lock:
        bucket = _load_all()
        rec = bucket.get(key)
        if not isinstance(rec, dict):
            return None
        try:
            if float(rec.get("expires_at") or 0) < time.time():
                bucket.pop(key, None)
                _save_all(bucket)
                return None
        except (TypeError, ValueError):
            pass
        if int(rec.get("user_id") or 0) != int(user_id):
            return None
        start_iso = str(rec.get("start_iso") or "").strip()
        bucket.pop(key, None)
        _save_all(bucket)
        return start_iso or None


def pop_action_token(token: str, *, user_id: int) -> tuple[str, str] | None:
    from assistant.config import GOOGLE_CALENDAR_ID

    key = f"act:{(token or '').strip()}"
    if not key.startswith("act:") or len(key) < 6:
        return None
    with _lock:
        bucket = _load_all()
        rec = bucket.get(key)
        if not isinstance(rec, dict):
            return None
        try:
            if float(rec.get("expires_at") or 0) < time.time():
                bucket.pop(key, None)
                _save_all(bucket)
                return None
        except (TypeError, ValueError):
            pass
        if int(rec.get("user_id") or 0) != int(user_id):
            return None
        ev = str(rec.get("event_id") or "").strip()
        cal_id = str(rec.get("calendar_id") or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
        bucket.pop(key, None)
        _save_all(bucket)
        if not ev:
            return None
        return ev, cal_id


def put_invite_rsvp_token(
    *,
    organizer_user_id: int,
    invitee_user_id: int,
    invitee_email: str,
    event_id: str,
    calendar_id: str,
    ttl_sec: float = 604800.0,
) -> str:
    import secrets

    from assistant.config import GOOGLE_CALENDAR_ID

    token = secrets.token_urlsafe(8)
    key = f"inv:{token}"
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    with _lock:
        bucket = _load_all()
        bucket[key] = {
            "organizer_user_id": int(organizer_user_id),
            "invitee_user_id": int(invitee_user_id),
            "invitee_email": str(invitee_email or "").strip().lower(),
            "event_id": str(event_id),
            "calendar_id": cal_id,
            "expires_at": time.time() + ttl_sec,
        }
        _save_all(bucket)
    return token


def get_invite_rsvp_token(token: str, *, invitee_user_id: int) -> dict[str, Any] | None:
    key = f"inv:{(token or '').strip()}"
    if not key.startswith("inv:") or len(key) < 6:
        return None
    with _lock:
        bucket = _load_all()
        rec = bucket.get(key)
    if not isinstance(rec, dict):
        return None
    try:
        if float(rec.get("expires_at") or 0) < time.time():
            with _lock:
                bucket = _load_all()
                bucket.pop(key, None)
                _save_all(bucket)
            return None
    except (TypeError, ValueError):
        pass
    if int(rec.get("invitee_user_id") or 0) != int(invitee_user_id):
        return None
    return dict(rec)


def put_event_message_ref(
    chat_id: int,
    message_id: int,
    *,
    user_id: int,
    event_id: str,
    calendar_id: str = "primary",
    summary: str = "",
    start_iso: str = "",
    ttl_sec: float = 2592000.0,
) -> None:
    """Связь message_id бота с событием GCal (реплай «передвинь на…»)."""
    from assistant.config import GOOGLE_CALENDAR_ID

    key = f"evmsg:{int(chat_id)}:{int(message_id)}"
    cal_id = (calendar_id or GOOGLE_CALENDAR_ID).strip() or GOOGLE_CALENDAR_ID
    with _lock:
        bucket = _load_all()
        bucket[key] = {
            "user_id": int(user_id),
            "event_id": str(event_id),
            "calendar_id": cal_id,
            "summary": str(summary or ""),
            "start_iso": str(start_iso or ""),
            "expires_at": time.time() + ttl_sec,
        }
        _save_all(bucket)


def get_event_message_ref(chat_id: int, message_id: int) -> dict[str, Any] | None:
    key = f"evmsg:{int(chat_id)}:{int(message_id)}"
    with _lock:
        rec = _load_all().get(key)
    if not isinstance(rec, dict):
        return None
    try:
        if float(rec.get("expires_at") or 0) < time.time():
            with _lock:
                bucket = _load_all()
                bucket.pop(key, None)
                _save_all(bucket)
            return None
    except (TypeError, ValueError):
        pass
    return dict(rec)


def find_by_prompt_message(
    chat_id: int,
    message_id: int,
    *,
    ttl_sec: float,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    """Ответ реплаем на сообщение бота с уточняющим вопросом."""
    st = get_pending(chat_id, ttl_sec=ttl_sec, user_id=user_id)
    if not st:
        return None
    try:
        mid = int(st.get("prompt_message_id") or 0)
    except (TypeError, ValueError):
        mid = 0
    if mid and mid == int(message_id):
        return st
    return None
