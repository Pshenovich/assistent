"""Свободный слайд (free.canvas): элементы с координатами на сцене 1280×720 вместо макета.

Слайд получается «отвязкой» готового слайда в редакторе (браузер меряет отрисованные блоки
и присылает элементы) или пустым. Лео создаёт презентации по макетам, свободные слайды только правит.

slots = {surface: light|dark|accent, elements: [...], origin?: {role, variant, slots}}
Элемент: {id, type, x, y, w, h, z, rot?, ...} — типы text | image | shape | icon | object | chart.
Цвета и шрифты — токены стиля (var(--s-*), --f-*), поэтому смена стиля перекрашивает и свободные слайды.
"""

from __future__ import annotations

import copy
import re
import secrets
from html import escape
from html.parser import HTMLParser
from typing import Any, Optional

from assistant.lib import canvas_charts
from assistant.lib import canvas_icons
from assistant.lib import canvas_templates as ct

STAGE_W, STAGE_H = 1280, 720
MAX_ELEMENTS = 120
MAX_HTML = 2000
SURFACES = ("light", "dark", "accent")
TYPES = ("text", "image", "shape", "icon", "object", "chart")
SIZES = ("s", "m", "l", "xl", "xxl", "xxxl")  # 16 20 28 40 56 80 px (CSS .fr-size-*)
FONTS = ("display", "body")
ALIGNS = ("left", "center", "right")
WEIGHTS = (300, 400, 500, 600, 700)
# Цвет-токен → CSS. grad — градиент стиля (--fr-grad в CSS каждого стиля).
COLORS = {
    "text": "var(--s-text)",
    "muted": "var(--s-muted)",
    "faint": "var(--s-faint)",
    "acc": "var(--s-acc)",
    "on-acc": "var(--s-on-acc)",
    "el": "var(--s-el)",
    "rule": "var(--s-rule)",
    "tint": "var(--s-tint)",
    "acc-soft": "var(--s-acc-soft)",
    "grad": "var(--fr-grad)",
    "white": "#ffffff",
    "black": "#0b0b0b",
    "none": "transparent",
}
SHAPES = ("rect", "round", "circle", "line")
_ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
_MKEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")
FREE_VARIANT = "free.canvas"


def new_id() -> str:
    return secrets.token_hex(4)


# ---------------------------------------------------------------- rich text


class _Sanitizer(HTMLParser):
    """Оставляет b / i / br и span.acc / span.muted; блоки (div, p, li) превращает в переносы строк."""

    _BLOCK = {"div", "p", "li", "h1", "h2", "h3", "h4", "blockquote", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip = 0

    def _br(self) -> None:
        if self.out and self.out[-1] != "<br>":
            self.out.append("<br>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in ("script", "style", "svg", "math", "template"):
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "br":
            self.out.append("<br>")
        elif tag in ("b", "strong"):
            self.out.append("<b>")
            self.open.append("b")
        elif tag in ("i", "em"):
            self.out.append("<i>")
            self.open.append("i")
        elif tag == "span":
            cls = next((v or "" for k, v in attrs if k == "class"), "")
            tone = next((t for t in ("acc", "muted") if t in cls.split()), "")
            self.out.append(f'<span class="{tone}">' if tone else "<span>")
            self.open.append("span")
        elif tag in self._BLOCK:
            self._br()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "svg", "math", "template"):
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        t = {"strong": "b", "em": "i"}.get(tag, tag)
        if t in ("b", "i", "span") and t in self.open:
            while self.open:
                x = self.open.pop()
                self.out.append(f"</{x}>")
                if x == t:
                    break
        elif tag in self._BLOCK:
            self._br()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        s = "".join(self.out)
        s = re.sub(r"^(?:<br>)+|(?:<br>)+$", "", s)
        s = re.sub(r"<(b|i|span)[^>]*></\1>", "", s)
        return s


def sanitize_html(raw: Any) -> str:
    p = _Sanitizer()
    try:
        p.feed(str(raw or "")[: MAX_HTML * 2])
        p.close()
    except Exception:  # noqa: BLE001
        return escape(re.sub(r"<[^>]*>", "", str(raw or "")))[:MAX_HTML]
    return p.result()[:MAX_HTML]


def plain_text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(html or "").replace("<br>", "\n"))).strip()


