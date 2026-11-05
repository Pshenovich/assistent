#!/usr/bin/env python3
"""Сдвиг нумерации записей во всех базах Leo на START (по умолчанию 10 000 000).

Зачем: старый сервер (Beget) лежит, Орбита запускается на новом с пустыми базами. Когда старые базы
вернутся, новые записи добавятся к ним как есть — номера не совпадут, ссылки между таблицами не сломаются.

  bump_id_sequences.py            — записать стартовые номера (сервисы должны быть остановлены)
  bump_id_sequences.py --check    — показать, где нумерация ниже START (ничего не меняет)
  bump_id_sequences.py --names a,b — имена таблиц списком (например, из более свежего кода, чем на сервере)
  bump_id_sequences.py --print-names — напечатать имена из кода через запятую

Как: имена всех таблиц с AUTOINCREMENT берутся из кода (CREATE TABLE … AUTOINCREMENT). В каждую базу
(в т.ч. ещё не созданную) пишется строка sqlite_sequence(name, START) для каждого имени. SQLite берёт
следующий номер из sqlite_sequence по имени таблицы — и для таблиц, которые появятся позже.
Таблицу, в которой уже есть записи с номером ниже START, скрипт не трогает и сообщает о ней.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START = 10_000_000
DB_FILES = [
    "notes.sqlite",
    "assistant/lib/usage.sqlite",
    *(f"data/{n}.sqlite" for n in (
        "analytics", "board", "calendar_tasks", "chat_digest", "chat_threads", "credits", "gcal_push",
        "knowledge_bases", "meeting_recordings", "note_versions", "note_ydoc", "push_subscriptions",
        "reminders", "semantic", "site_chat", "user_agents",
    )),
]


def autoincrement_tables() -> list[str]:
    names: set[str] = set()
    files = [*ROOT.joinpath("assistant").rglob("*.py"), ROOT / "usage_server.py", ROOT / "bot.py"]
    rx = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s*\((.*?)\)\s*[\"';]", re.S | re.I)
    for p in files:
        if p.is_file():
            for m in rx.finditer(p.read_text(errors="replace")):
                if re.search(r"AUTOINCREMENT", m.group(2), re.I):
                    names.add(m.group(1))
    return sorted(names)


def db_paths() -> list[Path]:
    paths = {ROOT / f for f in DB_FILES}
    paths |= set((ROOT / "data").glob("*.sqlite"))
    return sorted(paths)


def main() -> int:
    check = "--check" in sys.argv
    names = autoincrement_tables()
    if "--print-names" in sys.argv:
        print(",".join(names))
        return 0
    if "--names" in sys.argv:
        extra = sys.argv[sys.argv.index("--names") + 1]
        names = sorted(set(names) | {n.strip() for n in extra.split(",") if n.strip()})
    print(f"таблиц с AUTOINCREMENT в коде: {len(names)}")
    problems = 0
    for path in db_paths():
        if check and not path.is_file():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(path), timeout=30)
        c.execute("PRAGMA journal_mode=WAL")
        has_seq = c.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'").fetchone()
        if not has_seq and not check:
            # sqlite_sequence появляется вместе с первой AUTOINCREMENT-таблицей.
            c.execute("CREATE TABLE IF NOT EXISTS _seq_bootstrap (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        existing = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        seq = dict(c.execute("SELECT name, seq FROM sqlite_sequence").fetchall()) if (has_seq or not check) else {}
        low = []
        for name in names:
            max_id = c.execute(f'SELECT MAX(rowid) FROM "{name}"').fetchone()[0] if name in existing else None
            if max_id is not None and max_id < START:
                low.append(f"{name} (уже есть записи, max={max_id})")
                continue
            cur = seq.get(name)
            if cur is not None and cur >= START:
                continue
            if check:
                if name in existing:
                    low.append(f"{name} (seq={cur})")
                continue
            if cur is None:
                c.execute("INSERT INTO sqlite_sequence(name, seq) VALUES (?, ?)", (name, START))
            else:
                c.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (START, name))
        c.commit()
        c.close()
        rel = path.relative_to(ROOT)
        if low:
            problems += len(low)
            print(f"ВНИМАНИЕ {rel}: " + "; ".join(low))
        else:
            print(f"ok {rel}")
    print("ГОТОВО" if not problems else f"проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
