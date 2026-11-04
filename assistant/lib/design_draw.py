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
    return """
## Рисовать экраны на макете
Новый экран (или несколько) — ключ screens в том же блоке ```leo-board (action=update, sheet_id макета).
Координат нет: дерево компонентов, раскладку автолэйаутом и размеры считает Лео.
```leo-board
{"action":"update","sheet_id":"123","screens":[{"name":"Вход","device":"iphone","padding":[56,24,32,24],"gap":16,
 "children":[
  {"type":"text","text":"Вход","size":28,"weight":700},
  {"type":"text","text":"Введите e-mail и пароль","color":"muted"},
  {"type":"input","label":"E-mail","placeholder":"name@mail.ru"},
  {"type":"input","label":"Пароль","placeholder":"••••••••"},
  {"type":"link","text":"Забыли пароль?","align":"right"},
  {"type":"spacer"},
  {"type":"button","text":"Войти"},
  {"type":"row","justify":"center","children":[{"type":"text","text":"Нет аккаунта?","color":"muted"},{"type":"link","text":"Регистрация"}]}]}]}
```
Компоненты (type):
- text {text, size 15, weight 400|500|600|700, color: "ink"|"muted"|"accent"|#rrggbb, align left|center|right};
  link {text} — текст-ссылка цветом акцента;
- button {text, variant: primary|secondary|ghost}; input {label?, placeholder}; image {label, h} — заглушка картинки;
  avatar {size 48}; icon {name} — квадрат 24 под иконку; divider; spacer — забирает свободное место по высоте;
- row {children, gap 8, justify start|center|end|between, align center} — в строку; column {children, gap 8};
  grid {children, columns 2, gap 12}; card {children, gap 8, padding 16} — карточка;
- list {items:[{title, subtitle?, avatar?:true}]}; header {title, back?:true, action?:"Готово"} — шапка экрана;
  tabbar {items:["Главная","Поиск","Профиль"], active 0} — нижние вкладки.
- У любого: name — имя слоя (по смыслу, по-русски), fill — заливка #rrggbb, grow:true — растянуть в строке.
Экран: name, device iphone|android|ipad|laptop|desktop (по умолчанию iphone), fill, padding [верх, право, низ, лево],
gap, children. Цвета и шрифт Лео возьмёт из уже нарисованных экранов (акцент — accent экрана или самый частый цвет кнопок).
Дорисовать в существующий фрейм: "add":[{"into":"id_фрейма","after":"id_слоя (необязательно)","children":[…]}].
Пиши реальные тексты по смыслу заметки, без «Lorem ipsum». Один экран — до ~40 компонентов.
""".strip()


# ---- Стиль макета: акцент, шрифт ----

