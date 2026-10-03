"""Присутствие в обсуждении заметки/чата (для live UI и пуш-уведомлений)."""

from __future__ import annotations

import threading
import time
from typing import Any

_LOCK = threading.Lock()
_PRESENCE: dict[tuple[str, str, str], dict[str, dict[str, Any]]] = {}
_TTL_SEC = 18.0


def _uid(value: int | str) -> str:
    return str(value).strip()


def _key(owner_user_id: int | str, kind: str, item_id: str) -> tuple[str, str, str]:
    return (_uid(owner_user_id), str(kind or "").strip(), str(item_id or "").strip())


def heartbeat(
    owner_user_id: int | str,
    kind: str,
    item_id: str,
    user_id: int | str,
    *,
    display_name: str = "",
    username: str | None = None,
) -> list[str]:
    """Отметить зрителя обсуждения. Возвращает user_id всех «онлайн»."""
    key = _key(owner_user_id, kind, item_id)
    uid = _uid(user_id)
    now = time.time()
    with _LOCK:
        room = _PRESENCE.setdefault(key, {})
        stale = [
            pid
            for pid, info in room.items()
            if now - float(info.get("ts") or 0) > _TTL_SEC
        ]
        for pid in stale:
            room.pop(pid, None)
        room[uid] = {
            "user_id": uid,
            "name": (display_name or "").strip() or "Участник",
            "username": (username or "").strip().lstrip("@") or None,
            "ts": now,
        }
        return sorted(room.keys())


def leave(owner_user_id: int | str, kind: str, item_id: str, user_id: int | str) -> None:
    key = _key(owner_user_id, kind, item_id)
    uid = _uid(user_id)
    with _LOCK:
        room = _PRESENCE.get(key)
        if not room:
            return
        room.pop(uid, None)
        if not room:
            _PRESENCE.pop(key, None)


def present_user_ids(owner_user_id: int | str, kind: str, item_id: str) -> set[str]:
    key = _key(owner_user_id, kind, item_id)
    now = time.time()
    with _LOCK:
        room = _PRESENCE.get(key) or {}
        live: set[str] = set()
        for pid, info in list(room.items()):
            if now - float(info.get("ts") or 0) > _TTL_SEC:
                room.pop(pid, None)
                continue
            live.add(pid)
        if not room:
            _PRESENCE.pop(key, None)
        return live
