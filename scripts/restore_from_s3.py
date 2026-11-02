#!/usr/bin/env python3
"""Восстановление Leo из S3 в пустой каталог (сервер погиб / репетиция).

  restore_from_s3.py <каталог> [--prefix ИМЯ_СЕРВЕРА] [--stamp STAMP] [--no-litestream]

1. Последняя ночная копия (или --stamp) из <prefix>/daily/: базы *.gz и state.tar.gz
   (токены OAuth, .env, data/users …) → в <каталог>.
2. Поверх баз — самые свежие версии из Litestream (litestream/<prefix>/…), если litestream установлен:
   потеря данных — секунды, а не с прошлой ночи.
3. quick_check каждой базы и число строк по таблицам (сверить со старым сервером).

Ключи — S3_BACKUP_* из /opt/assistant/.env или окружения. --prefix по умолчанию — имя этого сервера,
поэтому на НОВОЙ машине его нужно указать (имя погибшего сервера: см. backup_s3.py list).
Ничего не пишет в /opt/assistant: восстановили в каталог → проверили → остановили сервисы → скопировали.
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_s3 import S3, _env, fetch  # noqa: E402


def litestream_dbs(s3: S3, host: str) -> list[str]:
    # Litestream 0.5: <путь реплики>/<уровень 0000…0009>/<txid>-<txid>.ltx
    keys, _ = s3.list(f"litestream/{host}/")
    rels = set()
    for k in keys:
        m = re.match(rf"litestream/{re.escape(host)}/(.+)/[0-9a-f]{{4}}/[0-9a-f-]+\.ltx$", k)
        if m:
            rels.add(m.group(1))
    return sorted(rels)


def litestream_restore(s3: S3, host: str, rel: str, out: Path) -> bool:
    q = urllib.parse.urlencode({"endpoint": s3.endpoint, "region": s3.region, "force-path-style": "true"})
    url = f"s3://{s3.bucket}/litestream/{host}/{rel}?{q}"
    env = {**os.environ, "LITESTREAM_ACCESS_KEY_ID": s3.key, "LITESTREAM_SECRET_ACCESS_KEY": s3.secret}
    tmp = out.with_name(out.name + ".ls-restore")
    tmp.unlink(missing_ok=True)
    r = subprocess.run(["litestream", "restore", "-o", str(tmp), url], env=env, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.is_file():
        print(f"  litestream {rel}: ошибка {r.returncode} {r.stderr.strip()[-300:]}")
        tmp.unlink(missing_ok=True)
        return False
    for side in ("-wal", "-shm"):
        Path(str(out) + side).unlink(missing_ok=True)
    tmp.replace(out)
    return True


def report(root: Path) -> int:
    bad = 0
    dbs = [root / "notes.sqlite", root / "assistant/lib/usage.sqlite", *sorted((root / "data").glob("*.sqlite"))]
    for db in dbs:
        if not db.is_file():
            continue
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ok = c.execute("PRAGMA quick_check").fetchone()[0]
        bad += ok != "ok"
        tables = [r[0] for r in c.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
        counts = []
        for t in tables:
            n = c.execute('select count(*) from "%s"' % t.replace('"', '""')).fetchone()[0]
            counts.append(f"{t}={n}")
        c.close()
        print(f"{db.relative_to(root)} [{ok}] " + " ".join(counts))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--prefix")
    ap.add_argument("--stamp", default="latest")
    ap.add_argument("--no-litestream", action="store_true")
    a = ap.parse_args()
    env = _env()
    if a.prefix:
        env["S3_BACKUP_PREFIX"] = a.prefix
        os.environ["S3_BACKUP_PREFIX"] = a.prefix
    s3 = S3(env)
    if not s3.configured:
        raise SystemExit("нет ключей S3_BACKUP_ACCESS_KEY/S3_BACKUP_SECRET_KEY")
    target = Path(a.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    print(f"==> Ночная копия {s3.prefix}/daily/{a.stamp}")
    with tempfile.TemporaryDirectory() as tmp:
        run = fetch(a.stamp, Path(tmp), "daily")
        state = run / "state.tar.gz"
        if state.is_file():
            with tarfile.open(state) as tar:
                try:
                    tar.extractall(target, filter="data")
                except TypeError:  # Python без фильтров tarfile
                    tar.extractall(target)
            print("  state.tar.gz распакован")
        else:
            print("  WARN: в копии нет state.tar.gz (копия неполная?)")
        for gz in sorted(run.glob("*.sqlite.gz")):
            rel = gz.name[: -len(".gz")].replace("__", "/")
            out = target / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(gz, "rb") as fi, open(out, "wb") as fo:
                shutil.copyfileobj(fi, fo)
        print(f"  баз из ночной копии: {len(list(run.glob('*.sqlite.gz')))}")

    if not a.no_litestream:
        if not shutil.which("litestream"):
            print("==> Litestream не установлен — базы на момент ночной копии")
        else:
            rels = litestream_dbs(s3, s3.prefix)
            print(f"==> Litestream: {len(rels)} баз в litestream/{s3.prefix}/")
            for rel in rels:
                out = target / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                print(f"  {rel}: {'ok' if litestream_restore(s3, s3.prefix, rel, out) else 'оставлена ночная'}")

    print("==> Проверка баз")
    bad = report(target)
    print("ГОТОВО" if not bad else f"ВНИМАНИЕ: {bad} баз не прошли quick_check")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
