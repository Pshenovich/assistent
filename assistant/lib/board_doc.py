"""Холст (лист kind=board): JSON-документ доски — блоки, фигуры, линии и стрелки.

Тело листа — ``{"v": 1, "elements": {id: {...}}}``. Здесь: проверка тела перед
сохранением, текстовая замена (поиск, ИИ, экспорт) и простой SVG для публичной
страницы. Рисует и правит доску клиент — webapp/board.js.
"""

from __future__ import annotations

import html
import json
import math
import re
from typing import Any, Optional

BOARD_VERSION = 1
MAX_BOARD_BYTES = 12 * 1024 * 1024
MAX_ELEMENTS = 5000
MAX_TEXT = 20000
ELEMENT_TYPES = ("text", "rect", "diamond", "ellipse", "sticky", "line", "pen", "image")
SHAPE_TYPES = ("text", "rect", "diamond", "ellipse", "sticky", "image")
_IMAGE_PREFIXES = ("data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp", "data:image/gif")
_PATH_RE = re.compile(r"^[MLCQZmlcqz0-9.,\s-]{1,20000}$")
_STYLE_KEYS = ("fill", "stroke", "strokeWidth", "dash", "fontSize", "align", "textColor", "bold")


class BoardError(ValueError):
    pass


def empty_board() -> str:
    return json.dumps({"v": BOARD_VERSION, "elements": {}}, ensure_ascii=False, separators=(",", ":"))


def _num(raw: Any, default: float = 0.0, lo: float = -1e7, hi: float = 1e7) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v):
        return default
    v = max(lo, min(hi, v))
    # Целые — без «.0»: тело совпадает с тем, что пишет клиент.
    return int(v) if float(v).is_integer() else round(v, 2)


def _str(raw: Any, limit: int = 64) -> str:
    return str(raw if raw is not None else "")[:limit]


MAX_WAYPOINTS = 32  # точки излома стрелки


def _end(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict) or not raw.get("el"):
        return None
    out: dict[str, Any] = {"el": _str(raw.get("el"))}
    side = raw.get("side")
    if side in ("top", "right", "bottom", "left"):
        out["side"] = side
    anchor = raw.get("anchor")
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        out["anchor"] = [_num(anchor[0], 0.5, 0, 1), _num(anchor[1], 0.5, 0, 1)]
    return out


