#!/usr/bin/env python3
"""Чипы задач в заметках для пунктов Фокуса, поставленных в задачу до исправления разметки.

Раньше чип вставал только в «похожую» строку заметки; у пересказанного инсайта такой не было,
и отметка молча пропадала. Скрипт проходит по пунктам «поставлена / связана задача» из заметок
и ставит недостающий чип (уже стоящие не трогает).

  python3 scripts/backfill_focus_task_chips.py --days 30          # только показать
  python3 scripts/backfill_focus_task_chips.py --days 30 --apply  # записать

--move: переставить и уже стоящие отметки, если строка для них теперь находится другая
(чип задачи встал в «Решения» вместо строки задачи, пометка — на заголовок «📋 Задачи»).
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import assistant.config  # noqa: E402,F401  — подхватывает .env
from assistant.lib import note_items  # noqa: E402
from assistant.services import focus_items as fi  # noqa: E402
from assistant.stores import notes as notes_store  # noqa: E402


def _unflatten(body: str, row: dict, tasks) -> str:
    """Редактор пересобирал строку задачи текстом: значок стал словом в конце строки
    («… — Сегодня неактуально»). Значка с ID пункта нет — снять это слово, значок поставит sync_source."""
    if f'data-leo-mark-item="{fi.mark_id(row["item_key"])}"' in body:
        return body
    m = re.search(r">([^<]+)</span>$", fi.mark_html(row) or "")
    label = html.unescape(m.group(1)).strip() if m else ""
    if not label:
        return body
    line = note_items.find_line(body, str((row.get("source") or {}).get("item") or ""))
    if not line and tasks:
        i = fi._meta_task_index(tasks, row)
        line = note_items.find_line(body, str(tasks[i].get("id") or "")) if i >= 0 else None
    if not line:
        return body
    at = int(line["at"])
    head = body[:at]
    tail = " " + html.escape(label, quote=False)
    if not head.rstrip().endswith(tail):
        return body
    return head.rstrip()[: -len(tail)] + body[at:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--move", action="store_true", help="переставить отметки, стоящие не на своей строке")
    args = ap.parse_args()
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).isoformat()
    rows = [
        fi._row(r)
        for r in fi._conn().execute(
            "SELECT * FROM focus_items WHERE status IN (%s) AND updated_at >= ?"
            % ("'converted', 'linked', 'completed', 'dismissed', 'retyped'" if args.move else "'converted', 'linked', 'completed'"),
            (since,),
        ).fetchall()
    ]
    todo = 0
    for row in rows:
        src = row.get("source") or {}
        if src.get("kind") != "note" or not src.get("id"):
            continue
        if not args.move and not (row.get("link") or {}).get("task_id"):
            continue
        if row.get("status") == "dismissed" and row.get("reason") == "not_me":
            continue  # «не мне» в общем саммари не помечаем
        owner = row.get("owner_user_id")
        note = notes_store.get_accessible_note(owner, int(src["id"]), with_sharing=False)
        if not note:
            continue
        owner_id = str(note.get("owner_user_id") or note.get("user_id") or owner)
        full = notes_store.get_note(owner_id, int(src["id"])) or {}
        body = str(full.get("body") or "")
        if args.move:
            meta = full.get("meta") if isinstance(full.get("meta"), dict) else {}
            tasks = meta.get("tasks") if isinstance(meta.get("tasks"), list) else None
            fixed = _unflatten(body, row, tasks)
            if fi.place_mark(fixed, row, tasks)[0] == body:
                continue
            if args.apply and fixed != body:
                notes_store.update_note(owner_id, int(src["id"]), body=fixed)
        elif f'data-leo-mark-item="{fi.mark_id(row["item_key"])}"' in body:
            continue
        if not fi._HTML_BODY_RE.match(body):
            print(f"note={src['id']}: тело в Markdown — пропуск")
            continue
        todo += 1
        print(f"note={src['id']} {row['status']} task={(row.get('link') or {}).get('task_id') or '-'} «{fi.public(row)['text'][:60]}»")
        if args.apply:
            fi.sync_source(owner, row)
    print(("Поставлено" if args.apply else "Будет поставлено") + f" отметок: {todo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
