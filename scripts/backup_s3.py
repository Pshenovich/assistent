#!/usr/bin/env python3
"""Копия бэкапов Leo в S3 вне сервера — в два независимых хранилища: Selectel и Timeweb.

  backup_s3.py upload <каталог бэкапа баз> daily|predeploy  — базы (*.gz) + архив файлов состояния
                                                           (state.tar.gz в каталоге — берётся готовый)
  backup_s3.py list [daily|predeploy] [--target selectel|timeweb]  — какие копии лежат в бакетах
  backup_s3.py fetch <stamp|latest> <куда> [daily|predeploy] [--target selectel|timeweb]
                                                           — скачать копию (для восстановления)

Обычно вызывается из backup_dbs.py сам, после локальной копии; копия уходит во все хранилища, для
которых заданы ключи (сбой одного не мешает другому). Ключи — из /opt/assistant/.env:
  selectel (основное, сюда же пишет Litestream): S3_BACKUP_ACCESS_KEY, S3_BACKUP_SECRET_KEY;
    необязательные S3_BACKUP_BUCKET, S3_BACKUP_ENDPOINT, S3_BACKUP_REGION;
  timeweb (второе, другой провайдер): TIMEWEB_S3_ACCESS_KEY, TIMEWEB_S3_SECRET_ACCESS_KEY,
    TIMEWEB_SE_URL (адрес S3); необязательные TIMEWEB_S3_BUCKET (orbita-s3), TIMEWEB_S3_REGION (ru-1).
S3_BACKUP_PREFIX — общий для обоих (по умолчанию имя сервера, чтобы репетиция не мешалась с продом).
Только стандартная библиотека + curl (--aws-sigv4); ключи идут в curl через stdin, не в argv.

В бакете: <prefix>/<kind>/<stamp>/<база>.gz и state.tar.gz — токены OAuth, .env, data/users и прочие
файлы, которых нет в базах (список — scripts/deploy_rsync_excludes.txt).
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEEP = {"daily": 30, "predeploy": 10}
# В архив состояния не кладём: код из git, окружение, кэши, сами базы (они рядом отдельными .gz).
STATE_SKIP_DIRS = {
    ".venv", ".git", ".claude", "__pycache__", "node_modules", ".npm-cache",
    "telegram-bot-api", "news", "tmp-restore",
}
STATE_SKIP_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite-journal", ".log", ".pyc")
NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _env() -> dict[str, str]:
    out: dict[str, str] = {}
    p = ROOT / ".env"
    if p.is_file():
        for line in p.read_text(errors="replace").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip().startswith(ENV_PREFIXES):
                out[key.strip()] = val.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k.startswith(ENV_PREFIXES) and v:
            out[k] = v
    return out


ENV_PREFIXES = ("S3_BACKUP_", "TIMEWEB_")
TARGETS = ("selectel", "timeweb")


class S3:
    def __init__(self, env: dict[str, str], target: str = "selectel"):
        self.name = target
        if target == "timeweb":
            self.key = env.get("TIMEWEB_S3_ACCESS_KEY", "")
            self.secret = env.get("TIMEWEB_S3_SECRET_ACCESS_KEY", "")
            self.bucket = env.get("TIMEWEB_S3_BUCKET") or "orbita-s3"
            self.endpoint = (env.get("TIMEWEB_SE_URL") or env.get("TIMEWEB_S3_ENDPOINT")
                             or "https://s3.twcstorage.ru").rstrip("/")
            self.region = env.get("TIMEWEB_S3_REGION") or "ru-1"
        elif target == "selectel":
            self.key = env.get("S3_BACKUP_ACCESS_KEY", "")
            self.secret = env.get("S3_BACKUP_SECRET_KEY", "")
            self.bucket = env.get("S3_BACKUP_BUCKET") or "orbita-backups"
            self.endpoint = (env.get("S3_BACKUP_ENDPOINT") or "https://s3.ru-7.storage.selcloud.ru").rstrip("/")
            self.region = env.get("S3_BACKUP_REGION") or "ru-7"
        else:
            raise SystemExit(f"неизвестное хранилище {target!r}: {', '.join(TARGETS)}")
        self.prefix = (env.get("S3_BACKUP_PREFIX") or socket.gethostname()).strip("/")

    @property
    def configured(self) -> bool:
        return bool(self.key and self.secret)

    def _curl(self, args: list[str], url: str) -> bytes:
        cfg = f'user = "{self.key}:{self.secret}"\n'
        r = subprocess.run(
            ["curl", "-sS", "--fail-with-body", "--retry", "3", "--max-time", "600", "-K", "-",
             "--aws-sigv4", f"aws:amz:{self.region}:s3", *args, url],
            input=cfg.encode(), capture_output=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"S3 {url.split('?')[0]}: curl {r.returncode} {r.stderr.decode()[:200]} {r.stdout.decode()[:300]}")
        return r.stdout

    def url(self, key: str, query: str = "") -> str:
        u = f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(key)}"
        return f"{u}?{query}" if query else u

    def put(self, path: Path, key: str) -> None:
        self._curl(["-T", str(path), "-o", "/dev/null"], self.url(key))

    def get(self, key: str, dest: Path) -> None:
        self._curl(["-o", str(dest)], self.url(key))

    def delete(self, key: str) -> None:
        self._curl(["-X", "DELETE", "-o", "/dev/null"], self.url(key))

    def list(self, prefix: str, delimiter: str = "") -> tuple[list[str], list[str]]:
        """(ключи, общие префиксы) под prefix, все страницы."""
        keys: list[str] = []
        prefixes: list[str] = []
        token = ""
        while True:
            q = {"list-type": "2", "prefix": prefix}
            if delimiter:
                q["delimiter"] = delimiter
            if token:
                q["continuation-token"] = token
            root = ET.fromstring(self._curl([], f"{self.endpoint}/{self.bucket}?{urllib.parse.urlencode(sorted(q.items()))}"))
            keys += [e.text or "" for e in root.iter(f"{NS}Key")]
            prefixes += [e.text or "" for e in root.iter(f"{NS}Prefix") if e.text and e.text != prefix]
            token = root.findtext(f"{NS}NextContinuationToken") or ""
            if root.findtext(f"{NS}IsTruncated") != "true" or not token:
                return keys, prefixes


def state_archive(dest: Path) -> int:
    """Архив файлов состояния /opt/assistant (без кода окружения, кэшей и баз). Возвращает число файлов."""
    n = 0
    with tarfile.open(dest, "w:gz", compresslevel=6) as tar:
        for dirpath, dirnames, filenames in os.walk(ROOT):
            dirnames[:] = [d for d in dirnames if d not in STATE_SKIP_DIRS and not d.startswith("assistant.stage")]
            for name in filenames:
                if name.endswith(STATE_SKIP_SUFFIXES):
                    continue
                full = Path(dirpath) / name
                if full.is_symlink() or not full.is_file():
                    continue
                try:
                    tar.add(full, arcname=full.relative_to(ROOT).as_posix(), recursive=False)
                    n += 1
                except OSError as e:  # файл исчез во время обхода — не повод срывать копию
                    print(f"[s3] skip {full}: {e!r}")
    return n


def targets(env: dict[str, str] | None = None) -> list[S3]:
    """Хранилища, для которых заданы ключи."""
    env = _env() if env is None else env
    return [s for s in (S3(env, t) for t in TARGETS) if s.configured]


def upload(src_dir: Path, kind: str) -> bool:
    """Копия во все настроенные хранилища. True — если дошла во все (и хотя бы одно есть)."""
    dests = targets()
    if not dests:
        print("[s3] ключи S3_BACKUP_* / TIMEWEB_S3_* не заданы — пропуск")
        return False
    files = sorted(f for f in src_dir.glob("*.gz") if f.name != "state.tar.gz")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        state = src_dir / "state.tar.gz"
        if state.is_file():  # архив уже собран (rescue_old_server.sh) — везём как есть
            n_state = -1
        else:
            state = Path(tmp) / "state.tar.gz"
            n_state = state_archive(state)
        size = sum(f.stat().st_size for f in files) + state.stat().st_size
        for s3 in dests:
            base = f"{s3.prefix}/{kind}/{src_dir.name}"
            try:
                for f in files:
                    s3.put(f, f"{base}/{f.name}")
                # state последним: его наличие = копия целиком дошла.
                s3.put(state, f"{base}/state.tar.gz")
                print(f"[s3:{s3.name}] {s3.bucket}/{base}: {len(files)} баз + state ({n_state} файлов), {size / 1e6:.1f} МБ")
                prune(s3, kind)
            except Exception as e:  # одно хранилище не должно срывать копию в другое
                ok = False
                print(f"[s3:{s3.name}] ОШИБКА: {e!r}")
    return ok


def runs(s3: S3, kind: str) -> list[str]:
    _, prefixes = s3.list(f"{s3.prefix}/{kind}/", delimiter="/")
    return sorted(p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes)


def prune(s3: S3, kind: str) -> None:
    stamps = runs(s3, kind)
    for stamp in stamps[: max(0, len(stamps) - KEEP[kind])]:
        keys, _ = s3.list(f"{s3.prefix}/{kind}/{stamp}/")
        for key in keys:
            s3.delete(key)
        print(f"[s3:{s3.name}] удалена старая копия {kind}/{stamp}")


def fetch(stamp: str, dest: Path, kind: str, s3: S3 | None = None) -> Path:
    s3 = s3 or S3(_env())
    if stamp == "latest":
        stamps = runs(s3, kind)
        if not stamps:
            raise SystemExit(f"в бакете нет копий {s3.prefix}/{kind}/")
        stamp = stamps[-1]
    keys, _ = s3.list(f"{s3.prefix}/{kind}/{stamp}/")
    if not keys:
        raise SystemExit(f"нет копии {s3.prefix}/{kind}/{stamp}")
    out = dest / stamp
    out.mkdir(parents=True, exist_ok=True)
    for key in keys:
        s3.get(key, out / key.rsplit("/", 1)[-1])
    print(f"[s3:{s3.name}] {s3.prefix}/{kind}/{stamp}: {len(keys)} файлов -> {out}")
    return out


def main() -> int:
    args = sys.argv[1:]
    target = ""
    if "--target" in args:
        i = args.index("--target")
        target = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    if len(args) >= 2 and args[0] == "upload":
        kind = args[2] if len(args) > 2 else "daily"
        return 0 if upload(Path(args[1]), kind) else 1
    if args and args[0] == "list":
        env = _env()
        for s3 in [S3(env, target)] if target else targets(env):
            for kind in [args[1]] if len(args) > 1 else list(KEEP):
                print(f"{s3.name} {s3.bucket}/{s3.prefix} {kind}:", *runs(s3, kind))
        return 0
    if len(args) >= 3 and args[0] == "fetch":
        fetch(args[1], Path(args[2]), args[3] if len(args) > 3 else "daily", S3(_env(), target or "selectel"))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
