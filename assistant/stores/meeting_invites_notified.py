"""Уже отправленные приглашения на встречу в Telegram."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from assistant.config import ROOT

_lock = threading.RLock()


def _path() -> Path:
    raw = os.getenv("MEETING_INVITES_NOTIFIED_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
    else:
        p = (ROOT / "data" / "meeting_invites_notified.json").resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict[str, float]:
    path = _path()
    with _lock:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sent = data.get("sent") if isinstance(data, dict) else {}
            return {str(k): float(v) for k, v in (sent or {}).items()}
        except Exception:
            return {}


def _save(sent: dict[str, float]) -> None:
    path = _path()
    cutoff = time.time() - 86400 * 21
    pruned = {k: v for k, v in sent.items() if v >= cutoff}
    with _lock:
        path.write_text(
            json.dumps({"sent": pruned}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def invite_key(user_id: int, event_id: str) -> str:
    return f"{int(user_id)}:{str(event_id or '').strip()}"


def was_notified(user_id: int, event_id: str) -> bool:
    key = invite_key(user_id, event_id)
    return key in _load()


def mark_notified(user_id: int, event_id: str) -> None:
    key = invite_key(user_id, event_id)
    if not str(event_id or "").strip():
        return
    sent = _load()
    sent[key] = time.time()
    _save(sent)
