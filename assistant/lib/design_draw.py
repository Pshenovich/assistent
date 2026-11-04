"""Лео рисует экраны на листе «Макет»: модель присылает дерево компонентов (текст, кнопка, поле,
карточка, список…), здесь оно раскладывается автолэйаутом в элементы доски (фреймы с layout,
тексты, прямоугольники) — так же, как их раскладывает webapp/board.js (applyLayout). Точную высоту
текстов клиент подгонит сам после отрисовки и пересоберёт автолэйаут.

Ещё здесь — когда вообще показывать модели макет: только если просьба про него (токены).
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any, Optional

from assistant.lib import board_doc

MAX_NODES = 400
MAX_DEPTH = 12
DEVICES = {
    "iphone": (393, 852), "android": (412, 915), "ipad": (820, 1180),
    "laptop": (1280, 800), "desktop": (1440, 1024),
}
FONT = "Inter"
INK = "#111827"
MUTED = "#6b7280"
PLACEHOLDER = "#9ca3af"
SURFACE = "#f3f4f6"
BORDER = "#e5e7eb"
ACCENT = "#2463b0"

# ---- Когда показывать модели макет ----

_DESIGN_RE = re.compile(
    r"(макет|экран|фрейм|сло[йяиеёю]|кнопк|\bux\b|\bui\b|интерфейс|в[её]рстк|разработчик|опечат|состояни|цвет|шрифт|"
    r"отступ|переимен|скр[ыо]й|скрыть|figma|фигм|дизайн|иконк|пол[ея] ввода|\bформ|модалк|попап|страниц|онбординг|"
    r"прототип|компонент|карточк|навигац|\bменю|шапк|вкладк|таббар|заголов|подпис|текст[аыуе]?\b)",
    re.IGNORECASE,
)
_DRAW_VERB_RE = re.compile(
    r"(нарису|сделай|сделать|создай|создать|добав|собери|сверста|набросай|накидай|спроектир|придумай|дорису|"
    r"сгенерир|построй|нарисовать|вставь|размести)",
    re.IGNORECASE,
)
_DRAW_NOUN_RE = re.compile(
    r"(экран|фрейм|страниц|\bформ|окн|модалк|попап|макет|карточк|шапк|\bменю|список|кнопк|пол[ея]|лендинг|онбординг|"
    r"профил|корзин|вход|регистрац|авторизац|главн|дашборд|вкладк|таббар|блок|секци|состояни)",
    re.IGNORECASE,
)


def design_intent(text: str, has_layers: bool = False) -> str:
    """Что нужно модели от открытого макета: "draw" — рисовать (дерево + инструкции рисования),
    "work" — разбор и правки (дерево + инструкции правок), "" — вопрос не про макет (ничего не шлём)."""
    q = str(text or "")
    if _DRAW_VERB_RE.search(q) and _DRAW_NOUN_RE.search(q):
        return "draw"
    if has_layers or _DESIGN_RE.search(q):
        return "work"
    return ""


def draw_addon() -> str:
    from assistant.lib import ui_icons

    return ("""
## Рисовать экраны на макете
Новый экран (или несколько) — ключ screens в том же блоке ```leo-board (action=update, sheet_id макета).
Координат нет: дерево компонентов, раскладку автолэйаутом и размеры считает Лео.

ГЛАВНОЕ — стиль существующих экранов. Если на макете уже есть такой элемент (нижнее меню, шапка, поле, кнопка,
карточка, строка списка, иконка) — НЕ рисуй заново, а скопируй его по id из дерева: {"type":"copy","from":"id"}.
- "texts": {"id текста внутри копии": "новый текст"} — заменить подписи; "text": "…" — заменить первый текст;
- "set": {"id слоя внутри копии": {"fill":"#rrggbb","textColor":"#rrggbb","stroke":"#rrggbb","hidden":true}} —
  например, в нижнем меню сделать активной другую вкладку (перенести цвет активной вкладки).
Новое (чего на макете нет) — компонентами ниже; цвета текста, фон, поля, карточки, скругления и шрифт Лео возьмёт
из существующих экранов сам, свои цвета указывай, только если просят.
```leo-board
{"action":"update","sheet_id":"123","screens":[{"name":"Вход","device":"iphone","padding":[56,24,32,24],"gap":16,
 "children":[
  {"type":"header","title":"Вход","back":true},
  {"type":"text","text":"Введите e-mail и пароль","color":"muted"},
  {"type":"input","label":"E-mail","placeholder":"name@mail.ru","icon":"mail"},
  {"type":"input","label":"Пароль","placeholder":"••••••••","icon":"lock"},
  {"type":"link","text":"Забыли пароль?","align":"right"},
  {"type":"spacer"},
  {"type":"button","text":"Войти"},
  {"type":"copy","from":"id_нижнего_меню_с_другого_экрана","set":{"id_активной_иконки":{"fill":"#…"}}}]}]}
