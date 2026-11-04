"""Макет → спецификация для разработчика и агента: дерево узлов, HTML/CSS, SVG фрейма.

Тело листа-макета (``board_doc``) хранит элементы плоско, координаты — мировые. Здесь фрейм
раскладывается в дерево (дети — по полю ``frame``), координаты становятся относительными
родителю, цвета — точными #rrggbb, автолэйаут — flex / grid.
"""

from __future__ import annotations

import html
import re
from typing import Any, Optional

from assistant.lib import board_doc as bd

MAX_DEPTH = 40
_SKIP_TYPES = ("table", "card", "gantt", "sticky")  # элементы доски — в макете не бывают, в код не идут

_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
     "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"],
))


# ── доступ к элементам ─────────────────────────────────────────────────────────────

def _elements(body: Any) -> dict[str, dict[str, Any]]:
    return bd.parse_board(body)["elements"]


def _kids_index(elements: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for el in elements.values():
        f = el.get("frame")
        if f and f in elements and elements[f].get("type") == "frame":
            out.setdefault(f, []).append(el)
    return out


def _is_top_frame(el: dict[str, Any], elements: dict[str, dict[str, Any]]) -> bool:
    if el.get("type") != "frame":
        return False
    parent = elements.get(el.get("frame") or "")
    return not (parent and parent.get("type") == "frame")


def _subtree(elements: dict[str, dict[str, Any]], root_id: str) -> dict[str, dict[str, Any]]:
    kids = _kids_index(elements)
    out: dict[str, dict[str, Any]] = {}
    stack = [(root_id, 0)]
    while stack:
        eid, depth = stack.pop()
        el = elements.get(eid)
        if not el or eid in out or depth > MAX_DEPTH:
            continue
        out[eid] = el
        for k in kids.get(eid, []):
            stack.append((k["id"], depth + 1))
    return out


def list_frames(body: Any) -> list[dict[str, Any]]:
    """Верхние фреймы макета (экраны) — по порядку на полотне: сверху вниз, слева направо."""
    elements = _elements(body)
    frames = [e for e in elements.values() if _is_top_frame(e, elements) and not e.get("hidden")]
    frames.sort(key=lambda e: (round(e["y"] / 200), e["x"]))
    out = []
    for f in frames:
        sub = _subtree(elements, f["id"])
        out.append({
            "id": f["id"],
            "name": f.get("name") or "Фрейм",
            "w": _r(f["w"]), "h": _r(f["h"]),
            "nodes": sum(1 for e in sub.values() if not e.get("hidden")) - 1,
            "autolayout": bool(f.get("layout")),
        })
    return out


def find_frame(body: Any, ref: str) -> Optional[dict[str, Any]]:
    """Фрейм по id или по имени (без учёта регистра); любой фрейм, не только верхний."""
    elements = _elements(body)
    ref = str(ref or "").strip()
    if not ref:
        return None
    el = elements.get(ref)
    if el and el.get("type") == "frame":
        return el
    low = ref.lower()
    named = [e for e in elements.values() if e.get("type") == "frame" and str(e.get("name") or "").strip().lower() == low]
    named.sort(key=lambda e: (not _is_top_frame(e, elements), e["y"], e["x"]))
    return named[0] if named else None


# ── значения ──────────────────────────────────────────────────────────────────────

def _r(v: Any) -> float | int:
    f = round(float(v or 0), 2)
    return int(f) if f == int(f) else f


def _hex_fill(v: Any) -> Optional[str]:
    return bd._fill(v)  # type: ignore[attr-defined]


def _hex_ink(v: Any, default: str = "#000000") -> str:
    return bd._ink(v, default)  # type: ignore[attr-defined]


def _rgba(hex6: str, alpha: float = 1.0) -> str:
    h = hex6.lstrip("#")
    if len(h) == 8:
        alpha *= int(h[6:8], 16) / 255
        h = h[:6]
    if alpha >= 0.999:
        return "#" + h.lower()
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {round(alpha, 3)})"


