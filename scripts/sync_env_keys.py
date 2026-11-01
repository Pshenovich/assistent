#!/usr/bin/env python3
"""Записать в /opt/assistant/.env ключи из /opt/assistant/.env_sync (KEY=VALUE по строке), затем удалить файл.

Деплой кладёт туда только ключи из списка ENV_SYNC_KEYS в deploy_prod_replace.sh, взятые из локального .env.
Перед правкой — копия .env.bak-envsync.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path("/opt/assistant")
SRC = ROOT / ".env_sync"
ENV = ROOT / ".env"


def main() -> None:
    if not SRC.is_file():
        print("env_sync:nothing")
        return
    pairs: dict[str, str] = {}
    for line in SRC.read_text().splitlines():
        key, sep, val = line.partition("=")
        if sep and key.strip() and val.strip():
            pairs[key.strip()] = val
    SRC.unlink()
    if not pairs or not ENV.is_file():
        print("env_sync:nothing")
        return
    shutil.copyfile(ENV, ENV.with_name(".env.bak-envsync"))
    out: list[str] = []
    seen: set[str] = set()
    changed: list[str] = []
    for line in ENV.read_text().splitlines():
        key = line.split("=", 1)[0].strip()
        if key in pairs and "=" in line:
            if key in seen:
                continue  # дубль ключа — оставляем одну строку
            seen.add(key)
            new = f"{key}={pairs[key]}"
            if new != line:
                changed.append(key)
            out.append(new)
        else:
            out.append(line)
    for key, val in pairs.items():
        if key not in seen:
            out.append(f"{key}={val}")
            changed.append(key)
    ENV.write_text("\n".join(out).rstrip() + "\n")
    print("env_sync:" + (",".join(changed) if changed else "unchanged"))


if __name__ == "__main__":
    main()
