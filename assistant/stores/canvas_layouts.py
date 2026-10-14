"""«Мои макеты»: сохранённые пользователем раскладки слайдов «Свой макет» (custom.grid).

Храним только структуру (строки, типы блоков, их доли ширины, тон карточек, фон) —
содержимое каждый раз заполняет ИИ по заметке. Макеты общие для всех презентаций
пользователя.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from assistant.stores import notes as notes_store

_LOCK = notes_store._LOCK  # type: ignore[attr-defined]
MAX_PER_USER = 30
MAX_NAME = 60


def _conn():
    conn = notes_store._conn()  # type: ignore[attr-defined]
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS canvas_layouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            structure TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_canvas_layouts_user ON canvas_layouts(user_id)")
    return conn


def structure_of(slots: dict[str, Any]) -> dict[str, Any]:
    """Раскладка слайда без текста: фон, наличие заголовка, строки с блоками (тип, доля, тон)."""
    from assistant.lib import canvas_blocks

    rows = []
    for r in (slots.get("rows") or [])[: canvas_blocks.MAX_ROWS]:
        if not isinstance(r, dict):
            continue
        blocks = []
        for b in (r.get("blocks") or [])[: canvas_blocks.MAX_BLOCKS]:
            if not isinstance(b, dict):
                continue
            t = canvas_blocks._block_type(b)
            item: dict[str, Any] = {"type": t, "span": canvas_blocks._span(b)}
            if t == "card" and b.get("tone") in ("accent", "dark"):
                item["tone"] = b["tone"]
            if t == "list":
                item["items"] = max(1, min(6, len(b.get("items") or [])))
            blocks.append(item)
        if blocks:
            rows.append({"blocks": blocks})
    return {
        "surface": str(slots.get("surface") or "light"),
        "title": bool(str(slots.get("title") or "").strip()),
        "eyebrow": bool(str(slots.get("eyebrow") or "").strip()),
        "rows": rows,
    }


_LABELS = {
    "heading": ("Заголовок", "заголовка"), "text": ("Текст", "текста"), "list": ("Список", "списка"),
    "number": ("Цифра", "цифры"), "card": ("Карточка", "карточки"), "quote": ("Цитата", "цитаты"),
    "pill": ("Метка", "метки"), "image": ("Фото", "фото"), "chart": ("График", "графика"),
}


def auto_name(structure: dict[str, Any]) -> str:
    """«Цифра + 3 карточки / Текст» — по строкам раскладки, чтобы макеты различались в списке."""
    parts = []
    for row in structure.get("rows") or []:
        groups: list[list[Any]] = []
        for b in row.get("blocks") or []:
            t = b.get("type", "text")
            if groups and groups[-1][0] == t:
                groups[-1][1] += 1
            else:
                groups.append([t, 1])
        bits = []
        for t, n in groups:
            one, many = _LABELS.get(t, ("Блок", "блока"))
            bits.append(one if n == 1 else f"{n} {many}")
        parts.append(" + ".join(bits))
    name = " / ".join(parts)
    name = name[:1].upper() + name[1:] if name else "Мой макет"
    return name if len(name) <= MAX_NAME else name[: MAX_NAME - 1] + "…"


def _row(r: Any) -> dict[str, Any]:
    try:
        structure = json.loads(r["structure"])
    except (TypeError, ValueError):
        structure = {"rows": []}
    return {"id": int(r["id"]), "name": str(r["name"]), "structure": structure, "created_at": r["created_at"]}


def list_layouts(user_id: int | str) -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute(
            "SELECT * FROM canvas_layouts WHERE user_id = ? ORDER BY id DESC", (str(int(user_id)),)
        ).fetchall()
    return [_row(r) for r in rows]


def get_layout(user_id: int | str, layout_id: int) -> Optional[dict[str, Any]]:
    with _LOCK:
        r = _conn().execute(
            "SELECT * FROM canvas_layouts WHERE user_id = ? AND id = ?", (str(int(user_id)), int(layout_id))
        ).fetchone()
    return _row(r) if r else None


def create_layout(user_id: int | str, name: str, slots: dict[str, Any]) -> dict[str, Any]:
    structure = structure_of(slots)
    if not structure["rows"]:
        raise ValueError("На слайде нет блоков — сохранять нечего")
    uid = str(int(user_id))
    name = " ".join(str(name or "").split())[:MAX_NAME] or auto_name(structure)
    with _LOCK:
        conn = _conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM canvas_layouts WHERE user_id = ?", (uid,)).fetchone()["n"]
        if int(n) >= MAX_PER_USER:
            raise ValueError(f"Можно сохранить не больше {MAX_PER_USER} макетов — удалите ненужные")
        cur = conn.execute(
            "INSERT INTO canvas_layouts (user_id, name, structure, created_at) VALUES (?, ?, ?, ?)",
            (uid, name, json.dumps(structure, ensure_ascii=False), datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
        )
        conn.commit()
        new_id = int(cur.lastrowid)
    return get_layout(uid, new_id) or {"id": new_id, "name": name, "structure": structure}


def delete_layout(user_id: int | str, layout_id: int) -> bool:
    with _LOCK:
        cur = _conn().execute(
            "DELETE FROM canvas_layouts WHERE user_id = ? AND id = ?", (str(int(user_id)), int(layout_id))
        )
        _conn().commit()
        return cur.rowcount > 0


def fit_to_structure(slots: dict[str, Any], structure: dict[str, Any]) -> dict[str, Any]:
    """Слоты от модели → ровно сохранённая раскладка (типы, доли, тон, фон); текст берём от модели."""
    model_rows = [r for r in (slots.get("rows") or []) if isinstance(r, dict)]
    out_rows = []
    for ri, srow in enumerate(structure.get("rows") or []):
        mblocks = [b for b in ((model_rows[ri] if ri < len(model_rows) else {}).get("blocks") or []) if isinstance(b, dict)]
        blocks = []
        for bi, sb in enumerate(srow.get("blocks") or []):
            mb = dict(mblocks[bi]) if bi < len(mblocks) else {}
            mb["type"] = sb.get("type", "text")
            mb["span"] = sb.get("span", 6)
            if sb.get("tone"):
                mb["tone"] = sb["tone"]
            blocks.append(mb)
        out_rows.append({"blocks": blocks})
    out = dict(slots)
    for k in ("title", "eyebrow"):  # модель иногда копирует флаги раскладки (true) вместо текста
        if not isinstance(out.get(k), str) or out[k].strip().lower() in ("true", "false"):
            out.pop(k, None)
    out["rows"] = out_rows
    out["surface"] = structure.get("surface") or "light"
    return out
