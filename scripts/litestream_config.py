#!/usr/bin/env python3
"""Настройка Litestream — непрерывная копия всех баз SQLite Leo в S3 (секунды потерь, а не сутки).

  litestream_config.py          — записать /etc/litestream.yml и /etc/litestream.env; печатает changed/unchanged/skip
  litestream_config.py --print  — показать конфиг, ничего не писать

Список баз тот же, что у backup_dbs.py. Новая база попадёт в репликацию со следующей выкаткой
(деплой запускает этот скрипт и перезапускает litestream, если конфиг изменился).
Ключи S3_BACKUP_* берутся из /opt/assistant/.env и кладутся в /etc/litestream.env (600) — не в yml.

Восстановление одной базы (на чистый путь; -force, если файл уже есть):
  litestream restore -o /tmp/r/notes.sqlite /opt/assistant/notes.sqlite
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backup_dbs import ROOT, databases  # noqa: E402
from backup_s3 import S3, _env  # noqa: E402

YML = Path("/etc/litestream.yml")
ENVF = Path("/etc/litestream.env")


def render(s3: S3) -> str:
    host = s3.prefix or socket.gethostname()
    lines = [
        "# Генерирует /opt/assistant/scripts/litestream_config.py при выкатке — руками не править.",
        f"endpoint: {s3.endpoint}",
        f"region: {s3.region}",
        f"bucket: {s3.bucket}",
        "force-path-style: true",
        "snapshot:",
        "  interval: 6h",
        "  retention: 72h",
        "dbs:",
    ]
    for db in databases():
        rel = db.relative_to(ROOT).as_posix()
        lines += [
            f"  - path: {db}",
            "    replica:",
            "      type: s3",
            f"      path: litestream/{host}/{rel}",
        ]
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> bool:
    if path.is_file() and path.read_text() == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.chmod(0o600)
    tmp.replace(path)
    return True


def main() -> int:
    s3 = S3(_env())
    if not s3.configured:
        print("skip: нет S3_BACKUP_ACCESS_KEY/S3_BACKUP_SECRET_KEY")
        return 0
    yml = render(s3)
    if "--print" in sys.argv:
        print(yml, end="")
        return 0
    env = f"LITESTREAM_ACCESS_KEY_ID={s3.key}\nLITESTREAM_SECRET_ACCESS_KEY={s3.secret}\n"
    changed = _write(ENVF, env)
    changed = _write(YML, yml) or changed
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
