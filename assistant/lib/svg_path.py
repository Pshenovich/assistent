"""SVG-путь → только абсолютные M / L / C / Q / Z (так хранит векторы доска, board_doc._PATH_RE).

Нужен для иконок (VK Icons: h/v/s/a и относительные команды). Дуги переводятся в кубические кривые.
"""

from __future__ import annotations

import math
import re

_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _f(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _tokenize(d: str) -> list[str]:
    out: list[str] = []
    # Флаги дуг без пробелов: после команды A первые 3 числа обычные, потом два флага по одной цифре.
    pos = 0
    cmd = ""
    argn = 0
    while pos < len(d):
        ch = d[pos]
        if ch.isspace() or ch == ",":
            pos += 1
            continue
        if ch.isalpha():
            cmd = ch.upper()
            argn = 0
            out.append(ch)
            pos += 1
            continue
        if cmd == "A" and argn % 7 in (3, 4) and ch in "01":
            out.append(ch)
            pos += 1
            argn += 1
            continue
        m = _TOKEN_RE.match(d, pos)
        if not m:
            pos += 1
            continue
        out.append(m.group(0))
        pos = m.end()
        argn += 1
    return out


def _arc_to_cubic(x1: float, y1: float, rx: float, ry: float, phi_deg: float, fa: int, fs: int,
                  x2: float, y2: float) -> list[tuple[float, ...]]:
    """Дуга SVG → кубические кривые (по приложению F.6 спецификации SVG)."""
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [(x1, y1, x2, y2, x2, y2)]
    phi = math.radians(phi_deg % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    rx, ry = abs(rx), abs(ry)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def ang(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    segs = max(1, math.ceil(abs(dt) / (math.pi / 2) - 1e-9))
    step = dt / segs
    k = 4 / 3 * math.tan(step / 4)
    out = []
    t = t1
    for _ in range(segs):
        c1, s1 = math.cos(t), math.sin(t)
        c2, s2 = math.cos(t + step), math.sin(t + step)
        p1 = (c1 - k * s1, s1 + k * c1)
        p2 = (c2 + k * s2, s2 - k * c2)
        p3 = (c2, s2)
        pts = []
        for px, py in (p1, p2, p3):
            px, py = px * rx, py * ry
            pts += [cos_p * px - sin_p * py + cx, sin_p * px + cos_p * py + cy]
        out.append(tuple(pts))
        t += step
    return out


def to_absolute(d: str) -> str:
    """Любой путь SVG → абсолютные M/L/C/Q/Z."""
    toks = _tokenize(str(d or ""))
    out: list[str] = []
    i = 0
    cx = cy = sx = sy = 0.0
    last_c: tuple[float, float] | None = None  # вторая контрольная точка для S
    last_q: tuple[float, float] | None = None  # контрольная точка для T
    cmd = ""
    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                out.append("Z")
                cx, cy = sx, sy
                last_c = last_q = None
                continue
        up = cmd.upper()
        n = _ARGS.get(up, 0)
        if not n or i + n > len(toks) or any(x.isalpha() for x in toks[i:i + n]):
            i += 1
            continue
        a = [float(x) for x in toks[i:i + n]]
        i += n
        rel = cmd.islower()
        if up == "M":
            x, y = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
            out.append(f"M{_f(x)} {_f(y)}")
            cx, cy, sx, sy = x, y, x, y
            cmd = "l" if rel else "L"  # следующие пары после M — это L
            last_c = last_q = None
        elif up in ("L", "H", "V"):
            if up == "L":
                x, y = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
            elif up == "H":
                x, y = (cx + a[0] if rel else a[0]), cy
            else:
                x, y = cx, (cy + a[0] if rel else a[0])
            out.append(f"L{_f(x)} {_f(y)}")
            cx, cy = x, y
            last_c = last_q = None
        elif up in ("C", "S"):
            if up == "C":
                pts = [(cx + a[j], cy + a[j + 1]) if rel else (a[j], a[j + 1]) for j in (0, 2, 4)]
            else:
                p1 = (2 * cx - last_c[0], 2 * cy - last_c[1]) if last_c else (cx, cy)
                rest = [(cx + a[j], cy + a[j + 1]) if rel else (a[j], a[j + 1]) for j in (0, 2)]
                pts = [p1] + rest
            out.append("C" + " ".join(f"{_f(px)} {_f(py)}" for px, py in pts))
            last_c = pts[1]
            cx, cy = pts[2]
            last_q = None
        elif up in ("Q", "T"):
            if up == "Q":
                pts = [(cx + a[j], cy + a[j + 1]) if rel else (a[j], a[j + 1]) for j in (0, 2)]
            else:
                p1 = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                p2 = (cx + a[0], cy + a[1]) if rel else (a[0], a[1])
                pts = [p1, p2]
            out.append("Q" + " ".join(f"{_f(px)} {_f(py)}" for px, py in pts))
            last_q = pts[0]
            cx, cy = pts[1]
            last_c = None
        elif up == "A":
            x, y = (cx + a[5], cy + a[6]) if rel else (a[5], a[6])
            for seg in _arc_to_cubic(cx, cy, a[0], a[1], a[2], int(a[3]), int(a[4]), x, y):
                out.append("C" + " ".join(_f(v) for v in seg))
            cx, cy = x, y
            last_c = last_q = None
    return "".join(out)
