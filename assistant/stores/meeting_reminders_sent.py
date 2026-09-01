"""Уже отправленные напоминания за 15 мин до встречи."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from assistant.config import ROOT

_lock = threading.RLock()


def _path() -> Path:
    raw = os.getenv("MEETING_REMINDERS_SENT_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
    else:
        p = (ROOT / "data" / "meeting_reminders_sent.json").resolve()
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
    cutoff = time.time() - 86400 * 14
    pruned = {k: v for k, v in sent.items() if v >= cutoff}
    with _lock:
        path.write_text(
            json.dumps({"sent": pruned}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def reminder_key(
    user_id: int, calendar_id: str, event_id: str, start_iso: str
) -> str:
    return f"{int(user_id)}:{calendar_id}:{event_id}:{start_iso}"


def was_sent(
    user_id: int, calendar_id: str, event_id: str, start_iso: str
) -> bool:
    key = reminder_key(user_id, calendar_id, event_id, start_iso)
    legacy = f"{int(user_id)}:{event_id}:{start_iso}"
    sent = _load()
    return key in sent or legacy in sent


def mark_sent(
    user_id: int, calendar_id: str, event_id: str, start_iso: str
) -> None:
    key = reminder_key(user_id, calendar_id, event_id, start_iso)
    sent = _load()
    sent[key] = time.time()
    _save(sent)
