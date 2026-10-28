"""Пульс процессов: бот раз в минуту пишет время, веб по нему отвечает в /healthz.

Файл data/<name>_heartbeat.json пишется атомарно (os.replace), чтобы читатель
никогда не видел половину записи.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _path(name: str) -> Path:
    raw = os.getenv("HEARTBEAT_DIR", "").strip()
    if raw:
        base = Path(raw).expanduser()
    else:
        from assistant.config import ROOT

        base = ROOT / "data"
    return base / f"{name}_heartbeat.json"


def beat(name: str) -> None:
    p = _path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps({"ts": time.time(), "pid": os.getpid()}), encoding="utf-8")
    os.replace(tmp, p)


def age_sec(name: str) -> float | None:
    """Сколько секунд назад был пульс; None — пульса ещё не было."""
    try:
        data = json.loads(_path(name).read_text(encoding="utf-8"))
        return max(0.0, time.time() - float(data["ts"]))
    except Exception:
        return None
