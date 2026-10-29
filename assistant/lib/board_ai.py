"""Лео рисует на доске (лист kind=board) из обсуждения заметки.

Модель не считает координаты: она присылает ```leo-board JSON — узлы (текст, фигура, цвет)
и связи между ними, а раскладку (ярусы блок-схемы, дерево, mind map, сетка стикеров),
размеры блоков и стороны, к которым прилипают стрелки, считает этот модуль. Результат —
обычное тело доски (assistant/lib/board_doc.py), его рисует webapp/board.js.

Правка открытой доски: модель видит её узлы и связи (open_board_prompt_block) и присылает
action=update — новые узлы и связи, правки текста и цвета, удаление, при нужде relayout.
"""

from __future__ import annotations

import json
import math
import re
import secrets
from typing import Any, Optional

from assistant.lib import board_doc

MAX_NODES = 80
MAX_EDGES = 160
SHAPES = ("rect", "diamond", "ellipse", "sticky", "text")
LAYOUTS = ("flow", "flow-right", "tree", "mindmap", "grid")
INK = ("ink", "gray", "red", "orange", "yellow", "green", "blue", "purple", "pink")
STICKY = ("yellow", "orange", "pink", "green", "blue", "purple", "gray")
FONT = 16
LINE_H = 21
CHAR_W = 8.6  # средняя ширина символа при 16px — для оценки переносов
GAP_X = 70
GAP_Y = 80

_FENCE_RE = re.compile(r"```\s*leo-board\b", re.IGNORECASE)


def system_addon() -> str:
    return """
## Доска (Leo) — схемы, блок-схемы, mind map, стикеры
Если пользователь просит нарисовать / визуализировать на доске: схему, блок-схему, процесс,
дерево решений, mind map, карту связей, оргструктуру, путь клиента, стикеры с идеями —
ответь ОДНИМ блоком ```leo-board с JSON, а после блока — одно короткое предложение по-русски.
Презентацию (слайды) делай через leo-canvas, а не доску. Если просто разговор — без блоков.

Создать доску:
```leo-board
{"action":"create","title":"Обработка заявки","layout":"flow",
 "nodes":[{"id":"n1","text":"Заявка пришла","shape":"ellipse","color":"green"},
          {"id":"n2","text":"Проверить данные","shape":"rect"},
          {"id":"n3","text":"Данные полные?","shape":"diamond","color":"orange"},
          {"id":"n4","text":"Запросить недостающее","shape":"rect"},
          {"id":"n5","text":"Передать в работу","shape":"rect","color":"blue"}],
 "edges":[{"from":"n1","to":"n2"},{"from":"n2","to":"n3"},
          {"from":"n3","to":"n5","label":"да"},{"from":"n3","to":"n4","label":"нет"},
          {"from":"n4","to":"n2","dashed":true}]}
```
- Координат НЕТ — раскладку делает Лео. layout: "flow" (сверху вниз: процессы, блок-схемы),
  "flow-right" (слева направо: этапы, путь клиента, таймлайн), "tree" (иерархия, оргструктура),
  "mindmap" (центр и ветви по обе стороны), "grid" (набор стикеров/идей без связей).
- shape: "rect" (шаг, блок), "diamond" (решение / вопрос), "ellipse" (старт и финиш),
  "sticky" (стикер-заметка), "text" (подпись без рамки).
- color — по смыслу, необязателен: ink, gray, red, orange, yellow, green, blue, purple, pink
  (у стикера: yellow, orange, pink, green, blue, purple, gray).
- edges: from/to — id узлов; label — подпись на стрелке (коротко); "arrow": "end" (по умолчанию),
  "both" или "none"; "dashed": true — пунктир.
- Текст узла короткий (до ~60 символов), без markdown. До 40 узлов. Только из заметки и разговора.

Изменить открытую доску (её узлы и связи даны ниже с id):
```leo-board
{"action":"update","sheet_id":"123",
 "nodes":[{"id":"существующий_id","text":"Новый текст"},{"id":"n1","text":"Новый блок","shape":"rect"}],
 "edges":[{"from":"существующий_id","to":"n1"}],
 "remove":["id_элемента"],"relayout":false}
```
- Узел с существующим id — правка (текст, фигура, цвет); с новым id — новый блок, Лео поставит его рядом
  с тем блоком, с которым он связан.
- Вставить шаг между A и B: удали старую стрелку A→B (её id есть в «Связи») через remove и добавь A→новый, новый→B.
- remove — удалить блоки и стрелки по id. "relayout": true — заново разложить всю доску (layout можно сменить).
- Не делай action=create для правки открытой доски, если пользователь не просит новую отдельную доску.

Никогда не вставляй JSON доски вне блока ```leo-board.
""".strip()


