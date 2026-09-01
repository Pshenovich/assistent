#!/usr/bin/env python3
"""Восстановление контактов из legacy-путей (contacts_user/{uid}.json пуст → копия из старых каталогов)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.stores import contacts_store  # noqa: E402


def main() -> int:
    backups = contacts_store._backup_assistant_roots()
    if backups:
        print("Найдены бэкапы assistant:", ", ".join(str(p) for p in backups))
    restored = contacts_store.restore_all_contacts_from_legacy()
    if restored:
        print(f"Восстановлено контактов для uid: {', '.join(str(u) for u in restored)}")
    else:
        print("Нечего восстанавливать (legacy пуст или primary уже заполнен).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