def _grad(raw: Any) -> Optional[dict[str, Any]]:
    s = str(raw or "").strip().lower()
    if not bd._GRAD_RE.match(s):  # type: ignore[attr-defined]
        return None
    parts = s.split()
    stops = []
    for t in parts[2:]:
        c, p = t.split("@")
        stops.append({"color": c[:7], "alpha": round(int(c[7:9], 16) / 255, 3), "pos": int(p)})
    stops.sort(key=lambda t: t["pos"])
    return {"type": parts[0], "angle": int(parts[1]), "stops": stops}


def _grad_css(g: dict[str, Any]) -> str:
    stops = ", ".join(f"{_rgba(t['color'], t['alpha'])} {t['pos']}%" for t in g["stops"])
    if g["type"] == "radial":
        return f"radial-gradient(circle, {stops})"
    return f"linear-gradient({g['angle'] + 90}deg, {stops})"


def _radii(el: dict[str, Any]) -> Optional[list[float]]:
    st = el.get("style") or {}
    if el["type"] not in ("rect", "frame", "image"):
        return None
    r = bd._radii(st, el["w"], el["h"], 0)  # type: ignore[attr-defined]
    return [_r(v) for v in r] if any(r) else None


def _shadow(st: dict[str, Any]) -> Optional[dict[str, Any]]:
    m = re.match(r"^(-?\d+) (-?\d+) (\d+) (#[0-9a-f]{8})$", str(st.get("shadow") or ""))
    if not m:
        return None
    return {"x": int(m[1]), "y": int(m[2]), "blur": int(m[3]), "color": _rgba(m[4])}


def _layout(el: dict[str, Any]) -> Optional[dict[str, Any]]:
    raw = str(el.get("layout") or "").split()
    if len(raw) < 9:
        return None
    ex = raw[9] if len(raw) > 9 else ""
    out: dict[str, Any] = {
        "mode": {"h": "row", "v": "column", "g": "grid"}[raw[0]],
        "gap": _r(raw[1]),
        "padding": [_r(raw[2]), _r(raw[3]), _r(raw[4]), _r(raw[5])],
        "align": raw[6],
        "justify": "space-between" if raw[7] == "between" else raw[7],
        "hug": {"w": "w" in raw[8], "h": "h" in raw[8]},
    }
    if raw[0] == "g":
        out["columns"] = int(ex[1:]) if ex.startswith("c") else 2
    elif ex == "wrap":
        out["wrap"] = True
    return out


# ── дерево узлов ──────────────────────────────────────────────────────────────────

