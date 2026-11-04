"""Холст (лист kind=board): JSON-документ доски — блоки, фигуры, линии и стрелки.

Тело листа — ``{"v": 1, "elements": {id: {...}}}``. Здесь: проверка тела перед
сохранением, текстовая замена (поиск, ИИ, экспорт) и простой SVG для публичной
страницы. Рисует и правит доску клиент — webapp/board.js.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
from html.parser import HTMLParser
from typing import Any, Optional

BOARD_VERSION = 1
MAX_BOARD_BYTES = 12 * 1024 * 1024
MAX_ELEMENTS = 5000
MAX_TEXT = 20000
FLOW_SHAPES = ("pill", "cylinder", "triangle", "triangleDown", "parallelogram", "parallelogramL")
ELEMENT_TYPES = ("text", "rect", "diamond", "ellipse", *FLOW_SHAPES, "sticky", "line", "pen", "image", "table", "card", "gantt", "vector", "frame")
SHAPE_TYPES = ("text", "rect", "diamond", "ellipse", *FLOW_SHAPES, "sticky", "image", "table", "card", "gantt", "vector", "frame")
# Режим листа-доски: «board» — доска, «design» — макет экранов (те же элементы, другие инструменты).
BOARD_MODES = ("board", "design")
MAX_VECTOR_PATHS = 200
MAX_TABLE_HTML = 400_000
MAX_GANTT_TASKS = 200
GANTT_UNITS = ("day", "week")
PALETTE = ("ink", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink", "white",
           "lime", "teal", "cyan", "indigo", "brown")
_IMAGE_PREFIXES = ("data:image/png", "data:image/jpeg", "data:image/jpg", "data:image/webp", "data:image/gif")
_PATH_RE = re.compile(r"^[MLCQZmlcqz0-9.,\s-]{1,20000}$")
_STYLE_KEYS = ("fill", "stroke", "strokeWidth", "dash", "fontSize", "align", "textColor", "bold", "radius", "highlight",
               "opacity", "shadow", "radii", "fillOpacity", "strokeOpacity", "fillOff", "strokeOff", "strokeAlign",
               "fillGrad", "strokeGrad", "blur", "bgBlur",
               "fontFamily", "fontWeight", "lineHeight", "letterSpacing", "valign", "deco", "textCase")
_LAYOUT_RE = re.compile(r"^(h|v|g)( \d{1,5}(\.\d)?){5} (start|center|end) (start|center|end|between) (none|w|h|wh)( (wrap|c\d{1,2}))?$")
# Градиент: «linear|radial угол #rrggbbaa@позиция …» (2–8 точек).
_GRAD_RE = re.compile(r"^(linear|radial) -?\d{1,3}( #[0-9a-fA-F]{8}@\d{1,3}){2,8}$")
# Скругления углов по отдельности: «левый-верхний правый-верхний правый-нижний левый-нижний».
_RADII_RE = re.compile(r"^\d{1,5}(\.\d{1,2})?( \d{1,5}(\.\d{1,2})?){3}$")
# Тень (макет): «x y размытие #rrggbbaa».
_SHADOW_RE = re.compile(r"^-?\d{1,3} -?\d{1,3} \d{1,3} #[0-9a-fA-F]{8}$")
_COLOR_KEYS = ("fill", "stroke", "textColor", "highlight")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class BoardError(ValueError):
    pass


def empty_board(mode: str = "board") -> str:
    data: dict[str, Any] = {"v": BOARD_VERSION, "elements": {}}
    if mode == "design":
        data["mode"] = "design"
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


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


# ---- таблица: разметка таблицы заметок (webapp/editor/table-manage.mjs), только белый список ----

_TABLE_TAGS = {
    "table", "colgroup", "col", "tbody", "thead", "tr", "td", "th", "p", "br", "strong", "b", "em", "i",
    "u", "s", "span", "mark", "a", "code",
}
_VOID_TAGS = {"col", "br"}
_STYLE_OK = re.compile(
    r"^\s*(width|min-width|height|text-align|vertical-align)\s*:\s*[a-z0-9.%\s-]{1,24}\s*$", re.IGNORECASE
)
_ATTRS_OK = {
    "colspan", "rowspan", "colwidth", "data-row-height", "data-highlight", "data-color", "data-leo-color",
    "class", "style", "href",
}


def _clean_style(raw: str) -> str:
    parts = [p.strip() for p in str(raw or "").split(";") if p.strip()]
    return "; ".join(p for p in parts if _STYLE_OK.match(p))


class _TableCleaner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.tables = 0
        self.skip = 0  # внутри <script> / <style> — текст выбрасываем

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self.skip += 1
            return
        if tag not in _TABLE_TAGS:
            return
        if tag == "table":
            if self.tables:
                return  # вложенные таблицы не нужны
            self.tables += 1
        kept: list[str] = []
        for k, v in attrs:
            k = (k or "").lower()
            if k not in _ATTRS_OK or v is None:
                continue
            if k == "href":
                if tag != "a" or not re.match(r"^(https?:|mailto:)", v.strip(), re.IGNORECASE):
                    continue
            elif k == "style":
                v = _clean_style(v)
                if not v:
                    continue
            elif k in ("colspan", "rowspan", "data-row-height"):
                if not re.match(r"^\d{1,4}$", v.strip()):
                    continue
            elif k == "colwidth":
                if not re.match(r"^[\d,\s]{1,200}$", v):
                    continue
            else:
                v = v[:64]
            kept.append(f' {k}="{_attr(v)}"')
        self.out.append(f"<{tag}{''.join(kept)}>")
        if tag not in _VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if tag not in self.stack:
            return
        while self.stack:
            top = self.stack.pop()
            self.out.append(f"</{top}>")
            if top == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.stack and not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean_table_html(raw: Any) -> str:
    """HTML таблицы доски: только таблица и простые отметки текста, без скриптов и чужих атрибутов."""
    src = str(raw or "")[:MAX_TABLE_HTML]
    if "<table" not in src.lower():
        return ""
    p = _TableCleaner()
    try:
        p.feed(src)
        p.close()
    except Exception:
        return ""
    while p.stack:
        p.out.append(f"</{p.stack.pop()}>")
    out = "".join(p.out)
    return out if out.startswith("<table") or "<table" in out else ""


class _TableText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.cell: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = []
        elif tag in ("br", "p") and self.cell is not None and self.cell:
            self.cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None:
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(" ".join("".join(self.cell).split()))
            self.cell = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def table_rows(raw_html: Any) -> list[list[str]]:
    """Текст ячеек таблицы по строкам."""
    p = _TableText()
    try:
        p.feed(str(raw_html or ""))
        p.close()
    except Exception:
        return []
    return [r for r in p.rows if r]


def table_html_from_rows(rows: list[list[Any]], header: bool = True) -> str:
    """Таблица из строк текста — в той же разметке, что в заметках (первая строка — шапка)."""
    clean = [[_str(c, 2000) for c in (r or [])] for r in rows if isinstance(r, (list, tuple))]
    clean = [r for r in clean if r][:100]
    if not clean:
        clean = [["", "", ""], ["", "", ""], ["", "", ""]]
    cols = min(30, max(len(r) for r in clean))
    out = ['<table class="note-editor-table"><tbody>']
    for i, r in enumerate(clean):
        tag = "th" if header and i == 0 else "td"
        cells = (r + [""] * cols)[:cols]
        out.append("<tr>" + "".join(
            f'<{tag} colspan="1" rowspan="1"><p>{html.escape(c, quote=False)}</p></{tag}>' for c in cells
        ) + "</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _date(raw: Any) -> Optional[str]:
    s = str(raw or "").strip()[:10]
    try:
        return dt.date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _color(raw: Any, default: str = "") -> str:
    v = str(raw or "").strip().lower()
    return v if v in PALETTE else default


def _gantt_tasks(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for i, t in enumerate(raw[:MAX_GANTT_TASKS]):
        if not isinstance(t, dict):
            continue
        start = _date(t.get("start"))
        end = _date(t.get("end")) or start
        if not start:
            continue
        if end < start:
            start, end = end, start
        task: dict[str, Any] = {"id": _str(t.get("id") or f"t{i + 1}", 32), "name": _str(t.get("name"), 300), "start": start, "end": end}
        owner = _str(t.get("owner"), 120).strip()
        if owner:
            task["owner"] = owner
        color = _color(t.get("color"))
        if color:
            task["color"] = color
        out.append(task)
    return out


def _paint(v: Any) -> Optional[str]:
    """Цвет контура вектора: #rrggbb, ключ палитры или нет цвета."""
    s = str(v or "").strip().lower()
    if not s or s == "none":
        return None
    return s if _HEX_RE.match(s) or s in PALETTE else None


