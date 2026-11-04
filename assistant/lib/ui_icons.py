"""Иконки интерфейса для макета (Лео рисует экраны): VK Icons 24px — тот же набор, что в макетах Obuchat
из Figma (webapp/canvas-kit/icons/icons.json, MIT). Иконка → векторный элемент доски (vb 24×24, контуры M/L/C/Q/Z).
"""

from __future__ import annotations

import functools
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Optional

from assistant.lib import svg_path

ICONS_FILE = Path(__file__).resolve().parents[2] / "webapp" / "canvas-kit" / "icons" / "icons.json"

# Короткие имена для модели (полное имя — <имя>_outline_24); остальные иконки набора тоже доступны по имени.
UI_NAMES = (
    "add", "add_circle", "add_square", "cancel", "cancel_circle", "check", "check_circle", "chevron_left", "chevron_right",
    "chevron_down_small", "chevron_up_small", "search", "gear", "home", "user", "users", "user_circle", "write",
    "delete", "menu", "filter", "sort", "notification", "calendar", "clock",
    "message", "messages", "mail", "phone", "camera", "picture", "clips_attach", "send", "microphone", "play", "pause",
    "like", "favorite", "bookmark", "share", "download", "upload", "link", "lock", "key", "view", "hide", "info",
    "warning_triangle", "help", "wallet", "money_circle", "payment_card", "bank", "shopping_cart", "gift", "place", "globe",
    "document", "folder", "list", "squareshape_4_grid", "sun", "door_arrow_right", "refresh",
    "stars", "square_4", "education", "poll", "graph", "statistics", "work", "home_heart", "fire", "copy",
)


@functools.lru_cache(maxsize=1)
def _raw() -> dict[str, str]:
    try:
        return json.loads(ICONS_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def resolve(name: Any) -> Optional[str]:
    """Имя иконки (короткое или полное) → ключ набора; похожее — по началу имени."""
    raw = _raw()
    n = re.sub(r"[^a-z0-9_]", "", str(name or "").strip().lower().replace("-", "_").replace(" ", "_"))
    if not n:
        return None
    for k in (n, f"{n}_outline_24", f"{n}_24", f"{n}_outline"):
        if k in raw:
            return k
    for k in raw:
        if k.startswith(n + "_"):
            return k
    return None


def available() -> list[str]:
    """Имена из UI_NAMES, которые реально есть в наборе (для подсказки модели)."""
    return [n for n in UI_NAMES if resolve(n)]


_INHERIT = ("fill", "stroke", "stroke-width", "fill-rule", "clip-rule", "stroke-linecap", "stroke-linejoin")


def _paint(v: Optional[str], color: str) -> Optional[str]:
    v = (v or "").strip().lower()
    if not v or v == "none":
        return None
    if v == "currentcolor":
        return color
    return v if re.match(r"^#[0-9a-f]{6}$", v) else color


def vector(name: Any, color: str = "#111827") -> Optional[dict[str, Any]]:
    """Иконка → {"vb": [24, 24], "paths": [...]} для элемента type=vector; None — нет такой."""
    key = resolve(name)
    if not key:
        return None
    try:
        root = ET.fromstring('<svg xmlns="http://www.w3.org/2000/svg">' + _raw()[key] + "</svg>")
    except ET.ParseError:
        return None
    paths: list[dict[str, Any]] = []

    def walk(node: ET.Element, inh: dict[str, str]) -> None:
        tag = node.tag.split("}")[-1]
        if tag in ("defs", "clipPath", "mask"):
            return
        attrs = dict(inh)
        for k in _INHERIT:
            if node.get(k) is not None:
                attrs[k] = node.get(k) or ""
        d = ""
        if tag == "path":
            d = node.get("d") or ""
        elif tag == "rect":
            x, y = float(node.get("x") or 0), float(node.get("y") or 0)
            w, h = float(node.get("width") or 0), float(node.get("height") or 0)
            if w >= 24 and h >= 24:
                d = ""  # рамка 24×24 из старых иконок — не рисуем
            else:
                d = f"M{x} {y}H{x + w}V{y + h}H{x}Z"
        elif tag == "polygon":
            pts = re.findall(r"-?[\d.]+", node.get("points") or "")
            pairs = list(zip(pts[0::2], pts[1::2]))
            if pairs and not (len(pairs) == 4 and {p for p in pairs} >= {("0", "0"), ("24", "24")}):
                d = "M" + "L".join(f"{a} {b}" for a, b in pairs) + "Z"
        elif tag == "circle":
            cx, cy, r = float(node.get("cx") or 0), float(node.get("cy") or 0), float(node.get("r") or 0)
            d = f"M{cx - r} {cy}A{r} {r} 0 1 0 {cx + r} {cy}A{r} {r} 0 1 0 {cx - r} {cy}Z"
        if d:
            stroke = _paint(attrs.get("stroke"), color)
            # Заливка по умолчанию (чёрная в SVG) — только у контуров без обводки: обводочные иконки не заливаем.
            fill = _paint(attrs.get("fill", "none" if stroke else "#000000"), color)
            if fill or stroke:
                p: dict[str, Any] = {"d": svg_path.to_absolute(d)}
                if fill:
                    p["fill"] = fill
                if stroke:
                    p["stroke"] = stroke
                    try:
                        p["sw"] = float(attrs.get("stroke-width") or 1)
                    except ValueError:
                        p["sw"] = 1.0
                    if attrs.get("stroke-linecap") in ("round", "square"):
                        p["cap"] = attrs["stroke-linecap"]
                    if attrs.get("stroke-linejoin") in ("round", "bevel"):
                        p["join"] = attrs["stroke-linejoin"]
                if "evenodd" in (attrs.get("fill-rule"), attrs.get("clip-rule")):
                    p["rule"] = "evenodd"
                if p["d"]:
                    paths.append(p)
        for child in node:
            walk(child, attrs)

    walk(root, {})
    return {"vb": [24.0, 24.0], "paths": paths[:200]} if paths else None
