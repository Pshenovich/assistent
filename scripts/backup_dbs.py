#!/usr/bin/env python3
"""Резервные копии баз Leo (заметки, листы, история версий, чаты, задачи…).

Запуск:
  backup_dbs.py daily      — ежедневная копия, хранится 14 последних
  backup_dbs.py predeploy  — копия перед выкаткой, хранится 10 последних

Копирует через sqlite backup API (согласованная копия даже при работающем сервере).
Если заданы ключи S3_BACKUP_*, ежедневная копия сразу уходит и в S3 (scripts/backup_s3.py);
predeploy — нет: сервисы в это время стоят, а базы между выкатками и так копирует Litestream.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = Path("/root/leo-backups")
KEEP = {"daily": 14, "predeploy": 10}


def databases() -> list[Path]:
    out = [ROOT / "notes.sqlite", ROOT / "assistant" / "lib" / "usage.sqlite"]
    out += sorted((ROOT / "data").glob("*.sqlite"))
    return [p for p in out if p.is_file() and p.stat().st_size > 0]


def backup_one(src: Path, dst_dir: Path) -> Path:
    rel = src.relative_to(ROOT).as_posix().replace("/", "__")
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        # mode=rw, а не ro: базу в WAL без -shm (сервисы остановлены) ro-соединение
        # может не открыть. rw не создаёт файл, если его нет.
        s = sqlite3.connect(f"file:{src}?mode=rw", uri=True, timeout=30)
        d = sqlite3.connect(str(tmp_path))
        s.backup(d)
        s.close()
        check = d.execute("PRAGMA quick_check").fetchone()
        d.close()
        if not check or check[0] != "ok":
            raise RuntimeError(f"quick_check копии: {check!r}")
        out = dst_dir / f"{rel}.gz"
        with open(tmp_path, "rb") as fi, gzip.open(out, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        return out
    finally:
        tmp_path.unlink(missing_ok=True)


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "daily"
    if kind not in KEEP:
        print("usage: backup_dbs.py daily|predeploy")
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = DEST / kind / stamp
    dst.mkdir(parents=True, exist_ok=True)
    done = []
    for db in databases():
        try:
            done.append(backup_one(db, dst).name)
        except Exception as e:  # одна база не должна срывать остальные
            print(f"[backup] {db}: {e!r}")
    runs = sorted(p for p in (DEST / kind).iterdir() if p.is_dir())
    for old in runs[: max(0, len(runs) - KEEP[kind])]:
        shutil.rmtree(old, ignore_errors=True)
    print(f"[backup] {kind} {stamp}: {len(done)} db -> {dst}")
    if done and kind == "daily":
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import backup_s3

            backup_s3.upload(dst, kind)
        except Exception as e:  # локальная копия уже есть; ошибку S3 видно в /var/log/leo-backup.log
            print(f"[backup] S3: {e!r}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
