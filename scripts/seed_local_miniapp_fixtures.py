#!/usr/bin/env python3
"""Сиды тестовых напоминаний для локального миниаппа (MINIAPP_DEV)."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from assistant.stores import reminders_store


def _uid() -> int:
    raw = (os.getenv("MINIAPP_DEV_TELEGRAM_USER_ID") or "").strip()
    if not raw:
        raise SystemExit("Задайте MINIAPP_DEV_TELEGRAM_USER_ID в .env")
    return int(raw)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="minutes")


def main() -> None:
    uid = _uid()
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fixtures = [
        {
            "id": "local-dev-rem-1",
            "user_id": uid,
            "chat_id": uid,
            "task": "Отправить отчёт по спринту",
            "when_iso": _iso(today + timedelta(days=1, hours=18)),
            "done": False,
        },
        {
            "id": "local-dev-rem-2",
            "user_id": uid,
            "chat_id": uid,
            "task": "Позвонить в сервисный центр",
            "when_iso": _iso(today + timedelta(hours=15, minutes=30)),
            "done": False,
        },
        {
            "id": "local-dev-rem-3",
            "user_id": uid,
            "chat_id": uid,
            "task": "Проверить правки в миниаппе",
            "when_iso": _iso(now + timedelta(hours=2)),
            "done": False,
        },
        {
            "id": "local-dev-rem-4",
            "user_id": uid,
            "chat_id": uid,
            "task": "Купить молоко (выполнено)",
            "when_iso": _iso(today + timedelta(hours=9)),
            "done": True,
        },
    ]

    items = reminders_store.load_reminders()
    by_id = {
        str(it.get("id") or ""): it
        for it in items
        if isinstance(it, dict) and str(it.get("id") or "").strip()
    }
    for row in fixtures:
        by_id[row["id"]] = row
    # drop stale local-dev fixtures not in current set
    keep_ids = {r["id"] for r in fixtures}
    merged = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rid = str(it.get("id") or "")
        if rid.startswith("local-dev-rem-") and rid not in keep_ids:
            continue
        if rid in by_id and rid.startswith("local-dev-rem-"):
            continue
        merged.append(it)
    for row in fixtures:
        merged.append(row)

    reminders_store.save_reminders(merged)
    path = reminders_store.store_path()
    print(f"ok uid={uid} path={path} reminders={len(merged)}")
    print(json.dumps(fixtures, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
