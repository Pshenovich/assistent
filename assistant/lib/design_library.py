"""Библиотека компонентов макета: мастера (фреймы с comp) и их содержимое — для вставки в другие макеты.

Версия компонента — хэш его содержимого без положения на холсте: перенос мастера по библиотеке
не считается обновлением, правка текста, цвета, размера или состава — считается.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from assistant.lib import board_doc

MAX_COMPONENTS = 300
MAX_BYTES = 6 * 1024 * 1024  # тело библиотеки в ответе (картинки внутри компонентов — data URL)


def _subtree(elements: dict[str, dict[str, Any]], root_id: str) -> list[dict[str, Any]]:
    kids: dict[str, list[str]] = {}
    for eid, el in elements.items():
        f = el.get("frame")
        if f:
            kids.setdefault(str(f), []).append(eid)
    out: list[str] = []
    stack = [root_id]
    seen = {root_id}
    while stack:
        cur = stack.pop()
        out.append(cur)
        for k in kids.get(cur, []):
            if k not in seen:
                seen.add(k)
                stack.append(k)
    return sorted((elements[i] for i in out), key=lambda e: (float(e.get("z") or 0), str(e.get("id"))))


def _version(master: dict[str, Any], parts: list[dict[str, Any]]) -> str:
    mx = float(master.get("x") or 0)
    my = float(master.get("y") or 0)
    norm = []
    for rank, el in enumerate(parts):
        e = dict(el)
        e["x"] = round(float(e.get("x") or 0) - mx, 1)
        e["y"] = round(float(e.get("y") or 0) - my, 1)
        e["z"] = rank
        if e.get("id") == master.get("id"):
            e.pop("frame", None)
        if isinstance(e.get("points"), list):
            e["points"] = [[round(float(p[0]) - mx, 1), round(float(p[1]) - my, 1)] for p in e["points"] if len(p) >= 2]
        e.pop("d", None)
        norm.append(e)
    raw = json.dumps(norm, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def components(body: str) -> list[dict[str, Any]]:
    """Мастера макета: [{id, name, label, ver, w, h, elements}] (elements — мастер и всё внутри, по z)."""
    doc = board_doc.parse_board(body)
    elements: dict[str, dict[str, Any]] = doc.get("elements") or {}
    masters = [e for e in elements.values() if e.get("type") == "frame" and e.get("comp")]
    # Набор вариантов: фрейм, в котором только мастера (их больше одного) — «Набор · Вариант».
    per: dict[str, int] = {}
    mixed: set[str] = set()
    for e in elements.values():
        f = e.get("frame")
        if not f:
            continue
        if e.get("type") == "frame" and e.get("comp"):
            per[str(f)] = per.get(str(f), 0) + 1
        else:
            mixed.add(str(f))
    out: list[dict[str, Any]] = []
    size = 0
    for m in sorted(masters, key=lambda e: (str(e.get("name") or ""), str(e.get("id")))):
        if len(out) >= MAX_COMPONENTS:
            break
        parts = _subtree(elements, str(m["id"]))
        f = str(m.get("frame") or "")
        setf = elements.get(f) if f and per.get(f, 0) > 1 and f not in mixed else None
        name = str(m.get("name") or "Компонент")
        item = {
            "id": str(m["id"]),
            "name": name,
            "set": str(setf.get("name") or "") if setf else "",
            "set_id": str(setf.get("id") or "") if setf else "",
            "label": (str(setf.get("name") or "") + " · " + name) if setf and setf.get("name") else name,
            "ver": _version(m, parts),
            "w": m.get("w"),
            "h": m.get("h"),
            "elements": parts,
        }
        size += len(json.dumps(parts, ensure_ascii=False))
        if size > MAX_BYTES:
            break
        out.append(item)
    out.sort(key=lambda c: c["label"].lower())
    return out
