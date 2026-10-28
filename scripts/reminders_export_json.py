#!/usr/bin/env python3
"""Выгрузка напоминаний из базы обратно в JSON — для отката на код до переезда в SQLite.

Запуск (сервисы остановлены):
  .venv/bin/python scripts/reminders_export_json.py [путь.json]

По умолчанию пишет в REMINDERS_STORE_PATH (./reminders.json); прежний файл
сохраняется рядом как .before-export.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from assistant.stores import reminders_store  # noqa: E402


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else reminders_store.store_path()
    items = reminders_store.load_reminders()
    if out.exists():
        shutil.copyfile(out, out.with_name(out.name + ".before-export"))
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    print(f"[reminders_export] {len(items)} -> {out} (из {reminders_store.db_path()})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