def _order(kids: list[dict[str, Any]], lay: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Порядок детей: в автолэйауте — как ставит клиент (по главной оси), иначе — по слоям (z)."""
    flow = [k for k in kids if k["type"] not in ("line", "pen")]
    free = [k for k in kids if k["type"] in ("line", "pen")]
    if not lay:
        return sorted(kids, key=lambda e: float(e.get("z") or 0))
    if lay["mode"] == "row" and not lay.get("wrap"):
        flow.sort(key=lambda e: e["x"] + e["w"] / 2)
    elif lay["mode"] == "column":
        flow.sort(key=lambda e: e["y"] + e["h"] / 2)
    else:
        # Строки: сверху вниз (с допуском в полвысоты), в строке — слева направо.
        flow.sort(key=lambda e: (e["y"] + e["h"] / 2, e["x"]))
        rows: list[list[dict[str, Any]]] = []
        for e in flow:
            cy = e["y"] + e["h"] / 2
            if rows and abs(cy - (rows[-1][0]["y"] + rows[-1][0]["h"] / 2)) <= min(e["h"], rows[-1][0]["h"]) / 2:
                rows[-1].append(e)
            else:
                rows.append([e])
        flow = [e for row in rows for e in sorted(row, key=lambda q: q["x"])]
    return flow + sorted(free, key=lambda e: float(e.get("z") or 0))


def _node(el: dict[str, Any], parent: Optional[dict[str, Any]], kids: dict[str, list[dict[str, Any]]],
          depth: int, assets: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    t = el["type"]
    if el.get("hidden") or t in _SKIP_TYPES or depth > MAX_DEPTH:
        return None
    st = el.get("style") or {}
    px, py = (parent["x"], parent["y"]) if parent else (el["x"], el["y"])
    n: dict[str, Any] = {"id": el["id"], "type": t}
    if el.get("name"):
        n["name"] = el["name"]
    if t in ("line", "pen"):
        pts = el.get("points") or []
        ox, oy = (el["x"], el["y"]) if t == "pen" else (0.0, 0.0)
        abs_pts = [[p[0] + ox, p[1] + oy] for p in pts]
        if not abs_pts:
            return None
        x0 = min(p[0] for p in abs_pts)
        y0 = min(p[1] for p in abs_pts)
        n.update({"x": _r(x0 - px), "y": _r(y0 - py),
                  "w": _r(max(p[0] for p in abs_pts) - x0), "h": _r(max(p[1] for p in abs_pts) - y0)})
        n["points"] = [[_r(p[0] - x0), _r(p[1] - y0)] for p in abs_pts]
        if t == "line":
            n["route"] = el.get("route") or "straight"
            if el.get("d"):
                n["d"] = el["d"]
                n["d_origin"] = [_r(x0), _r(y0)]  # путь в мировых координатах: сдвинуть на минус d_origin
            for k in ("arrowStart", "arrowEnd"):
                if el.get(k):
                    n[k] = True
        n["stroke"] = {"color": _hex_ink(st.get("stroke")), "width": _r(bd._num(st.get("strokeWidth"), 2, 0, 40))}
        if st.get("dash") in ("dashed", "dotted"):
            n["stroke"]["dash"] = st["dash"]
        return n
    n.update({"x": _r(el["x"] - px), "y": _r(el["y"] - py), "w": _r(el["w"]), "h": _r(el["h"])})
    if el.get("rot"):
        n["rotation"] = _r(el["rot"])
    if st.get("opacity") is not None and float(st["opacity"]) < 1:
        n["opacity"] = _r(st["opacity"])
    # Размеры в автолэйауте: «заполнить», «по содержимому», границы.
    play = _layout(parent) if parent else None
    sizing: dict[str, str] = {}
    own = _layout(el) if t == "frame" else None
    for ax in ("w", "h"):
        if play and ax in str(el.get("grow") or "") and not play["hug"][ax]:
            sizing[ax] = "fill"
        elif own and own["hug"][ax]:
            sizing[ax] = "hug"
        elif t == "text" and (ax == "h" or el.get("autoW")):
            sizing[ax] = "hug"
        else:
            sizing[ax] = "fixed"
    n["sizing"] = sizing
    for k in ("minW", "maxW", "minH", "maxH"):
        if el.get(k):
            n[k] = _r(el[k])
    if el.get("ratio"):
        n["keepRatio"] = True
    if own:
        n["layout"] = own
    if t == "frame":
        n["clip"] = not el.get("noclip")
    # Заливка и обводка.
    if t in ("rect", "ellipse", "diamond", "frame", "text", "vector"):
        g = _grad(st.get("fillGrad"))
        if st.get("fillOff"):
            pass
        elif g:
            n["fill"] = {"gradient": g}
        else:
            raw_fill = st.get("fill")
            if t == "frame" and raw_fill is None:
                raw_fill = "#ffffff"
            if t == "text" or t == "vector":
                raw_fill = raw_fill if t == "vector" and raw_fill not in (None, "none") else None
            color = _hex_fill(raw_fill)
            if color:
                a = float(st["fillOpacity"]) if st.get("fillOpacity") is not None else 1.0
                n["fill"] = {"color": color, **({"opacity": _r(a)} if a < 1 else {})}
        sw = float(bd._num(st.get("strokeWidth"), 0, 0, 1000))
        if sw > 0 and not st.get("strokeOff") and t != "text":
            stroke: dict[str, Any] = {"width": _r(sw), "align": st.get("strokeAlign") or "center"}
            sg = _grad(st.get("strokeGrad"))
            if sg:
                stroke["gradient"] = sg
            else:
                stroke["color"] = _hex_ink(st.get("stroke"))
                if st.get("strokeOpacity") is not None and float(st["strokeOpacity"]) < 1:
                    stroke["opacity"] = _r(st["strokeOpacity"])
            if st.get("dash") in ("dashed", "dotted"):
                stroke["dash"] = st["dash"]
            n["stroke"] = stroke
    radius = _radii(el)
    if radius:
        n["radius"] = radius[0] if len(set(radius)) == 1 else radius
    effects: dict[str, Any] = {}
    sh = _shadow(st)
    if sh:
        effects["shadow"] = sh
    if st.get("blur"):
        effects["blur"] = _r(st["blur"])
    if st.get("bgBlur"):
        effects["backgroundBlur"] = _r(st["bgBlur"])
    if effects:
        n["effects"] = effects
    # Текст (у текста и подпись внутри фигуры).
    text = str(el.get("text") or "")
    if text and t not in ("image", "frame", "vector"):
        fs = _r(bd._num(st.get("fontSize"), 16, 6, 400))
        tx: dict[str, Any] = {
            "content": text,
            "fontFamily": st.get("fontFamily") or "Inter",
            "fontSize": fs,
            "fontWeight": int(st.get("fontWeight") or (600 if st.get("bold") else 400)),
            "lineHeight": _r(st["lineHeight"]) if st.get("lineHeight") else "auto",
            "color": _hex_ink(st.get("textColor"), "#000000"),
            "align": st.get("align") if st.get("align") in ("left", "center", "right") else ("left" if t == "text" else "center"),
            "valign": st.get("valign") or "center",
        }
        if st.get("letterSpacing"):
            tx["letterSpacing"] = f"{_r(st['letterSpacing'])}%"
        if st.get("deco"):
            tx["decoration"] = st["deco"]
        if st.get("textCase"):
            tx["case"] = st["textCase"]
        if el.get("autoW"):
            tx["noWrap"] = True
        if t == "text" and st.get("fill") and st.get("fill") != "none":
            tx["background"] = _hex_fill(st.get("fill"))
        n["text"] = tx
    if t == "image":
        src = str(el.get("src") or "")
        mime = src[5:src.find(";")] if src.startswith("data:") and ";" in src else "image/png"
        ext = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
        n["image"] = {"asset": f"{el['id']}.{ext}", "mime": mime, "bytes": len(src) * 3 // 4}
        assets[el["id"]] = {"id": el["id"], "file": n["image"]["asset"], "mime": mime, "w": n["w"], "h": n["h"]}
    if t == "vector":
        n["svg"] = _vector_svg(el)
    if t == "frame":
        children = []
        for k in _order(kids.get(el["id"], []), own):
            c = _node(k, el, kids, depth + 1, assets)
            if c:
                children.append(c)
        n["children"] = children
    return n


def frame_spec(body: Any, ref: str) -> Optional[dict[str, Any]]:
    """Дерево фрейма: {frame, tokens, assets}; размеры в px, координаты — от левого верхнего угла родителя."""
    root = find_frame(body, ref)
    if not root:
        return None
    elements = _elements(body)
    kids = _kids_index(elements)
    assets: dict[str, dict[str, Any]] = {}
    tree = _node(root, None, kids, 0, assets)
    if tree is None:
        return None
    tree["x"] = tree["y"] = 0
    return {"frame": tree, "tokens": _tokens(tree), "assets": list(assets.values())}


def _walk(n: dict[str, Any]):
    yield n
    for c in n.get("children") or []:
        yield from _walk(c)


def _tokens(tree: dict[str, Any]) -> dict[str, Any]:
    """Цвета и текстовые стили фрейма — по частоте, чтобы агент собрал из них переменные."""
    colors: dict[str, int] = {}
    styles: dict[tuple, int] = {}

    def add(c: Optional[str]) -> None:
        if c:
            colors[c] = colors.get(c, 0) + 1

    for n in _walk(tree):
        f = n.get("fill") or {}
        add(f.get("color"))
        for s in (f.get("gradient") or {}).get("stops", []):
            add(s["color"])
        add((n.get("stroke") or {}).get("color"))
        tx = n.get("text")
        if tx:
            add(tx["color"])
            key = (tx["fontFamily"], tx["fontSize"], tx["fontWeight"], tx["lineHeight"], tx.get("letterSpacing", ""))
            styles[key] = styles.get(key, 0) + 1
    return {
        "colors": [{"value": c, "uses": k} for c, k in sorted(colors.items(), key=lambda kv: -kv[1])],
        "textStyles": [
            {"fontFamily": k[0], "fontSize": k[1], "fontWeight": k[2], "lineHeight": k[3],
             **({"letterSpacing": k[4]} if k[4] else {}), "uses": v}
            for k, v in sorted(styles.items(), key=lambda kv: -kv[1])
        ],
        "fonts": sorted({k[0] for k in styles}),
    }


# ── SVG кусочки ───────────────────────────────────────────────────────────────────

def _vector_svg(el: dict[str, Any]) -> str:
    one = dict(el, x=0, y=0)
    s = bd._svg_vector(one)  # type: ignore[attr-defined]
    return s.replace('<svg x="0" y="0" ', '<svg xmlns="http://www.w3.org/2000/svg" ', 1)


def _line_svg(n: dict[str, Any]) -> str:
    st = n["stroke"]
    w, h = max(1, n["w"]), max(1, n["h"])
    if n.get("d"):
        ox, oy = n["d_origin"]
        d = n["d"]
        tr = f' transform="translate({_r(-ox)} {_r(-oy)})"'
    else:
        d = "M" + "L".join(f"{p[0]},{p[1]}" for p in n["points"])
        tr = ""
    dash = ' stroke-dasharray="8 6"' if st.get("dash") == "dashed" else ' stroke-dasharray="2 5"' if st.get("dash") == "dotted" else ""
    marker = ""
    if n.get("arrowEnd") or n.get("arrowStart"):
        marker = ('<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                  'orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="context-stroke"/></marker></defs>')
    ms = ' marker-start="url(#a)"' if n.get("arrowStart") else ""
    me = ' marker-end="url(#a)"' if n.get("arrowEnd") else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" overflow="visible">{marker}'
            f'<path d="{d}"{tr} fill="none" stroke="{st["color"]}" stroke-width="{st["width"]}" '
            f'stroke-linecap="round" stroke-linejoin="round"{dash}{ms}{me}/></svg>')


def _diamond_svg(n: dict[str, Any]) -> str:
    w, h = n["w"], n["h"]
    fill = _fill_value(n) or "none"
    st = n.get("stroke") or {}
    stroke = f' stroke="{st.get("color", "#000000")}" stroke-width="{st["width"]}"' if st else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" overflow="visible">'
            f'<polygon points="{_r(w / 2)},0 {w},{_r(h / 2)} {_r(w / 2)},{h} 0,{_r(h / 2)}" fill="{fill}"{stroke}/></svg>')


def _fill_value(n: dict[str, Any]) -> Optional[str]:
    f = n.get("fill") or {}
    if f.get("color"):
        return _rgba(f["color"], float(f.get("opacity", 1)))
    return None


# ── HTML / CSS ────────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    s = "".join(_TRANSLIT.get(ch, ch) for ch in name.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:32].strip("-")


class _Css:
    def __init__(self) -> None:
        self.rules: list[tuple[str, list[str]]] = []
        self.used: dict[str, int] = {}
        self.ids: dict[str, str] = {}

    def cls(self, n: dict[str, Any]) -> str:
        base = _slug(str(n.get("name") or "")) or n["type"]
        if base[0].isdigit():
            base = f"{n['type']}-{base}"
        k = self.used.get(base, 0) + 1
        self.used[base] = k
        out = base if k == 1 else f"{base}-{k}"
        self.ids[n["id"]] = out
        return out

    def add(self, cls: str, props: list[str]) -> None:
        if props:
            self.rules.append((cls, props))

    def rule(self, cls: str) -> str:
        return "\n\n".join(f".{c} {{\n" + "".join(f"  {p};\n" for p in props) + "}" for c, props in self.rules if c == cls)

    def text(self) -> str:
        return "\n\n".join(f".{c} {{\n" + "".join(f"  {p};\n" for p in props) + "}" for c, props in self.rules)


def _font_css(tx: dict[str, Any]) -> list[str]:
    fam = tx["fontFamily"]
    props = [f"font-family: '{fam}', system-ui, sans-serif", f"font-size: {tx['fontSize']}px"]
    if tx["fontWeight"] != 400:
        props.append(f"font-weight: {tx['fontWeight']}")
    props.append(f"line-height: {tx['lineHeight']}px" if tx["lineHeight"] != "auto" else "line-height: 1.3")
    if tx.get("letterSpacing"):
        props.append(f"letter-spacing: {_r(float(tx['letterSpacing'][:-1]) / 100)}em")
    props.append(f"color: {tx['color']}")
    if tx["align"] != "left":
        props.append(f"text-align: {tx['align']}")
    if tx.get("decoration"):
        props.append("text-decoration: " + ("underline" if tx["decoration"] == "underline" else "line-through"))
    if tx.get("case"):
        props.append("text-transform: " + {"upper": "uppercase", "lower": "lowercase", "title": "capitalize"}[tx["case"]])
    if tx.get("noWrap"):
        props.append("white-space: pre")
    else:
        props.append("white-space: pre-wrap")
    return props


def _box_css(n: dict[str, Any], parent: Optional[dict[str, Any]]) -> list[str]:
    props: list[str] = []
    play = (parent or {}).get("layout")
    in_flow = bool(play) and n["type"] not in ("line", "pen")
    if parent is None:
        props.append("position: relative")
    elif not in_flow:
        props += ["position: absolute", f"left: {n['x']}px", f"top: {n['y']}px"]
    sz = n.get("sizing") or {}
    for ax, prop in (("w", "width"), ("h", "height")):
        mode = sz.get(ax, "fixed")
        if mode == "fixed":
            props.append(f"{prop}: {n[ax]}px")
        elif mode == "fill" and play:
            main = "w" if play["mode"] == "row" else "h"
            if play["mode"] == "grid":
                if ax == "w":
                    props.append("width: 100%")
                else:
                    props.append("align-self: stretch")
            elif ax == main:
                props.append(f"flex: 1 1 0; min-{prop}: 0")
            else:
                props.append("align-self: stretch")
    for k, prop in (("minW", "min-width"), ("maxW", "max-width"), ("minH", "min-height"), ("maxH", "max-height")):
        if n.get(k):
            props.append(f"{prop}: {n[k]}px")
    if in_flow and play["mode"] != "grid" and sz.get("w" if play["mode"] == "row" else "h") != "fill":
        props.append("flex-shrink: 0")
    if n.get("rotation"):
        props.append(f"transform: rotate({n['rotation']}deg)")
    if n.get("opacity") is not None:
        props.append(f"opacity: {n['opacity']}")
    if n["type"] in ("rect", "frame", "ellipse") or (n["type"] == "text" and (n.get("text") or {}).get("background")):
        f = n.get("fill") or {}
        if f.get("gradient"):
            props.append(f"background: {_grad_css(f['gradient'])}")
        elif _fill_value(n):
            props.append(f"background: {_fill_value(n)}")
        elif n["type"] == "text" and n["text"].get("background"):
            props.append(f"background: {n['text']['background']}")
        st = n.get("stroke")
        if st:
            style = {"dashed": "dashed", "dotted": "dotted"}.get(st.get("dash", ""), "solid")
            # Градиентной рамки в CSS нет — берём первый цвет (точный градиент — в спецификации).
            color = _rgba(st["color"], float(st.get("opacity", 1))) if st.get("color") else _rgba(
                st["gradient"]["stops"][0]["color"], st["gradient"]["stops"][0]["alpha"])
            if st["align"] == "outside":
                props.append(f"outline: {st['width']}px {style} {color}")
            else:  # внутрь и по центру — рамкой внутри размера (как Dev Mode у Figma)
                props.append(f"border: {st['width']}px {style} {color}")
                props.append("box-sizing: border-box")
        r = n.get("radius")
        if n["type"] == "ellipse":
            props.append("border-radius: 50%")
        elif isinstance(r, list):
            props.append("border-radius: " + " ".join(f"{v}px" for v in r))
        elif r:
            props.append(f"border-radius: {r}px")
    fx = n.get("effects") or {}
    if fx.get("shadow"):
        s = fx["shadow"]
        val = f"{s['x']}px {s['y']}px {s['blur']}px {s['color']}"
        if n["type"] in ("vector", "text", "diamond"):
            props.append(f"filter: drop-shadow({s['x']}px {s['y']}px {_r(s['blur'] / 2)}px {s['color']})")
        else:
            merged = [p for p in props if p.startswith("box-shadow:")]
            if merged:
                props.remove(merged[0])
                props.append(merged[0] + ", " + val)
            else:
                props.append(f"box-shadow: {val}")
    if fx.get("blur"):
        props.append(f"filter: blur({_r(fx['blur'] / 2)}px)")
    if fx.get("backgroundBlur"):
        props.append(f"backdrop-filter: blur({_r(fx['backgroundBlur'] / 2)}px)")
    return props


def _layout_css(lay: dict[str, Any]) -> list[str]:
    pad = lay["padding"]
    props: list[str] = []
    if lay["mode"] == "grid":
        props += ["display: grid", f"grid-template-columns: repeat({lay['columns']}, minmax(0, 1fr))", f"gap: {lay['gap']}px"]
    else:
        props += ["display: flex", f"flex-direction: {lay['mode']}"]
        if lay.get("wrap"):
            props.append("flex-wrap: wrap")
        if lay["gap"]:
            props.append(f"gap: {lay['gap']}px")
    flexish = {"start": "flex-start", "end": "flex-end", "center": "center", "space-between": "space-between"}
    if lay["align"] != "start":
        props.append(f"align-items: {flexish[lay['align']]}")
    if lay["justify"] != "start":
        props.append(f"justify-content: {flexish[lay['justify']]}")
    if any(pad):
        t, r, b, l = pad
        props.append("padding: " + (f"{t}px" if t == r == b == l else f"{t}px {r}px" if t == b and r == l else f"{t}px {r}px {b}px {l}px"))
    props.append("box-sizing: border-box")
    return props


def _text_inner(tx: dict[str, Any]) -> str:
    return html.escape(tx["content"]).replace("\n", "<br>")


def _emit(n: dict[str, Any], parent: Optional[dict[str, Any]], css: _Css, depth: int, images: str,
          srcs: dict[str, str]) -> str:
    pad = "  " * depth
    cls = css.cls(n)
    props = _box_css(n, parent)
    t = n["type"]
    if t == "frame":
        lay = n.get("layout")
        if lay:
            props += _layout_css(lay)
        if parent is not None and "position: absolute" not in props:
            props.append("position: relative")  # опора для детей с абсолютной позицией
        if n.get("clip"):
            props.append("overflow: hidden")
        css.add(cls, props)
        inner = "\n".join(_emit(c, n, css, depth + 1, images, srcs) for c in n.get("children") or [])
        label = f' data-name="{html.escape(n["name"], quote=True)}"' if n.get("name") else ""
        return f'{pad}<div class="{cls}"{label}>' + (f"\n{inner}\n{pad}" if inner else "") + "</div>"
    if t == "text":
        tx = n["text"]
        props += _font_css(tx)
        css.add(cls, props)
        return f'{pad}<p class="{cls}">{_text_inner(tx)}</p>'
    if t == "image":
        css.add(cls, props + ["object-fit: cover", "display: block"])
        src = srcs.get(n["id"], "") if images == "inline" else f"assets/{n['image']['asset']}"
        alt = html.escape(str(n.get("name") or ""), quote=True)
        return f'{pad}<img class="{cls}" src="{html.escape(src, quote=True)}" alt="{alt}">'
    if t in ("vector", "line", "pen", "diamond"):
        svg = n["svg"] if t == "vector" else _diamond_svg(n) if t == "diamond" else _line_svg(n)
        css.add(cls, props + ["display: block", "overflow: visible"])
        return f'{pad}<div class="{cls}">{svg}</div>'
    # rect / ellipse (с подписью — по центру).
    tx = n.get("text")
    if tx:
        jc = {"top": "flex-start", "bottom": "flex-end"}.get(tx["valign"], "center")
        props += ["display: flex", "flex-direction: column", f"justify-content: {jc}", "padding: 0 10px",
                  "box-sizing: border-box"] + _font_css(tx)
        css.add(cls, props)
        return f'{pad}<div class="{cls}">{_text_inner(tx)}</div>'
    css.add(cls, props)
    return f'{pad}<div class="{cls}"></div>'


def frame_code(body: Any, ref: str, *, images: str = "assets") -> Optional[dict[str, Any]]:
    """HTML + CSS фрейма. images="assets" — картинки ссылками assets/<id>.<ext> (файлы — отдельно),
    "inline" — data:URI прямо в разметке."""
    spec = frame_spec(body, ref)
    if not spec:
        return None
    srcs: dict[str, str] = {}
    if images == "inline":
        for el in _elements(body).values():
            if el["type"] == "image":
                srcs[el["id"]] = str(el.get("src") or "")
    css = _Css()
    markup = _emit(spec["frame"], None, css, 0, images, srcs)
    fonts = spec["tokens"]["fonts"]
    font_link = ""
    if fonts:
        fam = "&".join("family=" + f.replace(" ", "+") + ":wght@300;400;500;600;700" for f in fonts)
        font_link = f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?{fam}&display=swap">'
    return {
        "html": markup,
        "css": "* { margin: 0; }\n\n" + css.text(),
        "fonts": fonts,
        "font_link": font_link,
        "assets": spec["assets"],
        "name": spec["frame"].get("name") or "Фрейм",
        # Узел → класс и его правило: инспектор показывает CSS выделенного слоя.
        "nodes": {nid: {"class": c, "css": css.rule(c)} for nid, c in css.ids.items()},
    }


def frame_page(body: Any, ref: str, *, images: str = "inline") -> Optional[str]:
    """Готовая страница с фреймом — для предпросмотра и «скачать HTML»."""
    code = frame_code(body, ref, images=images)
    if not code:
        return None
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(code['name'])}</title>\n{code['font_link']}\n<style>\n{code['css']}\n</style>\n"
        f"</head>\n<body>\n{code['html']}\n</body>\n</html>\n"
    )


def frame_svg(body: Any, ref: str) -> Optional[str]:
    """Статичный SVG фрейма (без подписи), обрезанный по его рамке."""
    root = find_frame(body, ref)
    if not root:
        return None
    elements = _elements(body)
    sub = _subtree(elements, root["id"])
    view = (root["x"], root["y"], root["w"], root["h"])
    return bd.render_board_svg({"v": 1, "mode": "design", "elements": sub}, view=view, labels=False)


def spec_outline(spec: dict[str, Any]) -> str:
    """Короткое текстовое дерево фрейма — агенту для обзора без полного JSON."""
    lines: list[str] = []

    def walk(n: dict[str, Any], depth: int) -> None:
        bits = [n["type"]]
        if n.get("name"):
            bits.append(f"«{n['name']}»")
        bits.append(f"{n['w']}×{n['h']} @{n['x']},{n['y']}")
        lay = n.get("layout")
        if lay:
            bits.append(f"[{lay['mode']} gap {lay['gap']} pad {' '.join(str(p) for p in lay['padding'])}]")
        tx = n.get("text")
        if tx:
            s = tx["content"].replace("\n", " ⏎ ")
            bits.append(f"“{s[:60]}{'…' if len(s) > 60 else ''}” {tx['fontSize']}/{tx['fontWeight']}")
        lines.append("  " * depth + " ".join(bits) + f"  #{n['id']}")
        for c in n.get("children") or []:
            walk(c, depth + 1)

    walk(spec["frame"], 0)
    return "\n".join(lines)