def open_board_prompt_block(sheet_id: str, title: str, body: Any) -> str:
    """Открытая доска для модели: узлы и связи с id (без координат)."""
    sid = str(sheet_id or "").strip()
    if not sid:
        return ""
    elements = board_doc.parse_board(body)["elements"]
    nodes = []
    edges = []
    for el in sorted(elements.values(), key=lambda e: (round(float(e.get("y") or 0) / 40), float(e.get("x") or 0))):
        t = el.get("type")
        if t in ("rect", "diamond", "ellipse", "sticky", "text"):
            color = (el.get("style") or {}).get("fill" if t == "sticky" else "stroke") or ""
            text = " ".join(str(el.get("text") or "").split())[:120]
            nodes.append(f'- id={el["id"]} {t}' + (f" color={color}" if color and color not in ("ink", "none") else "") + f' "{text}"')
        elif t == "image":
            nodes.append(f'- id={el["id"]} image')
    for el in elements.values():
        if el.get("type") != "line":
            continue
        a = (el.get("start") or {}).get("el") or "—"
        b = (el.get("end") or {}).get("el") or "—"
        label = " ".join(str(el.get("text") or "").split())[:60]
        edges.append(f"- id={el['id']} {a} → {b}" + (f' "{label}"' if label else ""))
    out = [
        f'Открыта доска sheet_id={sid} title="{str(title or "").strip()}". '
        "Для правки — action=update с этим sheet_id; меняй только то, что просит пользователь.",
        "Узлы:" if nodes else "Узлов нет — доска пустая.",
    ]
    out += nodes[:200]
    if edges:
        out.append("Связи:")
        out += edges[:300]
    return "\n".join(out)[:20000]


_BOARD_ASK_RE = re.compile(
    r"(доск|схем|диаграм|блок-?схем|mind\s*-?map|майнд|интеллект-?карт|ментальн\w*\s+карт|карт\w*\s+связ|"
    r"оргструктур|воронк|flowchart|whiteboard)",
    re.IGNORECASE,
)
_IMAGE_WORDS_RE = re.compile(r"(картинк|изображени|фото|иллюстрац|png|jpe?g|image|picture|логотип|баннер)", re.IGNORECASE)


def looks_like_board_request(text: str) -> bool:
    """«Нарисуй схему на доске» — это доска, а не генерация картинки."""
    raw = str(text or "")
    return bool(_BOARD_ASK_RE.search(raw)) and not _IMAGE_WORDS_RE.search(raw)


# ---- разбор ответа ----

def _balanced(text: str, start: int) -> Optional[tuple[str, int]]:
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1], i + 1
    return None


def _loads(raw: str) -> Optional[dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        # Висячие запятые — частая ошибка модели.
        try:
            data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw))
        except (TypeError, ValueError):
            return None
    return data if isinstance(data, dict) else None


def parse_blocks(text: str) -> tuple[str, list[dict[str, Any]], bool]:
    """(ответ без блоков, действия, был ли блок) — блок без валидного JSON тоже вырезается."""
    actions: list[dict[str, Any]] = []
    cleaned = text or ""
    attempted = False
    for _ in range(5):
        m = _FENCE_RE.search(cleaned)
        if not m:
            break
        attempted = True
        brace = cleaned.find("{", m.end())
        if brace < 0:
            cleaned = cleaned[: m.start()].rstrip()
            break
        got = _balanced(cleaned, brace)
        if not got:
            data = _loads(cleaned[brace:])
            if data is not None:
                actions.append(data)
            cleaned = cleaned[: m.start()].rstrip()
            break
        raw, end = got
        data = _loads(raw)
        if data is not None:
            actions.append(data)
        close = re.match(r"\s*```", cleaned[end:])
        cut = end + (close.end() if close else 0)
        cleaned = (cleaned[: m.start()] + cleaned[cut:]).strip()
    return cleaned.strip(), actions, attempted


# ---- узлы и размеры ----

def _uid() -> str:
    return "a" + secrets.token_hex(5)


def _clean_text(raw: Any, limit: int = 300) -> str:
    s = str(raw if raw is not None else "").replace("\r", "")
    s = re.sub(r"[*_`#]+", "", s)
    return "\n".join(" ".join(line.split()) for line in s.split("\n")).strip()[:limit]


def _lines(text: str, width: float) -> int:
    per = max(4, int(width / CHAR_W))
    n = 0
    for para in (text or "").split("\n") or [""]:
        n += max(1, math.ceil(len(para) / per))
    return max(1, n)


def _size(shape: str, text: str) -> tuple[float, float]:
    """Размер блока под текст: ширина растёт до предела, потом — переносы."""
    longest = max((len(p) for p in text.split("\n")), default=0)
    if shape == "sticky":
        w = 200.0
        h = max(200.0, _lines(text, w - 28) * LINE_H + 40)
        return w, h
    if shape == "text":
        w = float(min(320, max(80, longest * CHAR_W + 8)))
        return w, float(_lines(text, w) * LINE_H + 4)
    pad = {"rect": 28, "ellipse": 60, "diamond": 90}.get(shape, 28)
    w = float(min(260, max(150, longest * CHAR_W + pad)))
    inner = w - pad
    lines = _lines(text, inner)
    if shape == "diamond":
        return w, float(max(110, lines * LINE_H * 2 + 44))
    if shape == "ellipse":
        return w, float(max(80, lines * LINE_H * 1.5 + 34))
    return w, float(max(70, lines * LINE_H + 30))