# ---------------------------------------------------------------- validation


def _num(v: Any, lo: float, hi: float, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f:  # NaN
        return default
    return round(max(lo, min(hi, f)), 1)


def _pick(v: Any, allowed: tuple, default: Any) -> Any:
    return v if v in allowed else default


def _color(v: Any, default: str) -> str:
    return v if isinstance(v, str) and v in COLORS else default


def clean_element(raw: Any, used_ids: set[str]) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    t = raw.get("type")
    if t not in TYPES:
        return None
    eid = str(raw.get("id") or "")
    if not _ID_RE.match(eid) or eid in used_ids:
        eid = new_id()
    used_ids.add(eid)
    el: dict[str, Any] = {
        "id": eid,
        "type": t,
        "x": _num(raw.get("x"), -STAGE_W / 2, STAGE_W * 1.5, 80),
        "y": _num(raw.get("y"), -STAGE_H / 2, STAGE_H * 1.5, 80),
        "w": _num(raw.get("w"), 4, STAGE_W * 2, 320),
        "h": _num(raw.get("h"), 2, STAGE_H * 2, 80),
        "z": int(_num(raw.get("z"), 0, 999, 1)),
    }
    rot = _num(raw.get("rot"), -180, 180, 0)
    if rot:
        el["rot"] = rot
    st = raw.get("style") if isinstance(raw.get("style"), dict) else {}
    if t == "text":
        el["html"] = sanitize_html(raw.get("html") if raw.get("html") is not None else escape(str(raw.get("text") or "")))
        el["style"] = {
            "size": _pick(st.get("size"), SIZES, "m"),
            "font": _pick(st.get("font"), FONTS, "body"),
            "align": _pick(st.get("align"), ALIGNS, "left"),
            "color": _color(st.get("color"), "text"),
            "weight": _pick(st.get("weight"), WEIGHTS, 400),
        }
        if st.get("bg") in COLORS and st.get("bg") != "none":
            el["style"]["bg"] = st["bg"]
    elif t == "shape":
        el["shape"] = _pick(raw.get("shape"), SHAPES, "rect")
        el["style"] = {
            "fill": _color(st.get("fill"), "el"),
            "stroke": _color(st.get("stroke"), "none"),
            "radius": int(_num(st.get("radius"), 0, 400, 16 if el["shape"] == "round" else 0)),
        }
    elif t in ("image", "object"):
        mkey = str(raw.get("mkey") or "")
        el["mkey"] = mkey if _MKEY_RE.match(mkey) else f"f-{eid}"
        if t == "image":
            prompt = str(raw.get("prompt") or "").strip()
            if prompt:
                el["prompt"] = prompt[:300]
            el["style"] = {"radius": int(_num(st.get("radius"), 0, 400, 16))}
        else:
            vis = str(raw.get("visual") or "").strip().lower()
            el["visual"] = vis if vis in ct._VISUALS else ct._VISUALS[0]
            el["style"] = {"radius": int(_num(st.get("radius"), 0, 400, 16))}
    elif t == "icon":
        name = canvas_icons.norm(raw.get("icon"))
        if not name:
            return None
        el["icon"] = name
        el["style"] = {"color": _color(st.get("color"), "acc")}
    elif t == "chart":
        data = raw.get("chart") if isinstance(raw.get("chart"), dict) else {}
        el["chart"] = ct._clean(data) or {}
        ctype = str(raw.get("ctype") or data.get("type") or "").strip()
        if ctype:
            el["ctype"] = ctype[:20]
    return el


def clean_elements(raw: Any) -> list[dict[str, Any]]:
    used: set[str] = set()
    out = []
    for r in raw if isinstance(raw, list) else []:
        el = clean_element(r, used)
        if el:
            out.append(el)
        if len(out) >= MAX_ELEMENTS:
            break
    return out


def clean_slots(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {
        "surface": _pick(src.get("surface"), SURFACES, "light"),
        "elements": clean_elements(src.get("elements")),
    }
    origin = src.get("origin")
    if isinstance(origin, dict) and origin.get("role") in ct.ROLE_IDS and origin.get("role") != "free":
        out["origin"] = {
            "role": origin["role"],
            "variant": str(origin.get("variant") or ""),
            "slots": ct._clean(origin.get("slots")) if isinstance(origin.get("slots"), dict) else {},
        }
    return out


# ---------------------------------------------------------------- render


def _box(el: dict[str, Any]) -> str:
    rot = f"transform:rotate({el['rot']}deg);" if el.get("rot") else ""
    return f"left:{el['x']}px;top:{el['y']}px;width:{el['w']}px;height:{el['h']}px;z-index:{el['z']};{rot}"


def _object_variant(theme: str, surface: str) -> str:
    if theme == "lime":
        return "lime-dark" if surface == "dark" else "lime-light"
    return ""


def _render_el(c: ct.Ctx, i: int, el: dict[str, Any], surface: str) -> str:
    t = el["type"]
    st = el.get("style") or {}
    attrs = f'class="fr-el fr-{t}" data-fid="{escape(el["id"])}" style="{_box(el)}"'
    if t == "text":
        path = f"elements.{i}.html"
        css = f"text-align:{st['align']};color:{COLORS[st['color']]};font-weight:{st['weight']};"
        if st.get("bg"):
            css += f"background:{COLORS[st['bg']]};"
        return (
            f'<div {attrs}><div class="fr-text fr-size-{st["size"]} fr-font-{st["font"]}" style="{css}" '
            f'data-slot="{path}" data-leo-id="{c.sid}-{path}" data-rich="1">{el.get("html") or ""}</div></div>'
        )
    if t == "shape":
        if el["shape"] == "line":
            return f'<div {attrs}><i class="fr-line" style="background:{COLORS[st["fill"]]}"></i></div>'
        radius = "50%" if el["shape"] == "circle" else f"{st.get('radius', 0)}px"
        border = f"border:2px solid {COLORS[st['stroke']]};" if st.get("stroke", "none") != "none" else ""
        return f'<div {attrs}><i class="fr-shape" style="background:{COLORS[st["fill"]]};border-radius:{radius};{border}"></i></div>'
    if t == "icon":
        return f'<div {attrs}><span class="fr-icon" style="color:{COLORS[st["color"]]}">{canvas_icons.svg(el["icon"], "ico fr-ico")}</span></div>'
    if t == "image":
        mid = f"{c.sid}-{el['mkey']}"
        slot = ct.media_slot_html(c, mid, el.get("prompt") or "", "fr-media")
        return f'<div {attrs}><div class="fr-clip" style="border-radius:{st.get("radius", 16)}px">{slot}</div></div>'
    if t == "object":
        mid = f"{c.sid}-{el['mkey']}"
        src = c.media.get(mid, "")
        vis = el["visual"]
        om = ct._OBJECT_SRC_RE.match(src)
        if om and om.group(1) in ct._VISUALS:
            vis = el["visual"] = om.group(1)  # выбор из библиотеки — смена объекта
            src = ""
        inner = f'<img class="media-img" alt="" src="{escape(src)}"/>' if src else ct._visual_html(vis, _object_variant(c.theme, surface))
        meta = ct.media_meta_attrs(c.media, mid) if src else ""
        return (
            f'<div {attrs}><div class="a3 fr-a3{" a3-photo" if src else ""}" style="border-radius:{st.get("radius", 16)}px" '
            f'data-media="{mid}" data-leo-id="{mid}" data-a3="{vis}"{meta}>{inner}</div></div>'
        )
    if t == "chart":
        data = dict(el.get("chart") or {})
        if el.get("ctype"):
            data["type"] = el["ctype"]
        try:
            svg = canvas_charts.render_chart(data, max(120.0, el["w"]), max(80.0, el["h"]))
        except Exception:  # noqa: BLE001 — кривые данные не ломают слайд
            svg = ""
        return f'<div {attrs}><div class="fr-chart">{svg}</div></div>'
    return ""


def render(c: ct.Ctx) -> tuple[str, str]:
    surface = c.slots["surface"] = _pick(c.slots.get("surface"), SURFACES, "light")
    # Чистим и при отрисовке (JSON деки пишется после неё): индексы data-slot совпадают с сохранёнными.
    els = c.slots["elements"] = clean_elements(c.slots.get("elements"))
    body = "".join(_render_el(c, i, e, surface) for i, e in sorted(enumerate(els), key=lambda p: p[1].get("z", 0)))
    return f'<div class="fr-slide fr-{surface}" data-free-surface="{surface}">{body}</div>', ""


# ---------------------------------------------------------------- operations


def _slide(deck: dict[str, Any], sid: str) -> dict[str, Any]:
    return ct._slide(deck, sid)


def op_detach(deck: dict[str, Any], sid: str, elements: Any, surface: str = "") -> None:
    s = _slide(deck, sid)
    if s.get("variant") == FREE_VARIANT:
        raise ct.DeckOpError("Слайд уже свободный")
    els = clean_elements(elements)
    if not els:
        raise ct.DeckOpError("Не удалось разобрать слайд на элементы")
    s["slots"] = {
        "surface": _pick(surface, SURFACES, "light"),
        "elements": els,
        "origin": {"role": s["role"], "variant": s["variant"], "slots": copy.deepcopy(s.get("slots") or {})},
    }
    s["role"], s["variant"] = "free", FREE_VARIANT


def op_reattach(deck: dict[str, Any], sid: str) -> None:
    s = _slide(deck, sid)
    origin = (s.get("slots") or {}).get("origin")
    if s.get("variant") != FREE_VARIANT or not isinstance(origin, dict):
        raise ct.DeckOpError("У слайда нет исходного макета")
    restored = ct.normalize_slide({"role": origin.get("role"), "variant": origin.get("variant"), "slots": origin.get("slots")}, sid=s["id"])
    s.update(restored)


def op_update(deck: dict[str, Any], sid: str, elements: Any, surface: str = "") -> None:
    s = _slide(deck, sid)
    if s.get("variant") != FREE_VARIANT:
        raise ct.DeckOpError("Слайд не свободный")
    slots = s.setdefault("slots", {})
    slots["elements"] = clean_elements(elements)
    if surface in SURFACES:
        slots["surface"] = surface


def blank_slots(surface: str = "light") -> dict[str, Any]:
    return {
        "surface": surface,
        "elements": [
            {"id": new_id(), "type": "text", "x": 80, "y": 80, "w": 900, "h": 140, "z": 1,
             "html": "Заголовок", "style": {"size": "xxl", "font": "display", "align": "left", "color": "text", "weight": 400}},
            {"id": new_id(), "type": "text", "x": 80, "y": 240, "w": 700, "h": 120, "z": 2,
             "html": "Текст слайда", "style": {"size": "m", "font": "body", "align": "left", "color": "muted", "weight": 400}},
        ],
    }


def op_add_blank(deck: dict[str, Any], after_sid: str, surface: str = "") -> str:
    if len(deck.get("slides") or []) >= ct.MAX_DECK_SLIDES:
        raise ct.DeckOpError(f"В презентации максимум {ct.MAX_DECK_SLIDES} слайдов")
    new = {"id": ct._next_id(deck), "role": "free", "variant": FREE_VARIANT,
           "slots": clean_slots(blank_slots(_pick(surface, SURFACES, "light")))}
    slides = deck.setdefault("slides", [])
    idx = next((i for i, s in enumerate(slides) if str(s.get("id")) == str(after_sid)), len(slides) - 1)
    slides.insert(idx + 1, new)
    return new["id"]


SCHEMA = (
    "surface: light|dark|accent, elements: [{id, type: text|image|shape|icon|object|chart, x, y, w, h (px, сцена 1280×720), z, "
    "html? (text: можно <b>, <i>, <span class=\"acc\">), style?}] — свободный слайд, его сделал пользователь руками. "
    "Сам такие слайды не создавай; правя, сохраняй id, координаты и стиль элементов, меняй html текстов"
)

ct._reg(ct.Variant(
    FREE_VARIANT, "free", "Свободный слайд",
    SCHEMA,
    render,
    lambda s: ct._need(isinstance(s.get("elements"), list), "нет элементов"),
))