def _author(raw: Any) -> Optional[dict[str, Any]]:
    """Автор стикера: id пользователя, имя для подписи; ai — стикер добавил Лео."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    uid = _str(raw.get("id"), 32).strip()
    name = _str(raw.get("name"), 64).strip()
    if uid:
        out["id"] = uid
    if name:
        out["name"] = name
    if raw.get("ai") is True:
        out["ai"] = True
    return out if (uid or name) else None


def _points(raw: Any, limit: int = 20000) -> list[list[float]]:
    if not isinstance(raw, list):
        return []
    out: list[list[float]] = []
    for p in raw[:limit]:
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            out.append([round(_num(p[0]), 2), round(_num(p[1]), 2)])
    return out


def _clean_element(eid: str, raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    etype = raw.get("type")
    if etype not in ELEMENT_TYPES:
        return None
    el: dict[str, Any] = {
        "id": eid,
        "type": etype,
        "x": _num(raw.get("x")),
        "y": _num(raw.get("y")),
        "w": _num(raw.get("w"), 0, 0, 1e6),
        "h": _num(raw.get("h"), 0, 0, 1e6),
        "z": _num(raw.get("z")),
    }
    if raw.get("rot"):
        el["rot"] = _num(raw.get("rot"), 0, -360, 360)
    if raw.get("group"):
        el["group"] = _str(raw.get("group"))
    if raw.get("text"):
        el["text"] = _str(raw.get("text"), MAX_TEXT)
    style = raw.get("style")
    if isinstance(style, dict):
        st: dict[str, Any] = {}
        for k in _STYLE_KEYS:
            v = style.get(k)
            if v is None:
                continue
            st[k] = v if isinstance(v, (int, float, bool)) else _str(v, 32)
        if st:
            el["style"] = st
    if etype == "sticky":
        author = _author(raw.get("author"))
        if author:
            el["author"] = author
    if etype == "line":
        el["points"] = _points(raw.get("points"), 2) or [[el["x"], el["y"]], [el["x"] + 100, el["y"]]]
        for k in ("start", "end"):
            end = _end(raw.get(k))
            if end:
                el[k] = end
        for k in ("arrowStart", "arrowEnd"):
            if raw.get(k):
                el[k] = True
        route = raw.get("route")
        el["route"] = route if route in ("straight", "curve", "elbow") else "straight"
        if raw.get("bend"):
            el["bend"] = _num(raw.get("bend"), 0, -1e5, 1e5)
        if raw.get("labelT") is not None:
            el["labelT"] = _num(raw.get("labelT"), 0.5, 0, 1)  # где подпись: доля длины стрелки
        wps = _points(raw.get("waypoints"), MAX_WAYPOINTS)
        if wps:
            el["waypoints"] = wps
        d = str(raw.get("d") or "")
        if d and _PATH_RE.match(d):
            el["d"] = d  # путь, посчитанный клиентом (изгиб, излом) — для SVG без клиента
    elif etype == "pen":
        el["points"] = _points(raw.get("points"))
    elif etype == "image":
        src = str(raw.get("src") or "")
        if not src.lower().startswith(_IMAGE_PREFIXES):
            return None
        el["src"] = src
    return el


def parse_board(body: Any) -> dict[str, Any]:
    """Тело листа → ``{"v", "elements"}``; кривое или пустое тело — пустая доска."""
    if isinstance(body, dict):
        data = body
    else:
        raw = str(body or "").strip()
        if not raw:
            return {"v": BOARD_VERSION, "elements": {}}
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {"v": BOARD_VERSION, "elements": {}}
    if not isinstance(data, dict):
        return {"v": BOARD_VERSION, "elements": {}}
    elements = data.get("elements")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(elements, dict):
        items = list(elements.items())
    elif isinstance(elements, list):
        items = [(str((e or {}).get("id") or ""), e) for e in elements if isinstance(e, dict)]
    else:
        items = []
    for eid, raw_el in items[:MAX_ELEMENTS]:
        eid = _str(eid)
        if not eid:
            continue
        el = _clean_element(eid, raw_el)
        if el is not None:
            out[eid] = el
    return {"v": BOARD_VERSION, "elements": out}


def normalize_board_body(body: Any) -> str:
    """Проверка тела перед сохранением: JSON-объект доски не больше лимита."""
    raw = str(body or "").strip()
    if not raw:
        return empty_board()
    if len(raw.encode("utf-8")) > MAX_BOARD_BYTES:
        raise BoardError("Холст слишком большой")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise BoardError("Некорректные данные холста") from e
    if not isinstance(data, dict):
        raise BoardError("Некорректные данные холста")
    board = parse_board(data)
    return json.dumps(board, ensure_ascii=False, separators=(",", ":"))


def _sorted_shapes(elements: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    shapes = [e for e in elements.values() if e.get("type") in SHAPE_TYPES]
    # Сверху вниз, слева направо; близкие по высоте — одна «строка».
    return sorted(shapes, key=lambda e: (round(float(e.get("y") or 0) / 40), float(e.get("x") or 0)))


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def board_plain_text(body: Any) -> str:
    """Текст холста: подписи блоков по порядку и связи «А → Б» — для поиска и ИИ."""
    elements = parse_board(body)["elements"]
    lines: list[str] = []
    for el in _sorted_shapes(elements):
        text = str(el.get("text") or "").strip()
        if text:
            lines.append(text)
    links: list[str] = []
    for el in elements.values():
        if el.get("type") != "line":
            continue
        a = (elements.get((el.get("start") or {}).get("el") or "") or {}).get("text")
        b = (elements.get((el.get("end") or {}).get("el") or "") or {}).get("text")
        label = _one_line(el.get("text") or "")
        if a and b:
            arrow = "↔" if el.get("arrowStart") and el.get("arrowEnd") else ("→" if el.get("arrowEnd") else ("←" if el.get("arrowStart") else "—"))
            links.append(f"{_one_line(a)} {arrow} {_one_line(b)}" + (f" ({label})" if label else ""))
        elif label:
            links.append(label)
    if links:
        lines.append("")
        lines.extend(links)
    return "\n".join(lines).strip()


_SHAPE_RU = {
    "rect": "блок", "diamond": "ромб/решение", "ellipse": "овал", "sticky": "стикер", "text": "текст", "image": "картинка",
}
_COLOR_RU = {
    "red": "красный", "orange": "оранжевый", "yellow": "жёлтый", "green": "зелёный", "blue": "синий",
    "purple": "фиолетовый", "pink": "розовый", "gray": "серый",
}


def board_describe(body: Any, title: str = "") -> str:
    """Описание доски для модели: блоки (форма, цвет, текст), связи с подписями, группы, рисунки."""
    elements = parse_board(body)["elements"]
    if not elements:
        return "Доска пустая."
    shapes = _sorted_shapes(elements)
    num = {el["id"]: i + 1 for i, el in enumerate(shapes)}

    def name(eid: str) -> str:
        el = elements.get(eid) or {}
        text = _one_line(el.get("text") or "")
        return f"#{num[eid]} «{text}»" if eid in num and text else (f"#{num[eid]}" if eid in num else "?")

    out = [f"Доска «{title}»:" if title else "Доска:", "Блоки (сверху вниз, слева направо):"]
    for el in shapes:
        st = el.get("style") or {}
        color = st.get("fill") if el["type"] == "sticky" else (st.get("fill") if st.get("fill") not in (None, "none") else st.get("stroke"))
        kind = _SHAPE_RU.get(el["type"], el["type"])
        if color in _COLOR_RU:
            kind += ", " + _COLOR_RU[str(color)]
        text = _one_line(el.get("text") or "")
        author = el.get("author") or {}
        if el["type"] == "sticky" and author.get("name"):
            kind += ", автор " + str(author["name"])  # кто что предложил — Лео отвечает и на это
        out.append(f"- #{num[el['id']]} [{kind}] {text}".rstrip())
    links = []
    free_lines = 0
    for el in elements.values():
        if el.get("type") != "line":
            continue
        a = (el.get("start") or {}).get("el")
        b = (el.get("end") or {}).get("el")
        label = _one_line(el.get("text") or "")
        if a in num and b in num:
            if el.get("arrowStart") and el.get("arrowEnd"):
                arrow = "↔"
            elif el.get("arrowStart"):
                a, b, arrow = b, a, "→"
            else:
                arrow = "→" if el.get("arrowEnd") else "—"
            dashed = ", пунктир" if (el.get("style") or {}).get("dash") else ""
            extra = f" («{label}»{dashed})" if label else (f" ({dashed[2:]})" if dashed else "")
            links.append(f"- {name(a)} {arrow} {name(b)}{extra}")
        else:
            free_lines += 1
            if label:
                links.append(f"- линия с подписью «{label}»")
    if links:
        out.append("Связи:")
        out.extend(links)
    groups: dict[str, list[str]] = {}
    for el in shapes:
        if el.get("group"):
            groups.setdefault(str(el["group"]), []).append(name(el["id"]))
    for members in groups.values():
        if len(members) > 1:
            out.append("Группа: " + ", ".join(members))
    pens = sum(1 for e in elements.values() if e.get("type") == "pen")
    extras = []
    if pens:
        extras.append(f"рисунков карандашом: {pens}")
    if free_lines:
        extras.append(f"линий без привязки: {free_lines}")
    if extras:
        out.append("Ещё на доске: " + ", ".join(extras) + " (их содержимое текстом не описать).")
    return "\n".join(out)


def board_text_html(body: Any) -> str:
    """Текст холста абзацами — для мест, которые ждут HTML (PDF, документ Базы знаний)."""
    text = board_plain_text(body)
    return "".join(f"<p>{html.escape(line)}</p>" for line in text.split("\n") if line.strip())


# ---- SVG (публичная страница, PDF) ----

# Цвета хранятся ключами палитры (клиент красит их под тему); здесь — светлый вариант.
_INK = {
    "ink": "#1f2937", "gray": "#6b7280", "red": "#e03131", "orange": "#f08c00", "yellow": "#f2c200",
    "green": "#2f9e44", "blue": "#1c7ed6", "purple": "#7048e8", "pink": "#d6336c", "white": "#ffffff",
}
_FILL = {
    "ink": "#dee2e6", "gray": "#f1f3f5", "red": "#ffe3e3", "orange": "#ffe8cc", "yellow": "#fff3bf",
    "green": "#d3f9d8", "blue": "#d0ebff", "purple": "#e5dbff", "pink": "#ffdeeb", "white": "#ffffff",
}
_STICKY = {
    "yellow": "#fff3a3", "orange": "#ffd8a8", "pink": "#ffc9de", "green": "#c8f2c2",
    "blue": "#c5e3ff", "purple": "#e3d4ff", "gray": "#e9ecef",
}


def _ink(v: Any, default: str = "#1f2937") -> str:
    if not v:
        return default
    return _INK.get(str(v), str(v) if str(v).startswith("#") else default)


def _fill(v: Any) -> Optional[str]:
    if not v or v == "none":
        return None
    return _FILL.get(str(v), str(v) if str(v).startswith("#") else None)


def _bounds(elements: dict[str, dict[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for el in elements.values():
        if el.get("type") in ("line", "pen"):
            for p in el.get("points") or []:
                ox = float(el.get("x") or 0) if el.get("type") == "pen" else 0.0
                oy = float(el.get("y") or 0) if el.get("type") == "pen" else 0.0
                xs.append(p[0] + ox)
                ys.append(p[1] + oy)
        else:
            xs += [float(el["x"]), float(el["x"]) + float(el["w"])]
            ys += [float(el["y"]), float(el["y"]) + float(el["h"])]
    if not xs:
        return 0.0, 0.0, 400.0, 300.0
    return min(xs) - 40, min(ys) - 40, max(xs) + 40, max(ys) + 40


def _attr(v: Any) -> str:
    return html.escape(str(v), quote=True)


def render_board_svg(body: Any) -> str:
    """Статичный SVG доски (без правки): фигуры, подписи, линии по сохранённым точкам."""
    elements = parse_board(body)["elements"]
    x0, y0, x1, y1 = _bounds(elements)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" '
        f'width="{w:.0f}" height="{h:.0f}" font-family="system-ui, sans-serif">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        'orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="context-stroke"/></marker></defs>',
    ]
    for el in sorted(elements.values(), key=lambda e: float(e.get("z") or 0)):
        st = el.get("style") or {}
        stroke = _attr(_ink(st.get("stroke")))
        sw = _num(st.get("strokeWidth"), 2, 0, 40)
        dash = ' stroke-dasharray="8 6"' if st.get("dash") == "dashed" else (' stroke-dasharray="2 5"' if st.get("dash") == "dotted" else "")
        t = el["type"]
        x, y, ew, eh = el["x"], el["y"], el["w"], el["h"]
        if t == "line":
            pts = el.get("points") or []
            if len(pts) < 2:
                continue
            ms = ' marker-start="url(#a)"' if el.get("arrowStart") else ""
            me = ' marker-end="url(#a)"' if el.get("arrowEnd") else ""
            d = el.get("d") or f"M{pts[0][0]},{pts[0][1]}L{pts[-1][0]},{pts[-1][1]}"
            out.append(
                f'<path d="{d}" fill="none" stroke="{stroke}" '
                f'stroke-width="{sw}"{dash}{ms}{me}/>'
            )
            continue
        if t == "pen":
            pts = el.get("points") or []
            if len(pts) < 2:
                continue
            d = "M" + "L".join(f"{p[0] + x:.1f},{p[1] + y:.1f}" for p in pts)
            out.append(f'<path d="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>')
            continue
        fill = _fill(st.get("fill"))
        if t == "sticky":
            fill = _STICKY.get(str(st.get("fill") or "yellow"), _STICKY["yellow"])
        fill_attr = _attr(fill) if fill else "none"
        if t == "rect":
            out.append(f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="8" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t == "sticky":
            out.append(f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="4" fill="{fill_attr}"/>')
        elif t == "ellipse":
            out.append(f'<ellipse cx="{x + ew / 2}" cy="{y + eh / 2}" rx="{ew / 2}" ry="{eh / 2}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t == "diamond":
            cx, cy = x + ew / 2, y + eh / 2
            out.append(f'<polygon points="{cx},{y} {x + ew},{cy} {cx},{y + eh} {x},{cy}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t == "image":
            out.append(f'<image x="{x}" y="{y}" width="{ew}" height="{eh}" href="{_attr(el.get("src") or "")}"/>')
        text = str(el.get("text") or "")
        if text and t != "image":
            fs = _num(st.get("fontSize"), 16, 6, 200)
            color = _attr(_ink(st.get("textColor"), "#111827"))
            lines = text.split("\n")
            lh = fs * 1.3
            cy = y + eh / 2 - (len(lines) - 1) * lh / 2
            align = st.get("align")
            if align not in ("left", "center", "right"):
                align = "left" if t == "text" else "center"
            anchor = {"left": "start", "center": "middle", "right": "end"}[align]
            # Поля как в редакторе: у текста без отступа, у ромба — 22% ширины, у остальных 10px.
            pad = 0 if t == "text" else ew * 0.22 if t == "diamond" else 10
            tx = x + pad if align == "left" else x + ew - pad if align == "right" else x + ew / 2
            spans = "".join(
                f'<tspan x="{tx}" y="{cy + i * lh:.1f}">{html.escape(line)}</tspan>' for i, line in enumerate(lines)
            )
            out.append(f'<text font-size="{fs}" fill="{color}" text-anchor="{anchor}" dominant-baseline="middle">{spans}</text>')
    out.append("</svg>")
    return "".join(out)