def _clean_vector(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Вектор (иконка / компонент из FigJam): контуры в своей системе координат vb = [w, h]."""
    vb = raw.get("vb")
    if not (isinstance(vb, (list, tuple)) and len(vb) == 2):
        return None
    vw, vh = _num(vb[0], 0, 0.01, 1e5), _num(vb[1], 0, 0.01, 1e5)
    paths = []
    for p in (raw.get("paths") or [])[:MAX_VECTOR_PATHS]:
        if not isinstance(p, dict):
            continue
        d = str(p.get("d") or "")
        if not d or not _PATH_RE.match(d):
            continue
        out: dict[str, Any] = {"d": d}
        fill, stroke = _paint(p.get("fill")), _paint(p.get("stroke"))
        if fill:
            out["fill"] = fill
        if stroke:
            out["stroke"] = stroke
            out["sw"] = _num(p.get("sw"), 1, 0, 1000)
            if p.get("cap") in ("round", "square"):
                out["cap"] = p["cap"]
            if p.get("join") in ("round", "bevel"):
                out["join"] = p["join"]
        if p.get("rule") == "evenodd":
            out["rule"] = "evenodd"
        if fill or stroke:
            paths.append(out)
    if not paths:
        return None
    return {"vb": [vw, vh], "paths": paths}


def _fmt_day(iso: str) -> str:
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.day:02d}.{d.month:02d}"


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
    # Экран, в котором лежит элемент (макет); у экрана — внешний экран. Стрелка — только свободная:
    # привязанная (переход между экранами) ходит за фигурами.
    tied = etype == "line" and any(isinstance(raw.get(k), dict) and raw[k].get("el") for k in ("start", "end"))
    if raw.get("frame") and raw.get("frame") != eid and not tied:
        el["frame"] = _str(raw.get("frame"))
    # Ребёнок автолэйаута: «заполнить» по ширине/высоте; замок пропорций.
    if raw.get("grow") in ("w", "h", "wh"):
        el["grow"] = raw["grow"]
    if raw.get("ratio") is True:
        el["ratio"] = True
    # Слои: скрыт с доски / заблокирован от правки.
    for k in ("hidden", "locked"):
        if raw.get(k) is True:
            el[k] = True
    if raw.get("autoW") is True and etype == "text":
        el["autoW"] = True  # текст «ширина по содержимому» — без переноса строк
    for k in ("minW", "maxW", "minH", "maxH"):
        v = _num(raw.get(k), 0, 0, 1e5)
        if v > 0:
            el[k] = v
    if etype != "frame" and raw.get("name"):
        el["name"] = _str(raw.get("name"), 120).strip()
    if raw.get("text"):
        el["text"] = _str(raw.get("text"), MAX_TEXT)
    style = raw.get("style")
    if isinstance(style, dict):
        st: dict[str, Any] = {}
        for k in _STYLE_KEYS:
            v = style.get(k)
            if v is None:
                continue
            if k in ("opacity", "fillOpacity", "strokeOpacity"):
                st[k] = _num(v, 1, 0, 1)
                continue
            if k in ("fillOff", "strokeOff"):
                if v is True:
                    st[k] = True
                continue
            if k == "fontFamily":
                if isinstance(v, str) and re.match(r"^[A-Za-z0-9 ]{1,40}$", v.strip()):
                    st[k] = v.strip()
                continue
            if k == "fontWeight":
                w = int(round(_num(v, 400, 100, 900) / 100) * 100)
                if w != 400:
                    st[k] = w
                continue
            if k in ("lineHeight", "letterSpacing"):
                n = _num(v, 0, 0 if k == "lineHeight" else -50, 1000 if k == "lineHeight" else 200)
                if n:
                    st[k] = n
                continue
            if k in ("valign", "deco", "textCase"):
                ok = {"valign": ("top", "bottom"), "deco": ("underline", "strike"), "textCase": ("upper", "lower", "title")}[k]
                if v in ok:
                    st[k] = v
                continue
            if k in ("fillGrad", "strokeGrad"):
                if isinstance(v, str) and _GRAD_RE.match(v.strip()):
                    st[k] = v.strip().lower()
                continue
            if k in ("blur", "bgBlur"):
                n = _num(v, 0, 0, 250)
                if n > 0:
                    st[k] = n
                continue
            if k == "strokeAlign":
                if v in ("inside", "outside"):
                    st[k] = v
                continue
            if k == "radii":
                if isinstance(v, str) and _RADII_RE.match(v.strip()):
                    st[k] = v.strip()
                continue
            if k == "shadow":
                if isinstance(v, str) and _SHADOW_RE.match(v.strip()):
                    st[k] = v.strip().lower()
                continue
            if k in _COLOR_KEYS:
                # Цвет — ключ палитры или точный #rrggbb (вставка из Miro / FigJam); остальное — в атрибуты не пускаем.
                v = str(v).strip()
                if not (_HEX_RE.match(v) or re.match(r"^[a-z]{2,10}$", v)):
                    continue
                st[k] = v.lower()
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
    elif etype == "table":
        el["html"] = clean_table_html(raw.get("html")) or table_html_from_rows([])
    elif etype == "card":
        for k, lim in (("title", 200), ("name", 200)):
            v = _str(raw.get(k), lim)
            if v:
                el[k] = v
        photo = str(raw.get("photo") or "")
        if photo and photo.lower().startswith(_IMAGE_PREFIXES):
            el["photo"] = photo
    elif etype == "frame":
        name = _str(raw.get("name"), 120).strip()
        if name:
            el["name"] = name
        device = _str(raw.get("device"), 24).strip()
        if device:
            el["device"] = device
        # Автолэйаут: «h|v промежуток отступы×4 выравнивание распределение обнимать».
        layout = str(raw.get("layout") or "").strip()
        if _LAYOUT_RE.match(layout):
            el["layout"] = layout
        if raw.get("noclip") is True:
            el["noclip"] = True  # содержимое не обрезается по краю фрейма
    elif etype == "vector":
        vec = _clean_vector(raw)
        if not vec:
            return None
        el.update(vec)
    elif etype == "gantt":
        el["start"] = _date(raw.get("start")) or dt.date.today().isoformat()
        el["unit"] = raw.get("unit") if raw.get("unit") in GANTT_UNITS else "day"
        el["span"] = int(_num(raw.get("span"), 14, 1, 520))
        el["tasks"] = _gantt_tasks(raw.get("tasks"))
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
    board: dict[str, Any] = {"v": BOARD_VERSION, "elements": out}
    if data.get("mode") == "design":
        board["mode"] = "design"
    return board


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


def _el_text(el: dict[str, Any]) -> str:
    """Текст элемента: у таблицы — строки «a | b», у карточки — «Должность — Имя: обязанности», у Ганта — задачи."""
    t = el.get("type")
    if t == "table":
        return "\n".join(" | ".join(r) for r in table_rows(el.get("html")))
    if t == "card":
        head = " — ".join(x for x in (_one_line(el.get("title") or ""), _one_line(el.get("name") or "")) if x)
        about = _one_line(el.get("text") or "")
        return f"{head}: {about}" if head and about else (head or about)
    if t == "frame":
        return str(el.get("name") or "")
    if t == "gantt":
        rows = []
        for task in el.get("tasks") or []:
            who = f" ({task['owner']})" if task.get("owner") else ""
            rows.append(f"{task.get('name') or 'Задача'}{who}: {_fmt_day(task['start'])}–{_fmt_day(task['end'])}")
        return "\n".join(rows)
    return str(el.get("text") or "")


def board_plain_text(body: Any) -> str:
    """Текст холста: подписи блоков по порядку и связи «А → Б» — для поиска и ИИ."""
    elements = parse_board(body)["elements"]
    lines: list[str] = []
    for el in _sorted_shapes(elements):
        text = _el_text(el).strip()
        if text:
            lines.append(text)
    links: list[str] = []
    for el in elements.values():
        if el.get("type") != "line":
            continue
        a = _el_text(elements.get((el.get("start") or {}).get("el") or "") or {}).split("\n")[0]
        b = _el_text(elements.get((el.get("end") or {}).get("el") or "") or {}).split("\n")[0]
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
    "rect": "блок", "diamond": "ромб/решение", "ellipse": "овал", "pill": "скруглённый блок (старт/финиш)",
    "cylinder": "цилиндр (база данных)", "triangle": "треугольник", "triangleDown": "треугольник вниз",
    "parallelogram": "параллелограмм (ввод/вывод данных)", "parallelogramL": "параллелограмм", "sticky": "стикер", "text": "текст", "image": "картинка",
    "table": "таблица", "card": "карточка оргструктуры", "gantt": "диаграмма Ганта", "vector": "иконка", "frame": "фрейм",
}
_COLOR_RU = {
    "red": "красный", "orange": "оранжевый", "yellow": "жёлтый", "green": "зелёный", "blue": "синий",
    "purple": "фиолетовый", "pink": "розовый", "gray": "серый",
    "lime": "салатовый", "teal": "бирюзовый", "cyan": "голубой", "indigo": "индиго", "brown": "коричневый",
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
        text = _one_line(_el_text(el).split("\n")[0])
        return f"#{num[eid]} «{text}»" if eid in num and text else (f"#{num[eid]}" if eid in num else "?")

    out = [f"Доска «{title}»:" if title else "Доска:", "Блоки (сверху вниз, слева направо):"]
    for el in shapes:
        st = el.get("style") or {}
        color = st.get("fill") if el["type"] == "sticky" else (st.get("fill") if st.get("fill") not in (None, "none") else st.get("stroke"))
        kind = _SHAPE_RU.get(el["type"], el["type"])
        if color in _COLOR_RU:
            kind += ", " + _COLOR_RU[str(color)]
        author = el.get("author") or {}
        if el["type"] == "sticky" and author.get("name"):
            kind += ", автор " + str(author["name"])  # кто что предложил — Лео отвечает и на это
        if el["type"] in ("table", "gantt"):
            body_lines = [ln for ln in _el_text(el).split("\n") if ln.strip()]
            out.append(f"- #{num[el['id']]} [{kind}]" + ("".join(f"\n    {ln}" for ln in body_lines[:60]) or " пустая"))
            continue
        text = _one_line(_el_text(el))
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
    "lime": "#66a80f", "teal": "#0ca678", "cyan": "#1098ad", "indigo": "#4263eb", "brown": "#8d6e4f",
}
_FILL = {
    "ink": "#dee2e6", "gray": "#f1f3f5", "red": "#ffe3e3", "orange": "#ffe8cc", "yellow": "#fff3bf",
    "green": "#d3f9d8", "blue": "#d0ebff", "purple": "#e5dbff", "pink": "#ffdeeb", "white": "#ffffff",
    "lime": "#e9fac8", "teal": "#c3fae8", "cyan": "#c5f6fa", "indigo": "#dbe4ff", "brown": "#efe3d6",
}
_STICKY = {
    "yellow": "#fff3a3", "orange": "#ffd8a8", "pink": "#ffc9de", "green": "#c8f2c2",
    "blue": "#c5e3ff", "purple": "#e3d4ff", "gray": "#e9ecef",
    "lime": "#dcf5b0", "teal": "#b2f2e5", "cyan": "#b9eef7", "indigo": "#d2dbff", "brown": "#e8d8c6",
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


def _rel_lum(hex6: str) -> float:
    def ch(i: int) -> float:
        v = int(hex6[i:i + 2], 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)


def auto_text_color(st: dict[str, Any], fill: Optional[str]) -> Optional[str]:
    """Цвет текста по заливке (как во Figma, webapp/board.js autoTextColor): None — цвет выбран вручную или заливки нет."""
    if (st.get("textColor") and st.get("textColor") != "ink") or st.get("fillOff") or not fill or not _HEX_RE.match(fill):
        return None
    if st.get("fillOpacity") is not None and float(st["fillOpacity"]) < 0.5:
        return None
    lum = _rel_lum(fill.lower())
    return "#ffffff" if 1.05 / (lum + 0.05) > (lum + 0.05) / (_rel_lum("#1f2328") + 0.05) else "#1f2328"


def _attr(v: Any) -> str:
    return html.escape(str(v), quote=True)


def _svg_text(x: float, y: float, text: str, fs: float = 13, color: str = "#111827", anchor: str = "start", weight: str = "") -> str:
    w = f' font-weight="{weight}"' if weight else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" fill="{color}" text-anchor="{anchor}" '
            f'dominant-baseline="middle"{w}>{html.escape(text)}</text>')


def _radii(st: dict[str, Any], w: float, h: float, default: float) -> list[float]:
    m = st.get("radii")
    if isinstance(m, str) and _RADII_RE.match(m):
        r = [float(x) for x in m.split()]
    else:
        r = [_num(st.get("radius"), default, 0, 1e4)] * 4
    lim = max(0.0, min(w, h) / 2)
    return [max(0.0, min(lim, float(v))) for v in r]


def _rounded_d(x: float, y: float, w: float, h: float, r: list[float]) -> str:
    tl, tr, br, bl = r
    return (f"M{x + tl} {y}H{x + w - tr}" + (f"A{tr} {tr} 0 0 1 {x + w} {y + tr}" if tr else "") +
            f"V{y + h - br}" + (f"A{br} {br} 0 0 1 {x + w - br} {y + h}" if br else "") +
            f"H{x + bl}" + (f"A{bl} {bl} 0 0 1 {x} {y + h - bl}" if bl else "") +
            f"V{y + tl}" + (f"A{tl} {tl} 0 0 1 {x + tl} {y}" if tl else "") + "Z")


def flow_shape_path(t: str, x: float, y: float, w: float, h: float) -> tuple[str, str]:
    """Контур фигуры блок-схемы (как webapp/board-geom.js shapePathD): (заливка+обводка, доп. линия)."""
    k = min(w * 0.25, h * 0.3)
    if t == "pill":
        r = min(w, h) / 2
        return (f"M{x + r:.1f} {y:.1f}H{x + w - r:.1f}A{r:.1f} {r:.1f} 0 0 1 {x + w - r:.1f} {y + h:.1f}"
                f"H{x + r:.1f}A{r:.1f} {r:.1f} 0 0 1 {x + r:.1f} {y:.1f}Z", "")
    if t == "cylinder":
        c = max(2.0, min(h * 0.12, w * 0.25, 40.0))
        rx = w / 2
        return (f"M{x:.1f} {y + c:.1f}A{rx:.1f} {c:.1f} 0 0 1 {x + w:.1f} {y + c:.1f}V{y + h - c:.1f}"
                f"A{rx:.1f} {c:.1f} 0 0 1 {x:.1f} {y + h - c:.1f}Z",
                f"M{x:.1f} {y + c:.1f}A{rx:.1f} {c:.1f} 0 0 0 {x + w:.1f} {y + c:.1f}")
    pts = {
        "triangle": [(w / 2, 0), (w, h), (0, h)],
        "triangleDown": [(0, 0), (w, 0), (w / 2, h)],
        "parallelogram": [(k, 0), (w, 0), (w - k, h), (0, h)],
        "parallelogramL": [(0, 0), (w - k, 0), (w, h), (k, h)],
    }.get(t, [(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)])
    return "M" + "L".join(f"{x + px:.1f} {y + py:.1f}" for px, py in pts) + "Z", ""


def _svg_vector(el: dict[str, Any]) -> str:
    vw, vh = el["vb"]
    st = el.get("style") or {}
    parts = []
    for p in el.get("paths") or []:
        fill = _fill(st.get("fill") or p.get("fill")) if p.get("fill") else None
        stroke = _ink(st.get("stroke") or p.get("stroke")) if p.get("stroke") else None
        attrs = f' fill="{_attr(fill) if fill else "none"}"'
        if stroke:
            attrs += f' stroke="{_attr(stroke)}" stroke-width="{p.get("sw", 1)}"'
            if p.get("cap"):
                attrs += f' stroke-linecap="{p["cap"]}"'
            if p.get("join"):
                attrs += f' stroke-linejoin="{p["join"]}"'
        if p.get("rule") == "evenodd":
            attrs += ' fill-rule="evenodd"'
        parts.append(f'<path d="{p["d"]}"{attrs}/>')
    return (f'<svg x="{el["x"]}" y="{el["y"]}" width="{el["w"]}" height="{el["h"]}" viewBox="0 0 {vw} {vh}" '
            f'preserveAspectRatio="none" overflow="visible">{"".join(parts)}</svg>')


def _gantt_geom(el: dict[str, Any]) -> dict[str, Any]:
    """Размеры Ганта — те же, что рисует клиент (webapp/board.js ganttLayout)."""
    cols = {"name": 150, "start": 64, "end": 64, "owner": 110}
    label_w = sum(cols.values())
    unit_w = 34 if el.get("unit") != "week" else 64
    return {"cols": cols, "label_w": label_w, "unit_w": unit_w, "head": 44, "row": 34}


def _svg_composite(el: dict[str, Any]) -> str:
    t = el["type"]
    x, y, w, h = float(el["x"]), float(el["y"]), float(el["w"]), float(el["h"])
    out: list[str] = []
    line = "#d0d5dd"
    if t == "table":
        rows = table_rows(el.get("html")) or [[""]]
        ncols = max(len(r) for r in rows) or 1
        rh = h / len(rows)
        cw = w / ncols
        head = "<th" in str(el.get("html") or "")
        if head:
            out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{rh:.1f}" fill="#f1f3f5"/>')
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="{line}"/>')
        for i in range(1, len(rows)):
            out.append(f'<line x1="{x}" y1="{y + i * rh:.1f}" x2="{x + w}" y2="{y + i * rh:.1f}" stroke="{line}"/>')
        for j in range(1, ncols):
            out.append(f'<line x1="{x + j * cw:.1f}" y1="{y}" x2="{x + j * cw:.1f}" y2="{y + h}" stroke="{line}"/>')
        for i, r in enumerate(rows):
            for j, cell in enumerate(r[:ncols]):
                if cell:
                    out.append(_svg_text(x + j * cw + 8, y + i * rh + rh / 2, cell[:60], 13, "#111827", "start", "600" if head and i == 0 else ""))
        return "".join(out)
    if t == "card":
        st = el.get("style") or {}
        head_fill = _FILL.get(str(st.get("fill") or "purple"), _FILL["purple"])
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="#ffffff" stroke="{line}"/>')
        out.append(f'<path d="M{x},{y + 10}a10,10 0 0 1 10,-10h{w - 20}a10,10 0 0 1 10,10v26h-{w}z" fill="{head_fill}"/>')
        out.append(_svg_text(x + w / 2, y + 18, str(el.get("title") or "")[:60], 13, "#1f2937", "middle", "600"))
        cx, cy = x + w / 2, y + 36 + 40
        if el.get("photo"):
            out.append(f'<clipPath id="ph-{_attr(el["id"])}"><circle cx="{cx}" cy="{cy}" r="30"/></clipPath>')
            out.append(f'<image x="{cx - 30}" y="{cy - 30}" width="60" height="60" preserveAspectRatio="xMidYMid slice" '
                       f'clip-path="url(#ph-{_attr(el["id"])})" href="{_attr(el["photo"])}"/>')
        else:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="30" fill="#f1f3f5" stroke="{line}" stroke-dasharray="3 3"/>')
        out.append(_svg_text(cx, cy + 46, str(el.get("name") or "")[:60], 14, "#111827", "middle", "600"))
        about = _one_line(el.get("text") or "")
        if about:
            out.append(_svg_text(cx, cy + 68, about[:70], 12, "#6b7280", "middle"))
        return "".join(out)
    g = _gantt_geom(el)
    try:
        start = dt.date.fromisoformat(str(el.get("start")))
    except ValueError:
        start = dt.date.today()
    step = 7 if el.get("unit") == "week" else 1
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8" fill="#ffffff" stroke="{line}"/>')
    out.append(f'<line x1="{x}" y1="{y + g["head"]}" x2="{x + w}" y2="{y + g["head"]}" stroke="{line}"/>')
    cx = x
    for key, label in (("name", "Задача"), ("start", "Начало"), ("end", "Конец"), ("owner", "Ответственный")):
        out.append(_svg_text(cx + 8, y + g["head"] / 2, label, 12, "#6b7280"))
        cx += g["cols"][key]
    gx = x + g["label_w"]
    for i in range(int(el.get("span") or 14)):
        d = start + dt.timedelta(days=i * step)
        out.append(_svg_text(gx + i * g["unit_w"] + g["unit_w"] / 2, y + g["head"] / 2, f"{d.day}", 11, "#6b7280", "middle"))
    for i, task in enumerate(el.get("tasks") or []):
        ry = y + g["head"] + i * g["row"]
        cx = x
        for key in ("name", "start", "end", "owner"):
            val = task.get(key) or ""
            if key in ("start", "end"):
                val = _fmt_day(val)
            out.append(_svg_text(cx + 8, ry + g["row"] / 2, str(val)[:40], 12))
            cx += g["cols"][key]
        try:
            a = (dt.date.fromisoformat(task["start"]) - start).days / step
            b = ((dt.date.fromisoformat(task["end"]) - start).days + 1) / step
        except ValueError:
            continue
        fill = _INK.get(str(task.get("color") or "blue"), _INK["blue"])
        out.append(f'<rect x="{gx + a * g["unit_w"]:.1f}" y="{ry + 8}" width="{max(4.0, (b - a) * g["unit_w"]):.1f}" '
                   f'height="{g["row"] - 16}" rx="4" fill="{fill}" fill-opacity="0.75"/>')
    return "".join(out)


def render_board_svg(body: Any, *, view: Optional[tuple[float, float, float, float]] = None, labels: bool = True) -> str:
    """Статичный SVG доски (без правки): фигуры, подписи, линии по сохранённым точкам.
    view — своя рамка (x, y, w, h) вместо границ всего содержимого; labels=False — без имён фреймов."""
    elements = parse_board(body)["elements"]
    if view:
        x0, y0, w, h = view
        w, h = max(1.0, w), max(1.0, h)
    else:
        x0, y0, x1, y1 = _bounds(elements)
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.1f} {y0:.1f} {w:.1f} {h:.1f}" '
        f'width="{w:.0f}" height="{h:.0f}" font-family="system-ui, sans-serif">',
        '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
        'orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="context-stroke"/></marker></defs>',
    ]
    def _hidden(e: dict[str, Any]) -> bool:
        seen = 0
        while e and seen < 30:
            if e.get("hidden"):
                return True
            e = elements.get(e.get("frame") or "")
            seen += 1
        return False

    for el in sorted(elements.values(), key=lambda e: float(e.get("z") or 0)):
        if _hidden(el):
            continue  # скрыт в слоях (сам или его фрейм)
        st = el.get("style") or {}
        stroke = _attr(_ink(st.get("stroke")))
        sw = _num(st.get("strokeWidth"), 2, 0, 40)
        if sw == 0 or st.get("strokeOff"):
            stroke = "none"
        elif st.get("strokeOpacity") is not None and st["strokeOpacity"] < 1:
            stroke += f'" stroke-opacity="{st["strokeOpacity"]}'
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
        if t in ("table", "card", "gantt"):
            out.append(_svg_composite(el))
            continue
        if t == "vector":
            out.append(_svg_vector(el))
            continue
        fill = _fill(st.get("fill"))
        if t == "sticky":
            sf = str(st.get("fill") or "yellow")
            fill = sf if _HEX_RE.match(sf) else _STICKY.get(sf, _STICKY["yellow"])
        fill_attr = _attr(fill) if fill and not st.get("fillOff") else "none"
        if fill_attr != "none" and st.get("fillOpacity") is not None and st["fillOpacity"] < 1:
            fill_attr += f'" fill-opacity="{st["fillOpacity"]}'
        if t == "rect":
            if st.get("radii"):
                out.append(f'<path d="{_rounded_d(x, y, ew, eh, _radii(st, ew, eh, 8))}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
            else:
                rx = min(_num(st.get("radius"), 8, 0, 1e4), ew / 2, eh / 2)
                out.append(f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="{rx}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t == "frame":
            if el.get("frame") or (labels is False):
                # Фрейм макета: заливка и рамка — только свои (вложенные фреймы из Figma часто прозрачные).
                fs = f'stroke="{stroke}" stroke-width="{sw}"' if st.get("strokeWidth") and stroke != "none" else 'stroke="none"'
                ff = fill_attr if "fill" in st else ("none" if el.get("frame") else "#ffffff")
            else:
                fs = f'stroke="{stroke}" stroke-width="{sw}"' if st.get("strokeWidth") else 'stroke="#d0d5dd" stroke-width="1"'
                ff = _attr(fill or "#ffffff")
            out.append(f'<path d="{_rounded_d(x, y, ew, eh, _radii(st, ew, eh, 0))}" fill="{ff}" {fs}/>')
            if el.get("name") and labels:
                out.append(f'<text x="{x}" y="{y - 10}" font-size="14" fill="#6b7280">{html.escape(str(el["name"]))}</text>')
        elif t == "text" and fill:
            out.append(f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="2" fill="{fill_attr}"/>')
        elif t == "sticky":
            out.append(f'<rect x="{x}" y="{y}" width="{ew}" height="{eh}" rx="4" fill="{fill_attr}"/>')
        elif t == "ellipse":
            out.append(f'<ellipse cx="{x + ew / 2}" cy="{y + eh / 2}" rx="{ew / 2}" ry="{eh / 2}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t == "diamond":
            cx, cy = x + ew / 2, y + eh / 2
            out.append(f'<polygon points="{cx},{y} {x + ew},{cy} {cx},{y + eh} {x},{cy}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
        elif t in FLOW_SHAPES:
            d, extra = flow_shape_path(t, x, y, ew, eh)
            out.append(f'<path d="{d}" fill="{fill_attr}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"{dash}/>')
            if extra and stroke != "none":
                out.append(f'<path d="{extra}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')
        elif t == "image":
            out.append(f'<image x="{x}" y="{y}" width="{ew}" height="{eh}" href="{_attr(el.get("src") or "")}"/>')
        text = str(el.get("text") or "")
        if text and t != "image":
            fs = _num(st.get("fontSize"), 16, 6, 200)
            color = _attr((t != "sticky" and auto_text_color(st, fill)) or _ink(st.get("textColor"), "#111827"))
            lines = text.split("\n")
            lh = float(st.get("lineHeight") or 0) or fs * 1.3
            # Середина подписи: у треугольника — в широкой части, у цилиндра — под крышкой.
            mid = {"triangle": y + eh * 0.7, "triangleDown": y + eh * 0.3}.get(t, y + eh / 2)
            if t == "cylinder":
                mid += max(2.0, min(eh * 0.12, ew * 0.25, 40.0)) / 2
            cy = mid - (len(lines) - 1) * lh / 2
            align = st.get("align")
            if align not in ("left", "center", "right"):
                align = "left" if t == "text" else "center"
            anchor = {"left": "start", "center": "middle", "right": "end"}[align]
            # Поля как в редакторе: у текста без отступа, у ромба — 22% ширины, у остальных 10px.
            pad = 0 if t == "text" else ew * 0.22 if t in ("diamond", "triangle", "triangleDown") else ew * 0.18 if t.startswith("parallelogram") else 20
            tx = x + pad if align == "left" else x + ew - pad if align == "right" else x + ew / 2
            hl = st.get("highlight")
            if isinstance(hl, str) and _HEX_RE.match(hl):
                # Подсветка под словами (из Miro): ширина по числу символов — для статичного SVG хватает.
                for i, line in enumerate(lines):
                    lw = len(line) * fs * 0.55
                    lx = tx if align == "left" else tx - lw if align == "right" else tx - lw / 2
                    out.append(f'<rect x="{lx - 2:.1f}" y="{cy + i * lh - lh / 2:.1f}" width="{lw + 4:.1f}" height="{lh:.1f}" fill="{hl}"/>')
            spans = "".join(
                f'<tspan x="{tx}" y="{cy + i * lh:.1f}">{html.escape(line)}</tspan>' for i, line in enumerate(lines)
            )
            font = ""
            if st.get("fontFamily"):
                font += f' font-family="{html.escape(str(st["fontFamily"]))}, sans-serif"'
            if st.get("fontWeight") or st.get("bold"):
                font += f' font-weight="{int(st.get("fontWeight") or 600)}"'
            out.append(f'<text font-size="{fs}"{font} fill="{color}" text-anchor="{anchor}" dominant-baseline="middle">{spans}</text>')
    out.append("</svg>")
    return "".join(out)