```
Компоненты (type):
- text {text, size 15, weight 400|500|600|700, color: "ink"|"muted"|"accent"|#rrggbb, align left|center|right};
  link {text} — текст-ссылка цветом акцента;
- button {text, variant: primary|secondary|ghost, icon?}; input {label?, placeholder, icon?}; image {label, h} — заглушка;
  icon {icon, size 24, color?} — векторная иконка; avatar {size 48}; divider; spacer — свободное место по высоте;
- row {children, gap 8, justify start|center|end|between, align center}; column {children, gap 8};
  grid {children, columns 2, gap 12}; card {children, gap 8, padding 16};
- list {items:[{title, subtitle?, value?, icon?, avatar?:true, chevron?:true}]};
  header {title, back?:true, action?:"Готово", action_icon?:"иконка"};
  tabbar {items:[{"title":"Главная","icon":"home"}, …], active 0} — только если на макете нет своего нижнего меню.
- У любого: name — имя слоя по-русски, fill — заливка, grow:true — растянуть в строке.
Иконки (icon) — только из списка: """ + ", ".join(ui_icons.available()) + """.
Иконку текстом («✎», «›», эмодзи) не рисуй — бери из списка.
Экран: name, device iphone|android|ipad|laptop|desktop (по умолчанию iphone), fill, padding [верх, право, низ, лево], gap, children.
Дорисовать в существующий фрейм: "add":[{"into":"id_фрейма","after":"id_слоя (необязательно)","children":[…]}].
Пиши реальные тексты по смыслу заметки, без «Lorem ipsum». Один экран — до ~40 компонентов.
""").strip()


# ---- Стиль макета: акцент, шрифт ----

def _lum(hex6: str) -> float:
    r, g, b = (int(hex6[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _top(counts: dict[str, int], pred=lambda c: True) -> Optional[str]:
    items = [(k, v) for k, v in counts.items() if pred(k)]
    return max(items, key=lambda kv: kv[1])[0] if items else None


def style_of(elements: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Стиль уже нарисованных экранов: акцент, шрифт, цвета текста, фон, поверхности, обводки, скругления."""
    hexre = re.compile(r"^#[0-9a-f]{6}$")
    accents: dict[str, int] = {}
    fonts: dict[str, int] = {}
    inks: dict[str, int] = {}
    bgs: dict[str, int] = {}
    surfaces: dict[str, int] = {}
    borders: dict[str, int] = {}
    radii: list[float] = []
    btn_radii: list[float] = []
    input_radii: list[float] = []
    for el in elements.values():
        st = el.get("style") or {}
        t = el.get("type")
        f = str(st.get("fill") or "").lower()
        top = t == "frame" and not (el.get("frame") and el["frame"] in elements)
        w, h = float(el.get("w") or 0), float(el.get("h") or 0)
        rad = float(st.get("radius") or 0)
        roundish = rad >= min(w, h) / 2 - 0.5 and abs(w - h) < 2  # кружки (кнопки-иконки) — не образец скругления
        sc = str(st.get("stroke") or "").lower()
        stroked = t in ("frame", "rect") and hexre.match(sc) and float(st.get("strokeWidth") or 0) > 0 and not st.get("strokeOff")
        if stroked and not roundish and w > 120 and 32 <= h <= 72:
            input_radii.append(rad)  # поле ввода: широкое, невысокое, с рамкой
        if hexre.match(f) and not st.get("fillOff"):
            if top:
                bgs[f] = bgs.get(f, 0) + 1
            elif t in ("frame", "rect"):
                if _saturated(f):
                    accents[f] = accents.get(f, 0) + 1
                    if float(st.get("radius") or 0) > 0:
                        btn_radii.append(float(st["radius"]))
                elif _lum(f) > 0.8:
                    surfaces[f] = surfaces.get(f, 0) + 1
                    if rad > 0 and not roundish and w > 120 and h > 72:
                        radii.append(rad)  # карточка / блок
        if stroked and not roundish and not _saturated(sc):
            borders[sc] = borders.get(sc, 0) + 1  # рамка — нейтральная, у полей и карточек
        if st.get("fontFamily"):
            fonts[str(st["fontFamily"])] = fonts.get(str(st["fontFamily"]), 0) + 1
        tc = str(st.get("textColor") or "").lower()
        if el.get("text") and hexre.match(tc):
            inks[tc] = inks.get(tc, 0) + len(str(el.get("text")))
    ink = _top(inks, lambda c: _lum(c) < 0.35) or INK
    muted = _top(inks, lambda c: c != ink and 0.25 < _lum(c) < 0.8) or MUTED
    bg = _top(bgs) or "#ffffff"
    mid = lambda xs, d: sorted(xs)[len(xs) // 2] if xs else d  # noqa: E731
    return {
        "accent": _top(accents) or ACCENT,
        "font": _top(fonts) or FONT,
        "ink": ink,
        "muted": muted,
        "placeholder": PLACEHOLDER,
        "bg": bg,
        "surface": _top(surfaces, lambda c: c != bg) or ("#ffffff" if bg != "#ffffff" else SURFACE),
        "border": _top(borders) or BORDER,
        "radius": mid(radii, 16.0),
        "btn_radius": mid(btn_radii, 12.0),
        "input_radius": mid(input_radii, 12.0),
        "_els": elements,
    }


def _saturated(hex6: str) -> bool:
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    return mx - mn > 60 and mx > 60


# ---- Узлы раскладки ----

class _N:
    """Узел раскладки: frame (с автолэйаутом или без), text, rect, ellipse, vector (иконка), clone (копия слоя)."""

    def __init__(self, kind: str, name: str = "") -> None:
        self.kind = kind
        self.name = name
        self.style: dict[str, Any] = {}
        self.text = ""
        self.dir = ""  # v | h | g (только у frame)
        self.gap = 0.0
        self.pad = [0.0, 0.0, 0.0, 0.0]
        self.align = "start"
        self.justify = "start"
        self.cols = 2
        self.sw = ("fixed", 0.0)  # ("fixed", v) | ("fill",) | ("hug",)
        self.sh = ("fixed", 0.0)
        self.kids: list[_N] = []
        self.autow = False
        self.x = self.y = self.w = self.h = 0.0
        self.id = ""
        self.vec: Optional[dict[str, Any]] = None  # vector: {"vb", "paths"}
        self.src = ""  # clone: id исходного слоя
        self.over: dict[str, Any] = {}  # clone: {"texts": {id: текст}, "set": {id: {fill, textColor, …}}}
        self.role = ""  # "button" — в строке по ширине подписи


def _color(raw: Any, theme: dict[str, str], default: str) -> str:
    v = str(raw or "").strip().lower()
    if re.match(r"^#[0-9a-f]{6}$", v):
        return v
    return {"ink": theme["ink"], "muted": theme["muted"], "accent": theme["accent"], "white": "#ffffff",
            "surface": theme["surface"], "bg": theme["bg"], "border": theme["border"]}.get(v, default)


def _num(raw: Any, default: float, lo: float = 0, hi: float = 4000) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _pad(raw: Any, default: list[float]) -> list[float]:
    if isinstance(raw, (int, float)):
        return [float(raw)] * 4
    if isinstance(raw, list) and raw:
        vals = [_num(v, 0, 0, 400) for v in raw[:4]]
        if len(vals) == 2:
            return [vals[0], vals[1], vals[0], vals[1]]
        if len(vals) == 4:
            return vals
        return [vals[0]] * 4
    return list(default)


def _text(text: Any, theme: dict[str, Any], *, size: float = 15, weight: int = 400, color: str = "",
          align: str = "left", name: str = "", hug: bool = False) -> _N:
    n = _N("text", name)
    n.text = str(text or "").strip()[:2000] or " "
    n.style = {"fontSize": size, "textColor": color or theme["ink"], "align": align if align in ("left", "center", "right") else "left",
               "fontFamily": theme["font"], "lineHeight": round(size * 1.35, 1)}
    if weight != 400:
        n.style["fontWeight"] = weight
    n.autow = hug
    n.sw = ("hug",) if hug else ("fill",)
    n.sh = ("hug",)
    return n


def _frame(dir_: str, name: str, *, gap: float = 8, pad: Optional[list[float]] = None, align: str = "start",
           justify: str = "start", fill: str = "none") -> _N:
    n = _N("frame", name)
    n.dir = dir_
    n.gap = gap
    n.pad = list(pad or [0, 0, 0, 0])
    n.align = align
    n.justify = justify
    n.style = {"fill": fill, "radius": 0}
    n.sw = ("fill",)
    n.sh = ("hug",)
    return n


def _icon(name: Any, theme: dict[str, Any], color: str = "", size: float = 24, label: str = "") -> _N:
    """Иконка из набора VK Icons (вектор); нет такой — серый квадрат-заглушка того же размера."""
    from assistant.lib import ui_icons

    vec = ui_icons.vector(name, color or theme["ink"])
    n = _N("vector" if vec else "rect", label or f"Иконка «{str(name or '')[:30]}»")
    if vec:
        n.vec = vec
        n.style = {}
    else:
        n.style = {"fill": "#d1d5db", "radius": 6, "strokeWidth": 0}
    n.sw = n.sh = ("fixed", size)
    return n


def _build(raw: Any, theme: dict[str, Any], depth: int, budget: list[int]) -> Optional[_N]:
    """Компонент модели → узел раскладки (компоненты разворачиваются во фреймы и тексты)."""
    if not isinstance(raw, dict) or depth > MAX_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    t = str(raw.get("type") or "text").strip().lower()
    name = str(raw.get("name") or "").strip()[:120]
    acc = theme["accent"]
    ink, muted, surface, border, ph = theme["ink"], theme["muted"], theme["surface"], theme["border"], theme["placeholder"]

    def kids(items: Any, into: _N) -> _N:
        for c in (items if isinstance(items, list) else [])[:60]:
            k = _build(c, theme, depth + 1, budget)
            if k:
                into.kids.append(k)
        return into

    n: Optional[_N] = None
    if t in ("text", "title", "heading", "paragraph", "label"):
        size = _num(raw.get("size"), 28 if t in ("title", "heading") else 15, 8, 120)
        weight = int(_num(raw.get("weight"), 700 if t in ("title", "heading") else 400, 100, 900) // 100 * 100)
        n = _text(raw.get("text"), theme, size=size, weight=weight, color=_color(raw.get("color"), theme, ink),
                  align=str(raw.get("align") or "left"), name=name or "Текст")
    elif t == "link":
        n = _text(raw.get("text"), theme, size=_num(raw.get("size"), 15, 8, 60), weight=500, color=_color(raw.get("color"), theme, acc),
                  align=str(raw.get("align") or "left"), name=name or "Ссылка")
    elif t == "button":
        variant = str(raw.get("variant") or "primary").lower()
        fill = _color(raw.get("fill"), theme, acc if variant == "primary" else surface if variant == "secondary" else "none")
        if variant == "ghost" and not raw.get("fill"):
            fill = "none"
        n = _frame("h", name or f"Кнопка «{str(raw.get('text') or '')[:40]}»", gap=8, pad=[14, 20, 14, 20], align="center", justify="center", fill=fill)
        n.role = "button"
        n.style["radius"] = _num(raw.get("radius"), theme["btn_radius"], 0, 200)
        if raw.get("icon"):
            n.kids.append(_icon(raw.get("icon"), theme, "#ffffff" if variant == "primary" and fill != "none" else acc, 20))
        color = "#ffffff" if variant == "primary" and fill != "none" else acc if variant == "ghost" else ink
        n.kids.append(_text(raw.get("text") or "Кнопка", theme, size=16, weight=600, color=_color(raw.get("color"), theme, color),
                            align="center", name="Подпись"))
    elif t == "input":
        field = _frame("h", "Поле", gap=8, pad=[14, 16, 14, 16], align="center", fill="#ffffff")
        field.style.update({"radius": theme["input_radius"], "stroke": border, "strokeWidth": 1, "strokeAlign": "inside",
                            "fill": "#ffffff"})
        if raw.get("icon"):
            field.kids.append(_icon(raw.get("icon"), theme, muted, 20))
        field.kids.append(_text(raw.get("placeholder") or raw.get("value") or "", theme, size=15,
                                color=ink if raw.get("value") else ph, name="Подсказка"))
        label = str(raw.get("label") or "").strip()
        if label:
            n = _frame("v", name or f"Поле «{label[:40]}»", gap=6)
            n.kids += [_text(label, theme, size=13, weight=500, color=muted, name="Название поля"), field]
        else:
            field.name = name or "Поле ввода"
            n = field
    elif t == "image":
        n = _N("rect", name or f"Картинка «{str(raw.get('label') or '')[:40]}»")
        n.text = str(raw.get("label") or "Картинка")[:200]
        n.style = {"fill": surface, "radius": _num(raw.get("radius"), 12, 0, 400), "strokeWidth": 0, "fontSize": 13, "textColor": muted,
                   "fontFamily": theme["font"]}
        n.sw = ("fixed", _num(raw.get("w"), 0, 0, 4000)) if raw.get("w") else ("fill",)
        n.sh = ("fixed", _num(raw.get("h"), 160, 16, 2000))
    elif t == "avatar":
        size = _num(raw.get("size"), 48, 16, 400)
        n = _N("ellipse", name or "Аватар")
        n.style = {"fill": "#d1d5db", "strokeWidth": 0}
        n.sw = n.sh = ("fixed", size)
    elif t == "icon":
        n = _icon(raw.get("icon") or raw.get("name"), theme, _color(raw.get("color"), theme, ink), _num(raw.get("size"), 24, 8, 200),
                  name if raw.get("icon") else "")
    elif t == "copy":
        # Копия слоя с другого экрана (нижнее меню, шапка, поле, карточка) — точный стиль макета.
        src = theme["_els"].get(str(raw.get("from") or ""))
        if not src or src.get("type") in ("line", "pen"):
            return None
        n = _N("clone", name or str(src.get("name") or ""))
        n.src = src["id"]
        texts = raw.get("texts") if isinstance(raw.get("texts"), dict) else {}
        n.over = {"texts": {str(k): str(v)[:2000] for k, v in texts.items()},
                  "set": raw.get("set") if isinstance(raw.get("set"), dict) else {},
                  "text": str(raw["text"])[:2000] if raw.get("text") is not None else None}
        n.sw = ("fill",) if "w" in str(src.get("grow") or "") or raw.get("grow") is True else ("fixed", float(src["w"]))
        n.sh = ("fixed", float(src["h"]))
    elif t == "divider":
        n = _N("rect", name or "Разделитель")
        n.style = {"fill": border, "radius": 0, "strokeWidth": 0}
        n.sw = ("fill",)
        n.sh = ("fixed", 1)
    elif t == "spacer":
        n = _frame("v", name or "Отступ", gap=0)
        n.sh = ("fixed", _num(raw.get("h"), 0, 0, 2000)) if raw.get("h") else ("fill",)
    elif t in ("row", "column", "stack", "grid", "card"):
        dir_ = "h" if t == "row" else "g" if t == "grid" else "v"
        card = t == "card"
        n = _frame(dir_, name or {"row": "Ряд", "grid": "Сетка", "card": "Карточка"}.get(t, "Колонка"),
                   gap=_num(raw.get("gap"), 12 if t == "grid" else 8, 0, 400),
                   pad=_pad(raw.get("padding"), [16, 16, 16, 16] if card else [0, 0, 0, 0]),
                   align=str(raw.get("align") or ("center" if t == "row" else "start")),
                   justify=str(raw.get("justify") or "start"),
                   fill=_color(raw.get("fill"), theme, (theme["surface"] if theme["bg"] == "#ffffff" else "#ffffff") if card else "none"))
        if card:
            n.style["radius"] = _num(raw.get("radius"), theme["radius"], 0, 200)
            if n.style.get("fill") in (theme["bg"], "none"):
                # Карточка сливается с фоном — тонкая рамка; иначе (белая на сером) — без рамки, как на макете.
                n.style.update({"stroke": border, "strokeWidth": 1, "strokeAlign": "inside"})
        n.cols = int(_num(raw.get("columns"), 2, 1, 12))
        kids(raw.get("children"), n)
        if t == "row":
            for k in n.kids:  # в строке тексты — по содержимому, если не просят растянуть
                if k.kind == "text":
                    k.sw = ("hug",)
                    k.autow = True
                elif k.role == "button" and k.sw == ("fill",) and not k.over.get("grow"):
                    k.sw = ("hug",)  # кнопка в строке — по ширине подписи
                    for q in k.kids:
                        if q.kind == "text":
                            q.sw, q.autow = ("hug",), True
    elif t == "list":
        n = _frame("v", name or "Список", gap=0)
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        for i, it in enumerate(items[:30]):
            it = it if isinstance(it, dict) else {"title": str(it)}
            row = _frame("h", f"Пункт «{str(it.get('title') or '')[:40]}»", gap=12, pad=[12, 0, 12, 0], align="center")
            if it.get("icon"):
                row.kids.append(_icon(it.get("icon"), theme, acc, 24))
            if it.get("avatar"):
                av = _N("ellipse", "Аватар")
                av.style = {"fill": "#d1d5db", "strokeWidth": 0}
                av.sw = av.sh = ("fixed", 40)
                row.kids.append(av)
            col = _frame("v", "Текст пункта", gap=2)
            col.kids.append(_text(it.get("title") or "", theme, size=16, weight=500, name="Заголовок"))
            if it.get("subtitle"):
                col.kids.append(_text(it.get("subtitle"), theme, size=14, color=muted, name="Подзаголовок"))
            row.kids.append(col)
            if it.get("value"):
                row.kids.append(_text(it.get("value"), theme, size=15, color=muted, hug=True, name="Значение"))
            if it.get("chevron") or it.get("action"):
                row.kids.append(_icon(it.get("action") if isinstance(it.get("action"), str) else "chevron_right", theme, muted, 20))
            n.kids.append(row)
            if i < len(items[:30]) - 1:
                d = _N("rect", "Разделитель")
                d.style = {"fill": border, "radius": 0, "strokeWidth": 0}
                d.sw, d.sh = ("fill",), ("fixed", 1)
                n.kids.append(d)
    elif t == "header":
        n = _frame("h", name or "Шапка", gap=8, pad=[8, 0, 8, 0], align="center", justify="between")
        left = _icon("chevron_left", theme, ink, 24, "Назад") if raw.get("back") else _frame("h", "Слева", gap=0)
        if not raw.get("back"):
            left.sw, left.sh = ("fixed", 24), ("fixed", 24)
        if raw.get("action_icon"):
            right = _icon(raw.get("action_icon"), theme, ink, 24, "Действие")
        elif raw.get("action"):
            right = _text(raw.get("action"), theme, size=16, weight=500, color=acc, hug=True, name="Действие")
        else:
            right = _frame("h", "Справа", gap=0)
            right.sw, right.sh = ("fixed", 24), ("fixed", 24)
        title = _text(raw.get("title") or "", theme, size=17, weight=600, align="center", name="Заголовок")
        n.kids = [left, title, right]
    elif t == "tabbar":
        n = _frame("h", name or "Нижние вкладки", gap=0, pad=[8, 8, 8, 8], align="center", justify="between", fill="#ffffff")
        n.style.update({"stroke": border, "strokeWidth": 1, "strokeAlign": "inside", "radius": 16})
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        active = int(_num(raw.get("active"), 0, 0, 20))
        for i, it in enumerate(items[:6]):
            label = it if isinstance(it, str) else str((it or {}).get("title") or "")
            icon_name = "" if isinstance(it, str) else str((it or {}).get("icon") or "")
            tab = _frame("v", f"Вкладка «{label[:30]}»", gap=4, align="center")
            tab.sw = ("hug",)
            ic = _icon(icon_name or "square_4", theme, acc if i == active else muted, 24)
            tab.kids = [ic, _text(label, theme, size=11, weight=500, color=acc if i == active else muted, hug=True, name="Подпись")]
            n.kids.append(tab)
    if n is None:
        return None
    fill = raw.get("fill")
    if fill and t not in ("button", "card", "text", "link"):
        n.style["fill"] = _color(fill, theme, n.style.get("fill") or "none")
    if raw.get("grow") is True:
        n.sw = ("fill",)
        n.autow = False
        n.over["grow"] = True
    if raw.get("w") and t not in ("image", "avatar", "icon"):
        n.sw = ("fixed", _num(raw.get("w"), 100, 1, 4000))
    if raw.get("h") and t not in ("image", "spacer", "avatar", "icon"):
        n.sh = ("fixed", _num(raw.get("h"), 40, 1, 4000))
    return n


# ---- Раскладка (как applyLayout в webapp/board.js) ----

def _char_w(n: _N) -> float:
    fs = float(n.style.get("fontSize") or 15)
    w = int(n.style.get("fontWeight") or 400)
    return fs * (0.56 if w < 600 else 0.6)


def _text_h(n: _N, width: float) -> float:
    fs = float(n.style.get("fontSize") or 15)
    lh = float(n.style.get("lineHeight") or fs * 1.35)
    cw = _char_w(n)
    lines = 0
    for para in n.text.split("\n"):
        per = max(1, int(max(1.0, width) // cw))
        lines += max(1, -(-len(para) // per))
    return round(lines * lh, 1)


def _intrinsic_w(n: _N) -> float:
    if n.kind == "text":
        longest = max((len(p) for p in n.text.split("\n")), default=1)
        return round(longest * _char_w(n) + 2, 1)
    if n.sw[0] == "fixed":
        return n.sw[1]
    if n.kind != "frame" or not n.kids:
        return n.pad[1] + n.pad[3] if n.kind == "frame" else 40.0
    ws = [_intrinsic_w(k) for k in n.kids]
    if n.dir == "h":
        return sum(ws) + n.gap * (len(ws) - 1) + n.pad[1] + n.pad[3]
    if n.dir == "g":
        return max(ws) * n.cols + n.gap * (n.cols - 1) + n.pad[1] + n.pad[3]
    return max(ws) + n.pad[1] + n.pad[3]


def _width(n: _N, w: float) -> None:
    """Сверху вниз: ширины (fill — от родителя, hug — по содержимому)."""
    n.w = max(1.0, round(w, 1))
    if n.kind != "frame" or not n.kids:
        return
    inner = max(1.0, n.w - n.pad[1] - n.pad[3])
    if n.dir == "v":
        for k in n.kids:
            _width(k, inner if k.sw[0] == "fill" else min(inner, _intrinsic_w(k)) if k.sw[0] == "hug" else k.sw[1])
    elif n.dir == "g":
        cell = max(1.0, (inner - n.gap * (n.cols - 1)) / n.cols)
        for k in n.kids:
            _width(k, cell if k.sw[0] != "fixed" else min(cell, k.sw[1]))
    else:
        fixed = [(_intrinsic_w(k) if k.sw[0] == "hug" else k.sw[1]) for k in n.kids if k.sw[0] != "fill"]
        fills = [k for k in n.kids if k.sw[0] == "fill"]
        free = inner - sum(fixed) - n.gap * (len(n.kids) - 1)
        share = max(1.0, free / len(fills)) if fills else 0
        for k in n.kids:
            if k.sw[0] == "fill":
                _width(k, share)
            else:
                _width(k, min(inner, _intrinsic_w(k)) if k.sw[0] == "hug" else k.sw[1])


def _height(n: _N) -> None:
    """Снизу вверх: высоты (hug — по содержимому; fill в колонке — потом, из свободного места)."""
    for k in n.kids:
        _height(k)
    if n.kind == "text":
        n.h = _text_h(n, n.w)
        return
    if n.sh[0] == "fixed":
        n.h = n.sh[1]
        return
    if n.kind != "frame":
        n.h = n.h or 40.0
        return
    if not n.kids:
        n.h = max(1.0, n.pad[0] + n.pad[2])
        return
    if n.dir == "v":
        hs = [k.h if k.sh[0] != "fill" else 0 for k in n.kids]
        n.h = sum(hs) + n.gap * (len(hs) - 1) + n.pad[0] + n.pad[2]
    elif n.dir == "g":
        rows = [n.kids[i:i + n.cols] for i in range(0, len(n.kids), n.cols)]
        n.h = sum(max(k.h for k in r) for r in rows) + n.gap * (len(rows) - 1) + n.pad[0] + n.pad[2]
    else:
        n.h = max(k.h for k in n.kids) + n.pad[0] + n.pad[2]
    n.h = round(max(1.0, n.h), 1)


def _fill_heights(n: _N) -> None:
    """Колонка фиксированной высоты: «заполняющие» по высоте делят свободное место; в строке — по высоте строки."""
    if n.kind != "frame" or not n.kids:
        return
    inner_h = n.h - n.pad[0] - n.pad[2]
    if n.dir == "v":
        fills = [k for k in n.kids if k.sh[0] == "fill"]
        if fills:
            used = sum(k.h for k in n.kids if k.sh[0] != "fill") + n.gap * (len(n.kids) - 1)
            share = max(0.0, (inner_h - used) / len(fills))
            for k in fills:
                k.h = round(share, 1)
    elif n.dir == "h":
        for k in n.kids:
            if k.sh[0] == "fill":
                k.h = inner_h
    for k in n.kids:
        _fill_heights(k)


def _place(n: _N, x: float, y: float) -> None:
    n.x, n.y = round(x, 1), round(y, 1)
    if n.kind != "frame" or not n.kids:
        return
    pt, pr, pb, pl = n.pad
    iw, ih = n.w - pl - pr, n.h - pt - pb
    if n.dir == "g":
        rows = [n.kids[i:i + n.cols] for i in range(0, len(n.kids), n.cols)]
        cell = (iw - n.gap * (n.cols - 1)) / n.cols
        cy = y + pt
        for r in rows:
            rh = max(k.h for k in r)
            for j, k in enumerate(r):
                _place(k, x + pl + j * (cell + n.gap), cy)
            cy += rh + n.gap
        return
    H = n.dir == "h"
    sizes = [k.w if H else k.h for k in n.kids]
    total = sum(sizes) + n.gap * (len(sizes) - 1)
    avail = iw if H else ih
    gap = n.gap
    start = 0.0
    if n.justify == "center":
        start = (avail - total) / 2
    elif n.justify == "end":
        start = avail - total
    elif n.justify == "between" and len(sizes) > 1:
        gap = max(n.gap, (avail - sum(sizes)) / (len(sizes) - 1))
    pos = start
    for k, s in zip(n.kids, sizes):
        cross = (ih - k.h) if H else (iw - k.w)
        off = cross / 2 if n.align == "center" else cross if n.align == "end" else 0
        if H:
            _place(k, x + pl + pos, y + pt + off)
        else:
            _place(k, x + pl + off, y + pt + pos)
        pos += s + gap


def _layout_str(n: _N) -> str:
    hug = ("w" if n.sw[0] == "hug" else "") + ("h" if n.sh[0] == "hug" else "")
    just = n.justify if n.justify in ("start", "center", "end", "between") else "start"
    align = n.align if n.align in ("start", "center", "end") else "start"
    r = lambda v: f"{round(float(v), 1):g}"  # noqa: E731
    out = f"{n.dir} {r(n.gap)} {r(n.pad[0])} {r(n.pad[1])} {r(n.pad[2])} {r(n.pad[3])} {align} {just} {hug or 'none'}"
    return out + (f" c{n.cols}" if n.dir == "g" else "")


def _new_id() -> str:
    return "lb" + secrets.token_hex(6)


def _emit_clone(n: _N, parent: Optional[_N], z: list[float], out: dict[str, dict[str, Any]], els: dict[str, dict[str, Any]]) -> None:
    """Копия слоя (с содержимым): новые id, сдвиг на место узла, замена текстов и цветов."""
    from assistant.lib import design_spec

    src = els.get(n.src)
    if not src:
        return
    sub = design_spec._subtree(els, src["id"]) if src.get("type") == "frame" else {src["id"]: src}  # noqa: SLF001
    ids = {old: _new_id() for old in sub}
    n.id = ids[src["id"]]
    dx, dy = n.x - float(src["x"]), n.y - float(src["y"])
    texts = n.over.get("texts") or {}
    sets = n.over.get("set") or {}
    first_text = n.over.get("text")
    for old in sorted(sub, key=lambda k: float(sub[k].get("z") or 0)):
        e = json.loads(json.dumps(sub[old]))
        z[0] += 1
        e.update({"id": ids[old], "z": z[0]})
        if e.get("type") == "line":
            # Линия (стрелка в иконке): точки в мировых координатах — сдвинуть; путь клиент пересчитает.
            e["points"] = [[round(p[0] + dx, 2), round(p[1] + dy, 2)] for p in e.get("points") or []]
            if e.get("waypoints"):
                e["waypoints"] = [[round(p[0] + dx, 2), round(p[1] + dy, 2)] for p in e["waypoints"]]
            for k in ("start", "end"):
                end = e.get(k)
                if isinstance(end, dict) and end.get("el"):
                    if end["el"] in ids:
                        end["el"] = ids[end["el"]]
                    else:
                        e.pop(k, None)
            e.pop("d", None)
        e["x"], e["y"] = round(float(e["x"]) + dx, 1), round(float(e["y"]) + dy, 1)
        e.pop("locked", None)
        if old == src["id"]:
            e["w"] = n.w
            if parent is not None:
                e["frame"] = parent.id
                grow = "w" if n.sw[0] == "fill" and parent.dir in ("v", "h", "g") else ""
                if grow:
                    e["grow"] = grow
                else:
                    e.pop("grow", None)
            else:
                e.pop("frame", None)
            if n.name:
                e["name"] = n.name
        elif e.get("frame") in ids:
            e["frame"] = ids[e["frame"]]
        if old in texts and e.get("type") not in ("frame", "image", "vector"):
            e["text"] = texts[old]
        elif first_text is not None and e.get("text") is not None and e.get("type") != "frame":
            e["text"] = first_text
            first_text = None
        ov = sets.get(old) if isinstance(sets.get(old), dict) else {}
        st = dict(e.get("style") or {})
        for k in ("fill", "textColor", "stroke"):
            v = str(ov.get(k) or "").strip().lower()
            if re.match(r"^#[0-9a-f]{6}$", v) or v == "none":
                st[k] = v
                if k != "textColor":
                    st.pop(k + "Off", None)
                    st.pop(k + "Grad", None)
                if e.get("type") == "vector" and k == "fill":
                    e["paths"] = [dict(p, fill=v) if p.get("fill") else p for p in e.get("paths") or []]
                if e.get("type") == "vector" and k == "stroke":
                    e["paths"] = [dict(p, stroke=v) if p.get("stroke") else p for p in e.get("paths") or []]
        if "hidden" in ov:
            if ov["hidden"] is True:
                e["hidden"] = True
            else:
                e.pop("hidden", None)
        if st:
            e["style"] = st
        out[e["id"]] = e


def _emit(n: _N, parent: Optional[_N], z: list[float], out: dict[str, dict[str, Any]],
          els: Optional[dict[str, dict[str, Any]]] = None) -> None:
    if n.kind == "clone":
        _emit_clone(n, parent, z, out, els or {})
        return
    n.id = n.id or _new_id()
    z[0] += 1
    el: dict[str, Any] = {"id": n.id, "type": n.kind, "x": n.x, "y": n.y, "w": n.w, "h": n.h, "z": z[0]}
    if n.name:
        el["name"] = n.name
    if parent is not None:
        el["frame"] = parent.id
        grow = ""
        if parent.dir in ("v", "h", "g"):
            if n.sw[0] == "fill":
                grow += "w"
            if n.sh[0] == "fill":
                grow += "h"
        if grow:
            el["grow"] = grow
    st = dict(n.style)
    if n.kind == "frame":
        el["layout"] = _layout_str(n)
        if st.get("fill") == "none":
            st["fill"] = "none"
        st.setdefault("strokeWidth", 0)
    if n.kind == "text":
        el["text"] = n.text
        if n.autow:
            el["autoW"] = True
    elif n.text:
        el["text"] = n.text
    if n.kind == "vector" and n.vec:
        el.update(n.vec)
        if parent is not None:
            el["ratio"] = True
    el["style"] = st
    out[n.id] = el
    for k in n.kids:
        _emit(k, n, z, out, els)


# ---- Применение к макету ----

def _top_frames(elements: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in elements.values() if e.get("type") == "frame" and not (e.get("frame") and e["frame"] in elements)]


def add_screens(elements: dict[str, dict[str, Any]], screens: Any) -> list[str]:
    """Новые экраны справа от существующих; вернуть id новых экранов."""
    theme = style_of(elements)
    tops = _top_frames(elements)
    x0 = max([e["x"] + e["w"] for e in tops] + [e["x"] + e["w"] for e in elements.values() if e.get("type") != "line"] + [-100]) + 100
    y0 = min([e["y"] for e in tops] or [0])
    z = [max([float(e.get("z") or 0) for e in elements.values()] + [0])]
    budget = [MAX_NODES]
    made: list[str] = []
    frames: list[str] = []
    for raw in (screens if isinstance(screens, list) else [])[:6]:
        if not isinstance(raw, dict):
            continue
        sw, sh = DEVICES.get(str(raw.get("device") or "iphone").lower(), DEVICES["iphone"])
        if raw.get("w"):
            sw = _num(raw.get("w"), sw, 120, 4000)
        if raw.get("h"):
            sh = _num(raw.get("h"), sh, 120, 8000)
        local = dict(theme)
        if raw.get("accent"):
            local["accent"] = _color(raw.get("accent"), theme, theme["accent"])
        scr = _frame("v", str(raw.get("name") or "Экран")[:120], gap=_num(raw.get("gap"), 16, 0, 400),
                     pad=_pad(raw.get("padding"), [56, 24, 32, 24]), fill=_color(raw.get("fill"), local, local["bg"]))
        scr.sw, scr.sh = ("fixed", sw), ("fixed", sh)
        for c in (raw.get("children") if isinstance(raw.get("children"), list) else [])[:80]:
            k = _build(c, local, 1, budget)
            if k:
                scr.kids.append(k)
        _width(scr, sw)
        _height(scr)
        # Содержимое длиннее экрана — экран вытягивается (прокрутка), а не обрезает.
        content = sum(k.h for k in scr.kids if k.sh[0] != "fill") + scr.gap * max(0, len(scr.kids) - 1) + scr.pad[0] + scr.pad[2]
        scr.h = max(sh, round(content, 1))
        _fill_heights(scr)
        _place(scr, x0, y0)
        out: dict[str, dict[str, Any]] = {}
        _emit(scr, None, z, out, elements)
        out[scr.id]["device"] = str(raw.get("device") or "iphone").lower() if scr.h == sh else ""
        if not out[scr.id]["device"]:
            out[scr.id].pop("device")
        elements.update(out)
        made.append(scr.id)
        # Все новые фреймы с автолэйаутом (и копии) — клиент пересоберёт их по реальным текстам.
        frames += [k for k, e in out.items() if e.get("type") == "frame" and e.get("layout") and k != scr.id]
        x0 += scr.w + 100
    return made + frames


def add_into(elements: dict[str, dict[str, Any]], adds: Any) -> list[str]:
    """Дорисовать компоненты в существующий фрейм (за слоем after или в конец); раскладку уточнит клиент."""
    theme = style_of(elements)
    z = [max([float(e.get("z") or 0) for e in elements.values()] + [0])]
    budget = [MAX_NODES]
    touched: list[str] = []
    for raw in (adds if isinstance(adds, list) else [])[:10]:
        if not isinstance(raw, dict):
            continue
        parent = elements.get(str(raw.get("into") or ""))
        if not parent or parent.get("type") != "frame":
            continue
        pdir = str(parent.get("layout") or "").split(" ")[0] or ""
        pad = [float(v) for v in (str(parent.get("layout") or "").split(" ")[2:6] or [0, 0, 0, 0])] if pdir else [16.0] * 4
        kids = [e for e in elements.values() if e.get("frame") == parent["id"] and e.get("type") not in ("line", "pen")]
        after = elements.get(str(raw.get("after") or ""))
        if after is not None and after.get("frame") != parent["id"]:
            after = None
        inner = max(40.0, float(parent["w"]) - pad[1] - pad[3])
        if after is not None:
            cy = after["y"] + after["h"] / 2 + 0.1  # центр чуть ниже «after» — порядок в автолэйауте сразу за ним
            cx = after["x"] + after["w"] / 2 + 0.1
        else:
            bottom = max([e["y"] + e["h"] for e in kids] + [parent["y"] + pad[0]])
            right = max([e["x"] + e["w"] for e in kids] + [parent["x"] + pad[3]])
            cy, cx = bottom + 8, right + 8
        for c in (raw.get("children") if isinstance(raw.get("children"), list) else [])[:40]:
            n = _build(c, theme, 1, budget)
            if not n:
                continue
            _width(n, inner if n.sw[0] == "fill" or pdir == "v" else min(inner, _intrinsic_w(n)) if n.sw[0] == "hug" else n.sw[1])
            _height(n)
            _fill_heights(n)
            if pdir == "h":
                _place(n, cx, parent["y"] + pad[0])
                cx += n.w + 1
            else:
                _place(n, parent["x"] + pad[3], cy - n.h / 2 if after is not None else cy)
                cy += n.h + 1
            fake = _N("frame")
            fake.id, fake.dir = parent["id"], pdir if pdir in ("v", "h", "g") else ""
            out: dict[str, dict[str, Any]] = {}
            _emit(n, fake, z, out, elements)
            elements.update(out)
            touched.append(n.id)
        touched.append(parent["id"])
    return touched


def apply_draw(body: Any, action: dict[str, Any]) -> tuple[str, list[str]]:
    """Экраны и дорисовка → (новое тело макета, id для пересборки автолэйаута на клиенте)."""
    board = board_doc.parse_board(body)
    elements = board["elements"]
    ids = add_screens(elements, action.get("screens")) + add_into(elements, action.get("add"))
    out = {"v": 1, "mode": "design", "elements": elements}
    return board_doc.normalize_board_body(json.dumps(out, ensure_ascii=False)), ids