def style_of(elements: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Акцент и шрифт уже нарисованных экранов (самые частые), иначе — по умолчанию."""
    fills: dict[str, int] = {}
    fonts: dict[str, int] = {}
    for el in elements.values():
        st = el.get("style") or {}
        f = str(st.get("fill") or "").lower()
        if re.match(r"^#[0-9a-f]{6}$", f) and _saturated(f) and el.get("type") in ("frame", "rect"):
            fills[f] = fills.get(f, 0) + 1
        if st.get("fontFamily"):
            fonts[str(st["fontFamily"])] = fonts.get(str(st["fontFamily"]), 0) + 1
    accent = max(fills, key=fills.get) if fills else ACCENT
    font = max(fonts, key=fonts.get) if fonts else FONT
    return {"accent": accent, "font": font}


def _saturated(hex6: str) -> bool:
    r, g, b = (int(hex6[i:i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    return mx - mn > 60 and mx > 60


# ---- Узлы раскладки ----

class _N:
    """Узел раскладки: frame (с автолэйаутом или без), text, rect, ellipse."""

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


def _color(raw: Any, theme: dict[str, str], default: str) -> str:
    v = str(raw or "").strip().lower()
    if re.match(r"^#[0-9a-f]{6}$", v):
        return v
    return {"ink": INK, "muted": MUTED, "accent": theme["accent"], "white": "#ffffff", "surface": SURFACE}.get(v, default)


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


def _text(text: Any, theme: dict[str, str], *, size: float = 15, weight: int = 400, color: str = INK,
          align: str = "left", name: str = "", hug: bool = False) -> _N:
    n = _N("text", name)
    n.text = str(text or "").strip()[:2000] or " "
    n.style = {"fontSize": size, "textColor": color, "align": align if align in ("left", "center", "right") else "left",
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


def _build(raw: Any, theme: dict[str, str], depth: int, budget: list[int]) -> Optional[_N]:
    """Компонент модели → узел раскладки (компоненты разворачиваются во фреймы и тексты)."""
    if not isinstance(raw, dict) or depth > MAX_DEPTH or budget[0] <= 0:
        return None
    budget[0] -= 1
    t = str(raw.get("type") or "text").strip().lower()
    name = str(raw.get("name") or "").strip()[:120]
    acc = theme["accent"]

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
        n = _text(raw.get("text"), theme, size=size, weight=weight, color=_color(raw.get("color"), theme, INK),
                  align=str(raw.get("align") or "left"), name=name or "Текст")
    elif t == "link":
        n = _text(raw.get("text"), theme, size=_num(raw.get("size"), 15, 8, 60), weight=500, color=_color(raw.get("color"), theme, acc),
                  align=str(raw.get("align") or "left"), name=name or "Ссылка")
    elif t == "button":
        variant = str(raw.get("variant") or "primary").lower()
        fill = _color(raw.get("fill"), theme, acc if variant == "primary" else SURFACE if variant == "secondary" else "none")
        if variant == "ghost" and not raw.get("fill"):
            fill = "none"
        n = _frame("h", name or f"Кнопка «{str(raw.get('text') or '')[:40]}»", gap=8, pad=[14, 20, 14, 20], align="center", justify="center", fill=fill)
        n.style["radius"] = _num(raw.get("radius"), 12, 0, 200)
        color = "#ffffff" if variant == "primary" and fill != "none" else acc if variant == "ghost" else INK
        n.kids.append(_text(raw.get("text") or "Кнопка", theme, size=16, weight=600, color=_color(raw.get("color"), theme, color),
                            align="center", name="Подпись"))
    elif t == "input":
        field = _frame("h", "Поле", gap=8, pad=[14, 16, 14, 16], align="center", fill="#ffffff")
        field.style.update({"radius": 10, "stroke": "#d0d5dd", "strokeWidth": 1, "strokeAlign": "inside"})
        field.kids.append(_text(raw.get("placeholder") or raw.get("value") or "", theme, size=15,
                                color=INK if raw.get("value") else PLACEHOLDER, name="Подсказка"))
        label = str(raw.get("label") or "").strip()
        if label:
            n = _frame("v", name or f"Поле «{label[:40]}»", gap=6)
            n.kids += [_text(label, theme, size=13, weight=500, color=MUTED, name="Название поля"), field]
        else:
            field.name = name or "Поле ввода"
            n = field
    elif t == "image":
        n = _N("rect", name or f"Картинка «{str(raw.get('label') or '')[:40]}»")
        n.text = str(raw.get("label") or "Картинка")[:200]
        n.style = {"fill": SURFACE, "radius": _num(raw.get("radius"), 12, 0, 400), "strokeWidth": 0, "fontSize": 13, "textColor": MUTED,
                   "fontFamily": theme["font"]}
        n.sw = ("fixed", _num(raw.get("w"), 0, 0, 4000)) if raw.get("w") else ("fill",)
        n.sh = ("fixed", _num(raw.get("h"), 160, 16, 2000))
    elif t == "avatar":
        size = _num(raw.get("size"), 48, 16, 400)
        n = _N("ellipse", name or "Аватар")
        n.style = {"fill": "#d1d5db", "strokeWidth": 0}
        n.sw = n.sh = ("fixed", size)
    elif t == "icon":
        size = _num(raw.get("size"), 24, 8, 200)
        n = _N("rect", name or f"Иконка «{str(raw.get('name') or raw.get('label') or '')[:30]}»")
        n.style = {"fill": "#d1d5db", "radius": 6, "strokeWidth": 0}
        n.sw = n.sh = ("fixed", size)
    elif t == "divider":
        n = _N("rect", name or "Разделитель")
        n.style = {"fill": BORDER, "radius": 0, "strokeWidth": 0}
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
                   fill=_color(raw.get("fill"), theme, "#ffffff" if card else "none"))
        if card:
            n.style.update({"radius": _num(raw.get("radius"), 16, 0, 200), "stroke": BORDER, "strokeWidth": 1, "strokeAlign": "inside"})
        n.cols = int(_num(raw.get("columns"), 2, 1, 12))
        kids(raw.get("children"), n)
        if t == "row":
            for k in n.kids:  # в строке тексты — по содержимому, если не просят растянуть
                if k.kind == "text":
                    k.sw = ("hug",)
                    k.autow = True
    elif t == "list":
        n = _frame("v", name or "Список", gap=0)
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        for i, it in enumerate(items[:30]):
            it = it if isinstance(it, dict) else {"title": str(it)}
            row = _frame("h", f"Пункт «{str(it.get('title') or '')[:40]}»", gap=12, pad=[12, 0, 12, 0], align="center")
            if it.get("avatar"):
                av = _N("ellipse", "Аватар")
                av.style = {"fill": "#d1d5db", "strokeWidth": 0}
                av.sw = av.sh = ("fixed", 40)
                row.kids.append(av)
            col = _frame("v", "Текст пункта", gap=2)
            col.kids.append(_text(it.get("title") or "", theme, size=16, weight=500, name="Заголовок"))
            if it.get("subtitle"):
                col.kids.append(_text(it.get("subtitle"), theme, size=14, color=MUTED, name="Подзаголовок"))
            row.kids.append(col)
            if it.get("value"):
                row.kids.append(_text(it.get("value"), theme, size=15, color=MUTED, hug=True, name="Значение"))
            n.kids.append(row)
            if i < len(items[:30]) - 1:
                d = _N("rect", "Разделитель")
                d.style = {"fill": BORDER, "radius": 0, "strokeWidth": 0}
                d.sw, d.sh = ("fill",), ("fixed", 1)
                n.kids.append(d)
    elif t == "header":
        n = _frame("h", name or "Шапка", gap=8, pad=[8, 0, 8, 0], align="center", justify="between")
        left = _text("‹" if raw.get("back") else "", theme, size=24, weight=400, color=acc, hug=True, name="Назад")
        right = _text(raw.get("action") or "", theme, size=16, weight=500, color=acc, hug=True, name="Действие")
        title = _text(raw.get("title") or "", theme, size=17, weight=600, align="center", name="Заголовок")
        n.kids = [left, title, right]
    elif t == "tabbar":
        n = _frame("h", name or "Нижние вкладки", gap=0, pad=[8, 8, 8, 8], align="center", justify="between", fill="#ffffff")
        n.style.update({"stroke": BORDER, "strokeWidth": 1, "strokeAlign": "inside", "radius": 16})
        items = raw.get("items") if isinstance(raw.get("items"), list) else []
        active = int(_num(raw.get("active"), 0, 0, 20))
        for i, it in enumerate(items[:6]):
            label = it if isinstance(it, str) else str((it or {}).get("title") or "")
            tab = _frame("v", f"Вкладка «{label[:30]}»", gap=4, align="center")
            tab.sw = ("hug",)
            ic = _N("rect", "Иконка")
            ic.style = {"fill": acc if i == active else "#d1d5db", "radius": 6, "strokeWidth": 0}
            ic.sw = ic.sh = ("fixed", 24)
            tab.kids = [ic, _text(label, theme, size=11, weight=500, color=acc if i == active else MUTED, hug=True, name="Подпись")]
            n.kids.append(tab)
    if n is None:
        return None
    fill = raw.get("fill")
    if fill and t not in ("button", "card", "text", "link"):
        n.style["fill"] = _color(fill, theme, n.style.get("fill") or "none")
    if raw.get("grow") is True:
        n.sw = ("fill",)
        n.autow = False
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


def _emit(n: _N, parent: Optional[_N], z: list[float], out: dict[str, dict[str, Any]]) -> None:
    n.id = n.id or "lb" + secrets.token_hex(6)
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
    el["style"] = st
    out[n.id] = el
    for k in n.kids:
        _emit(k, n, z, out)


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
                     pad=_pad(raw.get("padding"), [56, 24, 32, 24]), fill=_color(raw.get("fill"), local, "#ffffff"))
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
        _emit(scr, None, z, out)
        out[scr.id]["device"] = str(raw.get("device") or "iphone").lower() if scr.h == sh else ""
        if not out[scr.id]["device"]:
            out[scr.id].pop("device")
        elements.update(out)
        made.append(scr.id)
        x0 += scr.w + 100
    return made


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
            _emit(n, fake, z, out)
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
