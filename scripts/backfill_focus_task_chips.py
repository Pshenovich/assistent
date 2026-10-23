#!/usr/bin/env python3
"""Чипы задач в заметках для пунктов Фокуса, поставленных в задачу до исправления разметки.

Раньше чип вставал только в «похожую» строку заметки; у пересказанного инсайта такой не было,
и отметка молча пропадала. Скрипт проходит по пунктам «поставлена / связана задача» из заметок
и ставит недостающий чип (уже стоящие не трогает).

  python3 scripts/backfill_focus_task_chips.py --days 30          # только показать
  python3 scripts/backfill_focus_task_chips.py --days 30 --apply  # записать
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import assistant.config  # noqa: E402,F401  — подхватывает .env
from assistant.services import focus_items as fi  # noqa: E402
from assistant.stores import notes as notes_store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    rows = [
        fi._row(r)
        for r in fi._conn().execute(
            "SELECT * FROM focus_items WHERE status IN ('converted', 'linked', 'completed') AND updated_at >= ?",
            (since,),
        ).fetchall()
    ]
    todo = 0
    for row in rows:
        src = row.get("source") or {}
        if src.get("kind") != "note" or not src.get("id") or not (row.get("link") or {}).get("task_id"):
            continue
        owner = row.get("owner_user_id")
        note = notes_store.get_accessible_note(owner, int(src["id"]), with_sharing=False)
        if not note:
            continue
        owner_id = str(note.get("owner_user_id") or note.get("user_id") or owner)
        body = str((notes_store.get_note(owner_id, int(src["id"])) or {}).get("body") or "")
        if f'data-leo-mark-item="{fi.mark_id(row["item_key"])}"' in body:
            continue
        todo += 1
        print(f"note={src['id']} task={row['link']['task_id']} «{fi.public(row)['text'][:60]}»")
        if args.apply:
            fi.sync_source(owner, row)
    print(("Поставлено" if args.apply else "Будет поставлено") + f" чипов: {todo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
