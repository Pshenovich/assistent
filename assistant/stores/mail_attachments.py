"""Вложения черновиков: временные файлы рядом с базой, 25 МБ на письмо, стираются после отправки или через 7 дней."""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

MAX_TOTAL = 25 * 1024 * 1024
TTL_SEC = 7 * 86400


class AttachmentError(ValueError):
    pass


def _root() -> Path:
    from assistant.stores import notes as notes_store

    p = Path(notes_store._db_path()).parent / "mail_attachments"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _dir(uid: Any) -> Path:
    p = _root() / str(int(uid))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_id(att_id: str) -> str:
    return att_id if re.fullmatch(r"[a-f0-9]{16}", str(att_id or "")) else ""


def save(uid: Any, name: str, content_type: str, data: bytes) -> dict[str, Any]:
    if len(data) > MAX_TOTAL:
        raise AttachmentError("Файл больше 25 МБ — почта такой не пропустит.")
    att_id = uuid.uuid4().hex[:16]
    d = _dir(uid)
    (d / f"{att_id}.bin").write_bytes(data)
    meta = {"id": att_id, "name": (name or "файл").replace("/", "_")[:200], "type": content_type or "application/octet-stream",
            "size": len(data), "ts": time.time()}
    (d / f"{att_id}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def _meta(uid: Any, att_id: str) -> dict[str, Any] | None:
    att_id = _safe_id(att_id)
    if not att_id:
        return None
    p = _dir(uid) / f"{att_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def describe(uid: Any, ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        m = _meta(uid, i)
        if m:
            out.append({"id": m["id"], "name": m["name"], "size": m["size"]})
    return out


def check_total(uid: Any, ids: list[str]) -> None:
    if sum(int(m["size"]) for m in describe(uid, ids)) > MAX_TOTAL:
        raise AttachmentError("Вложения вместе больше 25 МБ — почта такое письмо не пропустит.")


def load(uid: Any, ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in ids:
        m = _meta(uid, i)
        if not m:
            continue
        p = _dir(uid) / f"{m['id']}.bin"
        if p.is_file():
            out.append({"name": m["name"], "type": m["type"], "data": p.read_bytes()})
    return out


def remove(uid: Any, ids: list[str]) -> None:
    for i in ids:
        i = _safe_id(i)
        if not i:
            continue
        for ext in ("bin", "json"):
            try:
                (_dir(uid) / f"{i}.{ext}").unlink()
            except FileNotFoundError:
                pass


def purge_old(now: float | None = None) -> int:
    now = now or time.time()
    n = 0
    root = _root()
    for meta in root.glob("*/*.json"):
        try:
            ts = float(json.loads(meta.read_text(encoding="utf-8")).get("ts") or 0)
        except (OSError, ValueError):
            ts = 0
        if now - ts > TTL_SEC:
            for p in (meta, meta.with_suffix(".bin")):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            n += 1
    for d in root.iterdir():
        if d.is_dir() and not any(d.iterdir()):
            shutil.rmtree(d, ignore_errors=True)
    return n