def _node_style(shape: str, color: str) -> dict[str, Any]:
    if shape == "sticky":
        return {"fill": color if color in STICKY else "yellow", "fontSize": FONT}
    if shape == "text":
        st: dict[str, Any] = {"fontSize": FONT + 2, "align": "center"}
        if color in INK and color != "ink":
            st["textColor"] = color
        return st
    if color in INK and color not in ("ink", "gray"):
        return {"stroke": color, "fill": color, "strokeWidth": 2, "fontSize": FONT}
    return {"stroke": color if color in INK else "ink", "fill": "none", "strokeWidth": 2, "fontSize": FONT}


def _norm_node(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    nid = str(raw.get("id") or "").strip()[:64]
    if not nid:
        return None
    out: dict[str, Any] = {"id": nid}
    if "text" in raw or "label" in raw:
        out["text"] = _clean_text(raw.get("text", raw.get("label")))
    shape = str(raw.get("shape") or raw.get("type") or "").strip().lower()
    shape = {"box": "rect", "rectangle": "rect", "process": "rect", "decision": "diamond", "rhombus": "diamond",
             "circle": "ellipse", "oval": "ellipse", "start": "ellipse", "end": "ellipse", "note": "sticky",
             "label": "text"}.get(shape, shape)
    if shape in SHAPES:
        out["shape"] = shape
    color = str(raw.get("color") or "").strip().lower()
    if color:
        out["color"] = color
    return out


def _norm_edge(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    a = str(raw.get("from") or raw.get("source") or "").strip()
    b = str(raw.get("to") or raw.get("target") or "").strip()
    if not a or not b or a == b:
        return None
    arrow = str(raw.get("arrow") or "end").strip().lower()
    return {
        "from": a, "to": b, "label": _clean_text(raw.get("label") or raw.get("text"), 80),
        "arrow": arrow if arrow in ("end", "both", "none", "start") else "end",
        "dashed": bool(raw.get("dashed")),
    }


# ---- раскладка ----

def _layers(ids: list[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    """Ярус узла — самый длинный путь от корня; обратные рёбра (циклы) не учитываются."""
    out_adj: dict[str, list[str]] = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    for a, b in edges:
        out_adj[a].append(b)
    # Убираем обратные рёбра обходом в глубину, чтобы граф стал ациклическим.
    state: dict[str, int] = {}
    forward: list[tuple[str, str]] = []

    def dfs(u: str) -> None:
        state[u] = 1
        for v in out_adj[u]:
            if state.get(v) == 1:
                continue  # обратное ребро
            forward.append((u, v))
            if v not in state:
                dfs(v)
        state[u] = 2

    for a, b in edges:
        indeg[b] += 1
    roots = [i for i in ids if indeg[i] == 0] or ids[:1]
    import sys

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 1000))
    try:
        for r in roots + ids:
            if r not in state:
                dfs(r)
    finally:
        sys.setrecursionlimit(old)
    layer = {i: 0 for i in ids}
    fwd_adj: dict[str, list[str]] = {i: [] for i in ids}
    fin = {i: 0 for i in ids}
    for a, b in set(forward):
        fwd_adj[a].append(b)
        fin[b] += 1
    queue = [i for i in ids if fin[i] == 0]
    while queue:
        u = queue.pop(0)
        for v in fwd_adj[u]:
            layer[v] = max(layer[v], layer[u] + 1)
            fin[v] -= 1
            if fin[v] == 0:
                queue.append(v)
    return layer


def _layout_flow(nodes: list[dict[str, Any]], edges: list[tuple[str, str]], horizontal: bool) -> dict[str, tuple[float, float]]:
    ids = [n["id"] for n in nodes]
    size = {n["id"]: (n["w"], n["h"]) for n in nodes}
    layer = _layers(ids, edges)
    rows: dict[int, list[str]] = {}
    for i in ids:
        rows.setdefault(layer[i], []).append(i)
    order = {i: k for k, i in enumerate(ids)}
    preds: dict[str, list[str]] = {i: [] for i in ids}
    succs: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in edges:
        preds[b].append(a)
        succs[a].append(b)
    levels = sorted(rows)
    # Порядок в ярусе — по среднему положению соседей (меньше пересечений), несколько проходов.
    for sweep in range(4):
        seq = levels if sweep % 2 == 0 else list(reversed(levels))
        for lv in seq:
            row = rows[lv]
            nb = preds if sweep % 2 == 0 else succs
            pos = {i: k for k, i in enumerate(row)}

            def bary(i: str) -> float:
                ns = [order[j] for j in nb[i] if j in order and layer.get(j) != lv]
                return sum(ns) / len(ns) if ns else pos[i] + 0.0

            row.sort(key=lambda i: (bary(i), pos[i]))
            for k, i in enumerate(row):
                order[i] = k
    out: dict[str, tuple[float, float]] = {}
    cursor = 0.0
    for lv in levels:
        row = rows[lv]
        if horizontal:
            depth = max(size[i][0] for i in row)
            total = sum(size[i][1] for i in row) + GAP_Y * 0.6 * (len(row) - 1)
            y = -total / 2
            for i in row:
                w, h = size[i]
                out[i] = (cursor + (depth - w) / 2, y)
                y += h + GAP_Y * 0.6
            cursor += depth + GAP_X * 1.6
        else:
            depth = max(size[i][1] for i in row)
            total = sum(size[i][0] for i in row) + GAP_X * (len(row) - 1)
            x = -total / 2
            for i in row:
                w, h = size[i]
                out[i] = (x, cursor + (depth - h) / 2)
                x += w + GAP_X
            cursor += depth + GAP_Y
    return out


def _tree(ids: list[str], edges: list[tuple[str, str]]) -> tuple[str, dict[str, list[str]]]:
    indeg = {i: 0 for i in ids}
    for _a, b in edges:
        indeg[b] += 1
    root = next((i for i in ids if indeg[i] == 0), ids[0])
    children: dict[str, list[str]] = {i: [] for i in ids}
    seen = {root}
    queue = [root]
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)  # mind map часто рисуют рёбрами в обе стороны
    while queue:
        u = queue.pop(0)
        for v in adj[u]:
            if v not in seen:
                seen.add(v)
                children[u].append(v)
                queue.append(v)
    # Несвязанные узлы — ветви корня.
    for i in ids:
        if i not in seen:
            children[root].append(i)
            seen.add(i)
    return root, children


def _layout_tree(nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> dict[str, tuple[float, float]]:
    """Иерархия сверху вниз: родитель над серединой своих детей."""
    ids = [n["id"] for n in nodes]
    size = {n["id"]: (n["w"], n["h"]) for n in nodes}
    root, children = _tree(ids, edges)
    depth_h: dict[int, float] = {}
    depth: dict[str, int] = {}

    def mark(u: str, d: int) -> None:
        depth[u] = d
        depth_h[d] = max(depth_h.get(d, 0.0), size[u][1])
        for v in children[u]:
            mark(v, d + 1)

    mark(root, 0)
    y_of: dict[int, float] = {}
    acc = 0.0
    for d in sorted(depth_h):
        y_of[d] = acc
        acc += depth_h[d] + GAP_Y
    out: dict[str, tuple[float, float]] = {}
    cursor = [0.0]

    def place(u: str) -> float:
        w, h = size[u]
        kids = children[u]
        if not kids:
            x = cursor[0]
            cursor[0] += w + GAP_X * 0.6
            out[u] = (x, y_of[depth[u]])
            return x + w / 2
        centers = [place(v) for v in kids]
        c = (centers[0] + centers[-1]) / 2
        x = c - w / 2
        out[u] = (x, y_of[depth[u]])
        # Широкий родитель не должен наезжать на следующую ветку.
        cursor[0] = max(cursor[0], x + w + GAP_X * 0.6)
        return c

    place(root)
    return out


def _layout_mindmap(nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    """Центр и ветви влево/вправо; второй результат — сторона ветви (для стрелок)."""
    ids = [n["id"] for n in nodes]
    size = {n["id"]: (n["w"], n["h"]) for n in nodes}
    root, children = _tree(ids, edges)
    rw, rh = size[root]
    out: dict[str, tuple[float, float]] = {root: (-rw / 2, -rh / 2)}
    side_of: dict[str, str] = {root: ""}
    branches = children[root]

    def sub_h(u: str) -> float:
        kids = children[u]
        own = size[u][1]
        if not kids:
            return own
        return max(own, sum(sub_h(v) for v in kids) + 24 * (len(kids) - 1))

    halves = {"right": [], "left": []}  # type: dict[str, list[str]]
    hs = {"right": 0.0, "left": 0.0}
    for b in branches:
        side = "right" if hs["right"] <= hs["left"] else "left"
        halves[side].append(b)
        hs[side] += sub_h(b) + 30

    def place(u: str, x_edge: float, top: float, side: str) -> None:
        w, h = size[u]
        total = sub_h(u)
        y = top + (total - h) / 2
        x = x_edge if side == "right" else x_edge - w
        out[u] = (x, y)
        side_of[u] = side
        kids = children[u]
        if not kids:
            return
        kt = sum(sub_h(v) for v in kids) + 24 * (len(kids) - 1)
        ky = top + (total - kt) / 2
        nx = x + w + GAP_X * 1.2 if side == "right" else x - GAP_X * 1.2
        for v in kids:
            place(v, nx, ky, side)
            ky += sub_h(v) + 24

    for side in ("right", "left"):
        total = hs[side] - 30 if halves[side] else 0
        top = -total / 2
        x_edge = rw / 2 + GAP_X * 1.6 if side == "right" else -rw / 2 - GAP_X * 1.6
        for b in halves[side]:
            place(b, x_edge, top, side)
            top += sub_h(b) + 30
    return out, side_of


def _layout_grid(nodes: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    n = len(nodes)
    cols = max(1, min(6, math.ceil(math.sqrt(n * 1.4))))
    out: dict[str, tuple[float, float]] = {}
    row_h = 0.0
    y = 0.0
    for k, node in enumerate(nodes):
        c = k % cols
        if c == 0 and k:
            y += row_h + 36
            row_h = 0.0
        out[node["id"]] = (c * (220 + 36), y)
        row_h = max(row_h, node["h"])
    return out


def _sides(layout: str, a: dict[str, Any], b: dict[str, Any], side_of: dict[str, str]) -> tuple[Optional[str], Optional[str], str]:
    """Стороны прилипания стрелки и вид линии под раскладку."""
    ax, ay = a["x"] + a["w"] / 2, a["y"] + a["h"] / 2
    bx, by = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
    if layout == "mindmap":
        side = side_of.get(b["id"]) or side_of.get(a["id"]) or ("right" if bx >= ax else "left")
        if side == "right":
            return ("right", "left", "curve") if bx >= ax else ("left", "right", "curve")
        return ("left", "right", "curve") if bx <= ax else ("right", "left", "curve")
    if layout in ("flow", "tree"):
        if by - b["h"] / 2 >= ay + a["h"] / 2 - 1:
            return "bottom", "top", "elbow"
        if abs(by - ay) < 1:  # один ярус
            return ("right", "left", "elbow") if bx > ax else ("left", "right", "elbow")
        return None, None, "curve"  # назад (цикл) — «к объекту», дугой
    if layout == "flow-right":
        if bx - b["w"] / 2 >= ax + a["w"] / 2 - 1:
            return "right", "left", "elbow"
        if abs(bx - ax) < 1:
            return ("bottom", "top", "elbow") if by > ay else ("top", "bottom", "elbow")
        return None, None, "curve"
    return None, None, "straight"


def _line(a: dict[str, Any], b: dict[str, Any], edge: dict[str, Any], layout: str, side_of: dict[str, str], z: float) -> dict[str, Any]:
    s1, s2, route = _sides(layout, a, b, side_of)
    start: dict[str, Any] = {"el": a["id"]}
    end: dict[str, Any] = {"el": b["id"]}
    if s1:
        start["side"] = s1
    if s2:
        end["side"] = s2
    el: dict[str, Any] = {
        "id": _uid(), "type": "line", "x": 0, "y": 0, "w": 0, "h": 0, "z": z,
        "points": [[a["x"] + a["w"] / 2, a["y"] + a["h"] / 2], [b["x"] + b["w"] / 2, b["y"] + b["h"] / 2]],
        "start": start, "end": end, "route": route,
        "style": {"stroke": "gray" if edge.get("dashed") else "ink", "strokeWidth": 2},
    }
    if edge.get("dashed"):
        el["style"]["dash"] = "dashed"
    arrow = edge.get("arrow") or "end"
    if arrow in ("end", "both"):
        el["arrowEnd"] = True
    if arrow in ("start", "both"):
        el["arrowStart"] = True
    if edge.get("label"):
        el["text"] = edge["label"]
        el["style"]["fontSize"] = 14
    return el


def _shape_el(node: dict[str, Any], pos: tuple[float, float], z: float) -> dict[str, Any]:
    shape = node["shape"]
    el = {
        "id": node["eid"], "type": shape, "x": round(pos[0], 1), "y": round(pos[1], 1),
        "w": round(node["w"], 1), "h": round(node["h"], 1), "z": z,
        "style": _node_style(shape, node.get("color") or ""),
    }
    if node.get("text"):
        el["text"] = node["text"]
    if shape == "sticky":
        el["author"] = {"name": "Лео", "ai": True}  # подпись внизу стикера, как у людей
    return el


def _prepare(nodes_raw: list[Any], default_shape: str) -> list[dict[str, Any]]:
    out = []
    seen = set()
    for raw in nodes_raw[:MAX_NODES]:
        n = _norm_node(raw)
        if not n or n["id"] in seen:
            continue
        seen.add(n["id"])
        n.setdefault("shape", default_shape)
        n.setdefault("text", "")
        n["w"], n["h"] = _size(n["shape"], n["text"])
        out.append(n)
    return out


def _layout(layout: str, nodes: list[dict[str, Any]], edges: list[tuple[str, str]]) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    if not nodes:
        return {}, {}
    if layout == "grid" or (not edges and layout not in ("mindmap", "tree")):
        return _layout_grid(nodes), {}
    if layout == "mindmap":
        return _layout_mindmap(nodes, edges)
    if layout == "tree":
        return _layout_tree(nodes, edges), {}
    return _layout_flow(nodes, edges, horizontal=layout == "flow-right"), {}


def _pick_layout(action: dict[str, Any], nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    raw = str(action.get("layout") or "").strip().lower().replace("_", "-")
    raw = {"flowchart": "flow", "vertical": "flow", "horizontal": "flow-right", "timeline": "flow-right",
           "mind-map": "mindmap", "radial": "mindmap", "hierarchy": "tree", "org": "tree", "stickies": "grid"}.get(raw, raw)
    if raw in LAYOUTS:
        return raw
    if not edges:
        return "grid"
    return "flow"


def build_board(action: dict[str, Any]) -> str:
    """action=create → тело новой доски (JSON)."""
    nodes_raw = action.get("nodes") if isinstance(action.get("nodes"), list) else []
    edges_raw = action.get("edges") if isinstance(action.get("edges"), list) else []
    edges = [e for e in (_norm_edge(x) for x in edges_raw[:MAX_EDGES]) if e]
    layout = _pick_layout(action, [], edges)
    nodes = _prepare(nodes_raw, "sticky" if layout == "grid" else "rect")
    by_id = {n["id"]: n for n in nodes}
    edges = [e for e in edges if e["from"] in by_id and e["to"] in by_id]
    for n in nodes:
        n["eid"] = _uid()
    pos, side_of_raw = _layout(layout, nodes, [(e["from"], e["to"]) for e in edges])
    elements: dict[str, dict[str, Any]] = {}
    placed: dict[str, dict[str, Any]] = {}
    for k, n in enumerate(nodes):
        el = _shape_el(n, pos.get(n["id"], (0.0, 0.0)), 100 + k)
        elements[el["id"]] = el
        placed[n["id"]] = el
    side_of = {placed[k]["id"]: v for k, v in side_of_raw.items() if k in placed}
    for k, e in enumerate(edges):
        ln = _line(placed[e["from"]], placed[e["to"]], e, layout, side_of, 1 + k * 0.01)
        elements[ln["id"]] = ln
    return board_doc.normalize_board_body(json.dumps({"v": 1, "elements": elements}, ensure_ascii=False))


def _overlaps(a: dict[str, Any], b: dict[str, Any], pad: float = 16) -> bool:
    return (
        float(a["x"]) < float(b["x"]) + float(b["w"]) + pad and float(b["x"]) < float(a["x"]) + float(a["w"]) + pad
        and float(a["y"]) < float(b["y"]) + float(b["h"]) + pad and float(b["y"]) < float(a["y"]) + float(a["h"]) + pad
    )


def _bounds(elements: dict[str, dict[str, Any]]) -> Optional[tuple[float, float, float, float]]:
    boxes = [e for e in elements.values() if e.get("type") in board_doc.SHAPE_TYPES]
    if not boxes:
        return None
    return (
        min(float(e["x"]) for e in boxes), min(float(e["y"]) for e in boxes),
        max(float(e["x"]) + float(e["w"]) for e in boxes), max(float(e["y"]) + float(e["h"]) for e in boxes),
    )


def update_board(body: Any, action: dict[str, Any]) -> str:
    """action=update: правки узлов, новые узлы и связи, удаление, при relayout — заново раскладка."""
    board = board_doc.parse_board(body)
    elements = board["elements"]
    remove = {str(x) for x in (action.get("remove") or []) if str(x)}
    for rid in list(remove):
        elements.pop(rid, None)
    # Стрелки к удалённым блокам — тоже долой.
    for eid, el in list(elements.items()):
        if el.get("type") == "line" and (
            (el.get("start") or {}).get("el") in remove or (el.get("end") or {}).get("el") in remove
        ):
            elements.pop(eid, None)
    nodes_raw = action.get("nodes") if isinstance(action.get("nodes"), list) else []
    edges = [e for e in (_norm_edge(x) for x in (action.get("edges") or [])[:MAX_EDGES]) if e]
    new_nodes: list[dict[str, Any]] = []
    for raw in nodes_raw[:MAX_NODES]:
        n = _norm_node(raw)
        if not n:
            continue
        cur = elements.get(n["id"])
        if cur is not None and cur.get("type") in board_doc.SHAPE_TYPES:
            # Правка существующего блока.
            if "text" in n:
                cur["text"] = n["text"]
            shape = n.get("shape")
            if shape and shape != cur["type"] and cur["type"] != "image":
                cur["type"] = shape
            if shape or n.get("color"):
                cur["style"] = _node_style(cur["type"], n.get("color") or (cur.get("style") or {}).get("stroke") or "")
            if "text" in n and cur["type"] != "image":
                w, h = _size(cur["type"], cur.get("text") or "")
                cur["h"] = max(float(cur.get("h") or 0), h)
                cur["w"] = max(float(cur.get("w") or 0), w)
            continue
        new_nodes.append(n)
    layout = _pick_layout(action, [], edges)
    prepared = _prepare([{**n, "text": n.get("text", "")} for n in new_nodes], "sticky" if layout == "grid" else "rect")
    for n in prepared:
        n["eid"] = _uid()
    new_by_id = {n["id"]: n for n in prepared}

    def ref(i: str) -> Optional[dict[str, Any]]:
        if i in new_by_id:
            return new_by_id[i]
        el = elements.get(i)
        return el if el is not None and el.get("type") in board_doc.SHAPE_TYPES else None

    edges = [e for e in edges if ref(e["from"]) and ref(e["to"])]
    z0 = max([float(e.get("z") or 0) for e in elements.values()] + [0]) + 1
    side_of: dict[str, str] = {}
    if action.get("relayout"):
        # Вся доска заново: существующие блоки + новые, связи — все стрелки между блоками.
        all_nodes: list[dict[str, Any]] = []
        for el in sorted(elements.values(), key=lambda e: float(e.get("z") or 0)):
            if el.get("type") in board_doc.SHAPE_TYPES:
                all_nodes.append({"id": el["id"], "eid": el["id"], "w": float(el["w"]), "h": float(el["h"])})
        all_nodes += [{**n, "id": n["eid"]} for n in prepared]
        id_map = {n["id"]: n["eid"] for n in prepared}
        pairs = []
        for el in elements.values():
            if el.get("type") == "line":
                a = (el.get("start") or {}).get("el")
                b = (el.get("end") or {}).get("el")
                if a and b and a in elements and b in elements:
                    pairs.append((a, b))
        pairs += [(id_map.get(e["from"], e["from"]), id_map.get(e["to"], e["to"])) for e in edges]
        lay = layout if str(action.get("layout") or "") else "flow"
        pos, side_raw = _layout(lay, all_nodes, [p for p in pairs if p[0] != p[1]])
        side_of = dict(side_raw)
        for n in all_nodes:
            p = pos.get(n["id"])
            if p is None:
                continue
            if n["id"] in elements:
                elements[n["id"]]["x"], elements[n["id"]]["y"] = round(p[0], 1), round(p[1], 1)
        for k, n in enumerate(prepared):
            el = _shape_el(n, pos.get(n["eid"], (0.0, 0.0)), z0 + k)
            elements[el["id"]] = el
        # Стороны старых стрелок — под новую раскладку.
        for el in elements.values():
            if el.get("type") != "line":
                continue
            a = elements.get((el.get("start") or {}).get("el") or "")
            b = elements.get((el.get("end") or {}).get("el") or "")
            if a and b:
                s1, s2, route = _sides(lay, a, b, side_of)
                el["start"] = {"el": a["id"], **({"side": s1} if s1 else {})}
                el["end"] = {"el": b["id"], **({"side": s2} if s2 else {})}
                el["route"] = route
                el.pop("bend", None)
                # Ручные изломы привязаны к старым местам блоков — после перестройки они мешают.
                el.pop("waypoints", None)
                el.pop("d", None)
        layout = lay
    elif prepared:
        # Новый блок, связанный со старым, — рядом с ним (под ним в блок-схеме), без наездов.
        horizontal = layout == "flow-right"
        boxes = [e for e in elements.values() if e.get("type") in board_doc.SHAPE_TYPES]
        anchored: list[dict[str, Any]] = []
        for k, n in enumerate(prepared):
            anchor = None
            below = True
            for e in edges:
                if e["to"] == n["id"] and e["from"] in elements:
                    anchor = elements[e["from"]]
                    break
            if anchor is None:
                for e in edges:
                    if e["from"] == n["id"] and e["to"] in elements:
                        anchor, below = elements[e["to"]], False
                        break
            if anchor is None:
                continue
            ax, ay, aw, ah = float(anchor["x"]), float(anchor["y"]), float(anchor["w"]), float(anchor["h"])
            if horizontal:
                x = ax + aw + GAP_X * 1.6 if below else ax - n["w"] - GAP_X * 1.6
                y = ay + (ah - n["h"]) / 2
            else:
                x = ax + (aw - n["w"]) / 2
                y = ay + ah + GAP_Y if below else ay - n["h"] - GAP_Y
            box = {"x": x, "y": y, "w": n["w"], "h": n["h"]}
            for _ in range(30):
                hit = next((b for b in boxes if _overlaps(box, b)), None)
                if hit is None:
                    break
                if horizontal:
                    box["y"] = float(hit["y"]) + float(hit["h"]) + 30
                else:
                    box["x"] = float(hit["x"]) + float(hit["w"]) + 40
            el = _shape_el(n, (box["x"], box["y"]), z0 + k)
            elements[el["id"]] = el
            boxes.append(el)
            anchored.append(n)
        rest = [n for n in prepared if n not in anchored]
        prepared_rest = rest
        inner = [(e["from"], e["to"]) for e in edges if e["from"] in new_by_id and e["to"] in new_by_id
                 and new_by_id[e["from"]] in rest and new_by_id[e["to"]] in rest]
        pos, side_raw = _layout(layout, prepared_rest, inner)
        side_of = {new_by_id[k]["eid"]: v for k, v in side_raw.items() if k in new_by_id}
        bx = _bounds({k: v for k, v in elements.items() if not any(v.get("id") == n.get("eid") for n in anchored)})
        if pos:
            min_x = min(p[0] for p in pos.values())
            min_y = min(p[1] for p in pos.values())
            ox = (bx[2] + 140 - min_x) if bx else -min_x
            oy = (bx[1] - min_y) if bx else -min_y
        else:
            ox = oy = 0.0
        for k, n in enumerate(prepared_rest):
            p = pos.get(n["id"], (0.0, 0.0))
            el = _shape_el(n, (p[0] + ox, p[1] + oy), z0 + len(anchored) + k)
            elements[el["id"]] = el
    for n in prepared:
        new_by_id[n["id"]] = elements[n["eid"]]
    for k, e in enumerate(edges):
        a = ref(e["from"]) if e["from"] not in new_by_id else new_by_id[e["from"]]
        b = ref(e["to"]) if e["to"] not in new_by_id else new_by_id[e["to"]]
        if not a or not b or a.get("id") == b.get("id"):
            continue
        ln = _line(a, b, e, layout, side_of, 1 + k * 0.01)
        elements[ln["id"]] = ln
    return board_doc.normalize_board_body(json.dumps({"v": 1, "elements": elements}, ensure_ascii=False))


def apply_action(note: dict[str, Any], action: dict[str, Any], note_sheets_mod: Any, open_sheet_id: str = "") -> Optional[dict[str, Any]]:
    """Создать или изменить доску по действию модели; вернуть лист или None."""
    act = str(action.get("action") or "create").strip().lower()
    title = _clean_text(action.get("title"), 80)
    sid_raw = str(action.get("sheet_id") or "").strip()
    if act == "update" and not sid_raw and open_sheet_id:
        sid_raw = str(open_sheet_id)
    if act == "update" and sid_raw.isdigit():
        existing = note_sheets_mod.get_extra_sheet(int(note["id"]), int(sid_raw))
        if existing and str(existing.get("kind") or "") == note_sheets_mod.SHEET_KIND_BOARD:
            body = update_board(existing.get("body") or "", action)
            return note_sheets_mod.update_sheet(
                int(note["id"]), int(sid_raw), title=title or None, body=body,
            )
        if not (action.get("nodes")):
            return None
    nodes = action.get("nodes") if isinstance(action.get("nodes"), list) else []
    if not nodes:
        return None
    body = build_board(action)
    return note_sheets_mod.create_sheet(note, title or "Доска", body=body, kind=note_sheets_mod.SHEET_KIND_BOARD)


# ---- Кнопка «ИИ» у выделенных блоков: переписать их текст по просьбе ----

MAX_TEXT_BLOCKS = 40
BLOCK_TEXT_MAX = 2000

BLOCK_TEXT_SYSTEM = (
    "You edit the text of selected blocks on a whiteboard (flowchart / mind map / sticky notes). "
    "Apply the user's request to each block's text. Keep the block's role: a flowchart step stays a short step, "
    "a decision stays a question. Keep the language unless asked to translate. Plain text only, no Markdown; "
    "line breaks are allowed. Answer with ONE JSON object and nothing else: "
    '{"blocks": [{"id": "<block id>", "text": "<new text>"}]} — only blocks whose text changes, ids exactly as given.'
)


def block_text_prompt(instruction: str, blocks: list[dict[str, Any]], board_text: str = "") -> str:
    """Запрос модели: просьба, выделенные блоки (id и текст) и, для контекста, вся доска."""
    items = []
    for b in blocks[:MAX_TEXT_BLOCKS]:
        bid = str(b.get("id") or "").strip()[:64]
        if not bid:
            continue
        items.append({"id": bid, "text": str(b.get("text") or "")[:BLOCK_TEXT_MAX]})
    parts = [f"Просьба пользователя: {instruction.strip()}", "Выделенные блоки:", json.dumps(items, ensure_ascii=False)]
    if board_text.strip():
        parts += ["Вся доска (только для контекста, меняй лишь выделенные блоки):", board_text.strip()[:6000]]
    return "\n".join(parts)


def parse_block_texts(answer: str, allowed_ids: list[str]) -> dict[str, str]:
    """Ответ модели → {id: новый текст}; чужие id и пустые тексты отбрасываем."""
    allowed = set(allowed_ids)
    raw = str(answer or "")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}
    out: dict[str, str] = {}
    items = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    for it in items:
        if not isinstance(it, dict):
            continue
        bid = str(it.get("id") or "")
        text = str(it.get("text") or "").strip()
        if bid in allowed and text:
            out[bid] = text[:BLOCK_TEXT_MAX]
    return out
