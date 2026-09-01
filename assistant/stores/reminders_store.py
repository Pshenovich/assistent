"""Хранение напоминаний бота (reminders.json). Общий модуль для bot.py и usage_server (мини-приложение)."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

_lock = threading.Lock()


def parse_when_iso(
    when_iso: Any,
    *,
    tz: ZoneInfo | None = None,
) -> datetime | None:
    raw = str(when_iso or "").strip()
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        from assistant.config import CALENDAR_TZ

        when = when.replace(tzinfo=tz or ZoneInfo(CALENDAR_TZ))
    return when
def store_path() -> Path:
    from assistant.config import ROOT

    raw = os.getenv("REMINDERS_STORE_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (ROOT / p).resolve()
        return p
    return ROOT / "reminders.json"


def load_reminders() -> list[dict[str, Any]]:
    path = store_path()
    try:
        if not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[reminders_store] load_failed err={e!r}")
        return []


def save_reminders(items: list[dict[str, Any]]) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(items, ensure_ascii=False, indent=2)
    with _lock:
        path.write_text(text, encoding="utf-8")


def get_reminder_by_id(reminder_id: str) -> dict[str, Any] | None:
    rid = (reminder_id or "").strip()
    if not rid:
        return None
    for it in load_reminders():
        if isinstance(it, dict) and str(it.get("id") or "").strip() == rid:
            return it
    return None


def find_reminder_by_message(chat_id: int, message_id: int) -> dict[str, Any] | None:
    for it in load_reminders():
        if not isinstance(it, dict):
            continue
        if int(it.get("chat_id") or 0) != int(chat_id):
            continue
        if int(it.get("reminder_message_id") or 0) == int(message_id):
            return it
    return None


def remove_reminder(reminder_id: str) -> None:
    rid = (reminder_id or "").strip()
    if not rid:
        return
    items = load_reminders()
    kept = [x for x in items if str((x or {}).get("id") or "").strip() != rid]
    if len(kept) != len(items):
        save_reminders(kept)


def set_reminder_message_id(reminder_id: str, message_id: int) -> None:
    rid = (reminder_id or "").strip()
    if not rid:
        return
    items = load_reminders()
    for it in items:
        if isinstance(it, dict) and str(it.get("id") or "").strip() == rid:
            it["reminder_message_id"] = int(message_id)
            save_reminders(items)
            return


def update_reminder_when(reminder_id: str, new_when_iso: str) -> dict[str, Any] | None:
    rid = (reminder_id or "").strip()
    if not rid:
        return None
    items = load_reminders()
    updated: dict[str, Any] | None = None
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("id") or "").strip() == rid:
            it["when_iso"] = new_when_iso
            updated = it
            break
    if updated is not None:
        save_reminders(items)
    return updated


def _normalize_checklist(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or "").strip()
        if not text:
            continue
        rid = str(it.get("id") or "").strip()
        if not rid:
            import uuid

            rid = "c" + uuid.uuid4().hex[:10]
        out.append(
            {
                "id": rid,
                "text": text,
                "done": bool(it.get("done")),
            }
        )
    return out


def patch_reminder(
    reminder_id: str,
    *,
    task: str | None = None,
    when_iso: str | None = None,
    done: bool | None = None,
    checklist: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Частичное обновление записи. Возвращает обновлённый dict или None."""
    rid = (reminder_id or "").strip()
    if not rid:
        return None
    items = load_reminders()
    out: dict[str, Any] | None = None
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("id") or "").strip() != rid:
            continue
        if task is not None:
            it["task"] = str(task).strip()
        if when_iso is not None:
            it["when_iso"] = str(when_iso).strip()
        if done is not None:
            if done:
                it["done"] = True
            else:
                it.pop("done", None)
        if checklist is not None:
            it["checklist"] = _normalize_checklist(checklist)
        out = it
        break
    if out is not None:
        save_reminders(items)
    return out


def add_reminder(
    *,
    user_id: int,
    chat_id: int,
    task: str,
    when_iso: str,
    checklist: list[dict[str, Any]] | None = None,
) -> str:
    import uuid

    rid = uuid.uuid4().hex[:12]
    items = load_reminders()
    row = {
        "id": rid,
        "user_id": int(user_id),
        "chat_id": int(chat_id),
        "task": (task or "").strip(),
        "when_iso": when_iso,
        "done": False,
    }
    normalized_checklist = _normalize_checklist(checklist)
    if normalized_checklist:
        row["checklist"] = normalized_checklist
    items.append(row)
    save_reminders(items)
    return rid


def reminders_for_user(telegram_user_id: int) -> list[dict[str, Any]]:
    """Все напоминания пользователя (включая выполненные)."""
    uid = int(telegram_user_id)
    rows: list[dict[str, Any]] = []
    for it in load_reminders():
        if not isinstance(it, dict):
            continue
        try:
            if int(it.get("user_id") or 0) != uid:
                continue
        except (TypeError, ValueError):
            continue
        rows.append(it)
    return rows
