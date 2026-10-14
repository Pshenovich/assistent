"""Server-side SVG charts for template web-canvas decks.

Port of the approved layout catalog renderers. Every renderer takes a validated
chart dict plus the drawing size and returns an ``<svg>`` string. Colors come
from CSS custom properties defined in ``canvas-kit/templates.css``.
"""

from __future__ import annotations

import math
from html import escape
from typing import Any, Callable

C = ("var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)")
TXT = "var(--s-text)"
MUT = "var(--s-muted)"
FAINT = "var(--s-faint)"
RULE = "var(--s-rule)"
SURF = "var(--s-el)"

CHART_INTENTS = (
    "compare",
    "rank",
    "trend",
    "share",
    "structure",
    "bridge",
    "conversion",
    "target",
    "change",
    "matrix",
)


def nf(value: Any, dec: int = 0) -> str:
    """Russian number format: 12 345,6 and a real minus sign."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return escape(str(value))
    s = f"{abs(v):,.{dec}f}".replace(",", " ").replace(".", ",")
    return ("−" if v < 0 and round(abs(v), dec) != 0 else "") + s


def _num(v: Any) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f if math.isfinite(f) else 0.0


def nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(v))
    f = v / p
    n = 1 if f <= 1 else 2 if f <= 2 else 2.5 if f <= 2.5 else 5 if f <= 5 else 10
    return n * p


def _ticks(mx: float, n: int = 4) -> list[float]:
    return [mx / n * i for i in range(n + 1)]


def _r_top(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    r = max(0.0, min(r, h, w / 2))
    return (
        f"M{x:.1f},{y + h:.1f}V{y + r:.1f}Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f}"
        f"H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}V{y + h:.1f}Z"
    )


def _r_right(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    w = max(w, 0.0)
    r = max(0.0, min(r, w, h / 2))
    return (
        f"M{x:.1f},{y:.1f}H{x + w - r:.1f}Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f}"
        f"V{y + h - r:.1f}Q{x + w:.1f},{y + h:.1f} {x + w - r:.1f},{y + h:.1f}H{x:.1f}Z"
    )


def _svg(w: float, h: float, inner: str) -> str:
    return (
        f'<svg class="chart" viewBox="0 0 {w:.0f} {h:.0f}" width="{w:.0f}" '
        f'height="{h:.0f}" role="img">{inner}</svg>'
    )


def _t(
    x: float,
    y: float,
    s: Any,
    *,
    fs: int = 14,
    fill: str = MUT,
    a: str = "start",
    fw: int = 400,
    db: str = "",
) -> str:
    dbs = f' dominant-baseline="{db}"' if db else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{fs}" fill="{fill}" '
        f'text-anchor="{a}" font-weight="{fw}"{dbs}>{escape(str(s))}</text>'
    )


def _tip(s: str) -> str:
    return f"<title>{escape(s)}</title>"


# ---------------------------------------------------------------- renderers


def bar_v(d: dict[str, Any], W: float, H: float) -> str:
    labels, values = d["x"], [_num(v) for v in d["series"][0]["values"]]
    dec, unit, hi = int(d.get("dec") or 0), str(d.get("unit") or ""), d.get("highlight")
    top, bot, n = 34, 40, len(values)
    mx = nice_max(max(values + [0]))
    band = W / n
    bw = min(band * 0.58, 84)
    g = f'<line x1="0" x2="{W}" y1="{H - bot}" y2="{H - bot}" stroke="{RULE}"/>'
    for i, v in enumerate(values):
        h = (H - top - bot) * max(v, 0) / mx
        x, y = i * band + (band - bw) / 2, H - bot - h
        on = hi is None or hi == i
        fill = "var(--s-acc)" if on else "var(--c-mute)"
        tip = f"{labels[i]}: {nf(v, dec)} {unit}"
        g += f'<path d="{_r_top(x, y, bw, h)}" fill="{fill}">{_tip(tip)}</path>'
        g += _t(x + bw / 2, y - 10, nf(v, dec), a="middle", fill=TXT if on else MUT, fw=600, fs=15)
        g += _t(x + bw / 2, H - bot + 24, labels[i], a="middle")
    return _svg(W, H, g)


def bar_h(d: dict[str, Any], W: float, H: float) -> str:
    pairs = sorted(
        zip(d["x"], (_num(v) for v in d["series"][0]["values"])),
        key=lambda p: p[1],
        reverse=True,
    )
    unit, dec = str(d.get("unit") or ""), int(d.get("dec") or 0)
    lw, vw, n = 230, 80, len(pairs)
    mx = max([p[1] for p in pairs] + [1e-9])
    row = H / n
    bh = min(row * 0.56, 26)
    g = ""
    for i, (lab, v) in enumerate(pairs):
        y = i * row + (row - bh) / 2
        w = (W - lw - vw) * max(v, 0) / mx
        on = i == 0
        g += _t(lw - 14, y + bh / 2, lab, a="end", db="central", fill=TXT if on else MUT, fs=15)
        fill = "var(--s-acc)" if on else "var(--c1)"
        tip = f"{lab}: {nf(v, dec)}"
        g += f'<path d="{_r_right(lw, y, w, bh)}" fill="{fill}" opacity="{1 if on else 0.55}">{_tip(tip)}</path>'
        g += _t(lw + w + 10, y + bh / 2, nf(v, dec) + unit, db="central", fill=TXT if on else MUT, fw=600, fs=15)
    g += f'<line x1="{lw}" x2="{lw}" y1="0" y2="{H}" stroke="{RULE}"/>'
    return _svg(W, H, g)


def line(d: dict[str, Any], W: float, H: float) -> str:
    xs, series = d["x"], d["series"]
    dec, unit = int(d.get("dec") or 0), str(d.get("unit") or "")
    l, r, top, bot, n = 52, 150, 14, 34, len(xs)
    mx = nice_max(max(_num(v) for s in series for v in s["values"]))

    def X(i: int) -> float:
        return l + (W - l - r) * i / max(n - 1, 1)

    def Y(v: float) -> float:
        return H - bot - (H - top - bot) * v / mx

    g = ""
    for tv in _ticks(mx):
        g += f'<line x1="{l}" x2="{W - r}" y1="{Y(tv):.1f}" y2="{Y(tv):.1f}" stroke="{RULE}"/>'
        g += _t(l - 10, Y(tv), nf(tv, dec), a="end", db="central", fs=13, fill=FAINT)
    step = max(1, math.ceil(n / 12))
    for i, xl in enumerate(xs):
        if i % step == 0 or i == n - 1:
            g += _t(X(i), H - bot + 24, xl, a="middle", fs=13)
    ends = []
    for si, s in enumerate(series):
        col = "var(--s-acc)" if len(series) == 1 else C[si % 4]
        vals = [_num(v) for v in s["values"]]
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        g += f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        for i, v in enumerate(vals):
            tip = f"{s.get('name', '')} · {xs[i]}: {nf(v, dec)}{unit}"
            g += f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="12" fill="transparent">{_tip(tip)}</circle>'
        lv = vals[-1]
        g += f'<circle cx="{X(n - 1):.1f}" cy="{Y(lv):.1f}" r="5" fill="{col}" stroke="{SURF}" stroke-width="2"/>'
        ends.append({"y": Y(lv), "name": str(s.get("name") or ""), "v": lv})
    ends.sort(key=lambda e: e["y"])
    for i in range(1, len(ends)):
        if ends[i]["y"] - ends[i - 1]["y"] < 34:
            ends[i]["y"] = ends[i - 1]["y"] + 34
    for e in ends:
        g += _t(X(n - 1) + 14, e["y"] - 6, nf(e["v"], dec) + unit, fill=TXT, fw=600, fs=15, db="central")
        if len(series) > 1:
            g += _t(X(n - 1) + 14, e["y"] + 12, e["name"], fs=13, db="central")
    return _svg(W, H, g)


def area(d: dict[str, Any], W: float, H: float) -> str:
    xs = d["x"]
    vals = [_num(v) for v in d["series"][0]["values"]]
    dec, unit = int(d.get("dec") or 0), str(d.get("unit") or "")
    l, r, top, bot, n = 52, 110, 40, 34, len(vals)
    mx = nice_max(max(vals + [0]))

    def X(i: int) -> float:
        return l + (W - l - r) * i / max(n - 1, 1)

    def Y(v: float) -> float:
        return H - bot - (H - top - bot) * v / mx

    g = (
        '<defs><linearGradient id="lc-ag" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="var(--s-acc)" stop-opacity=".35"/>'
        '<stop offset="1" stop-color="var(--s-acc)" stop-opacity="0"/></linearGradient></defs>'
    )
    for tv in _ticks(mx):
        g += f'<line x1="{l}" x2="{W - r}" y1="{Y(tv):.1f}" y2="{Y(tv):.1f}" stroke="{RULE}"/>'
        g += _t(l - 10, Y(tv), nf(tv, dec), a="end", db="central", fs=13, fill=FAINT)
    every = max(1, math.ceil(n / 7))
    for i, xl in enumerate(xs):
        if i % every == 0 or i == n - 1:
            g += _t(X(i), H - bot + 24, xl, a="middle", fs=13)
    pts = [f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals)]
    g += f'<path d="M{X(0):.1f},{H - bot}L{"L".join(pts)}L{X(n - 1):.1f},{H - bot}Z" fill="url(#lc-ag)"/>'
    g += f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--s-acc)" stroke-width="2.5" stroke-linejoin="round"/>'
    for i, v in enumerate(vals):
        tip = f"{xs[i]}: {nf(v, dec)}{unit}"
        g += f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="12" fill="transparent">{_tip(tip)}</circle>'
    lv = vals[-1]
    g += f'<circle cx="{X(n - 1):.1f}" cy="{Y(lv):.1f}" r="6" fill="var(--s-acc)" stroke="{SURF}" stroke-width="2"/>'
    g += _t(X(n - 1) + 14, Y(lv) - 4, nf(lv, dec) + unit, fill=TXT, fw=700, fs=22, db="central")
    g += _t(X(n - 1) + 14, Y(lv) + 18, xs[-1], fs=13, db="central")
    return _svg(W, H, g)


def grouped(d: dict[str, Any], W: float, H: float) -> str:
    xs, series = d["x"], d["series"]
    dec, unit = int(d.get("dec") or 0), str(d.get("unit") or "")
    l, top, bot, n, m = 44, 10, 36, len(xs), len(series)
    mx = nice_max(max(_num(v) for s in series for v in s["values"]))
    band = (W - l) / n
    gw = band * 0.7
    bw = (gw - (m - 1) * 2) / m

    def Y(v: float) -> float:
        return H - bot - (H - top - bot) * v / mx

    g = ""
    for tv in _ticks(mx):
        g += f'<line x1="{l}" x2="{W}" y1="{Y(tv):.1f}" y2="{Y(tv):.1f}" stroke="{RULE}"/>'
        g += _t(l - 10, Y(tv), nf(tv, dec), a="end", db="central", fs=13, fill=FAINT)
    for i, xl in enumerate(xs):
        gx = l + i * band + (band - gw) / 2
        for si, s in enumerate(series):
            v = _num(s["values"][i])
            x, y = gx + si * (bw + 2), Y(max(v, 0))
            tip = f"{s.get('name', '')} · {xl}: {nf(v, dec)}{unit}"
            g += f'<path d="{_r_top(x, y, bw, H - bot - y)}" fill="{C[si % 4]}">{_tip(tip)}</path>'
        g += _t(gx + gw / 2, H - bot + 24, xl, a="middle")
    return _svg(W, H, g)


def stacked100(d: dict[str, Any], W: float, H: float) -> str:
    rows, names = d["rows"], d["series_names"]
    lw, n = 120, len(rows)
    row = H / n
    bh = min(row * 0.6, 40)
    g = ""
    for i, r in enumerate(rows):
        vals = [max(_num(v), 0) for v in r["values"]]
        tot = sum(vals) or 1
        y = i * row + (row - bh) / 2
        g += _t(lw - 14, y + bh / 2, r['label'], a="end", db="central", fill=TXT, fs=15)
        x = lw
        for si, v in enumerate(vals):
            w = (W - lw) * v / tot - 2
            pct = 100 * v / tot
            rx = 4 if si in (0, len(vals) - 1) else 0
            tip = f"{r['label']} · {names[si]}: {nf(pct)}%"
            g += f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{bh:.1f}" rx="{rx}" fill="{C[si % 4]}">{_tip(tip)}</rect>'
            if pct >= 9:
                g += _t(x + w / 2, y + bh / 2, nf(pct) + "%", a="middle", db="central", fill="var(--s-on-acc)", fw=600)
            x += w + 2
    return _svg(W, H, g)


def donut(d: dict[str, Any], W: float, H: float) -> str:
    labels = d["x"]
    vals = [max(_num(v), 0) for v in d["series"][0]["values"]]
    unit, dec = str(d.get("unit") or ""), int(d.get("dec") or 0)
    tot = sum(vals) or 1
    cx = cy = H / 2
    R = H / 2 - 6
    r0 = R * 0.62
    a0 = -math.pi / 2

    def pt(a: float, rr: float) -> str:
        return f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}"

    g = ""
    for i, v in enumerate(vals):
        a1 = a0 + 2 * math.pi * v / tot
        big = 1 if a1 - a0 > math.pi else 0
        if v >= tot:  # full ring: split in two halves so the arc is drawable
            a1 = a0 + math.pi * 1.9999
        tip = f"{labels[i]}: {nf(100 * v / tot)}%"
        g += (
            f'<path d="M{pt(a0, R)}A{R:.1f},{R:.1f} 0 {big} 1 {pt(a1, R)}L{pt(a1, r0)}'
            f'A{r0:.1f},{r0:.1f} 0 {big} 0 {pt(a0, r0)}Z" fill="{C[i % 4]}" stroke="{SURF}" '
            f'stroke-width="3">{_tip(tip)}</path>'
        )
        a0 = a1
    center = d.get("center") if isinstance(d.get("center"), dict) else None
    if center:
        g += _t(cx, cy - 8, center.get("value", ""), a="middle", db="central", fill=TXT, fw=800, fs=34)
        g += _t(cx, cy + 24, center.get('label', ''), a="middle", db="central")
    lx = H + 40
    y0 = H / 2 - ((len(labels) - 1) * 52 + 20) / 2 + 7
    for i, lab in enumerate(labels):
        y = y0 + i * 52
        g += f'<rect x="{lx:.1f}" y="{y - 7:.1f}" width="12" height="12" rx="3" fill="{C[i % 4]}"/>'
        g += _t(lx + 24, y, lab, db="central", fill=TXT, fs=16)
        g += _t(W, y, nf(100 * vals[i] / tot) + "%", a="end", db="central", fill=TXT, fw=700, fs=18)
        g += _t(lx + 24, y + 20, (nf(vals[i], dec) + " " + unit).strip(), db="central", fs=13)
    return _svg(W, H, g)


def waterfall(d: dict[str, Any], W: float, H: float) -> str:
    steps, unit, dec = d["steps"], str(d.get("unit") or ""), int(d.get("dec") or 0)
    top, bot, n = 34, 40, len(steps)
    run, hi = 0.0, 0.0
    bars = []
    for s in steps:
        v = _num(s.get("value"))
        if s.get("total"):
            a, b = 0.0, v
        else:
            a, b = run, run + v
        run = b
        hi = max(hi, a, b)
        bars.append({**s, "value": v, "a": a, "b": b})
    mx = nice_max(hi)
    band = W / n
    bw = min(band * 0.6, 90)

    def Y(v: float) -> float:
        return H - bot - (H - top - bot) * v / mx

    g = f'<line x1="0" x2="{W}" y1="{Y(0):.1f}" y2="{Y(0):.1f}" stroke="{RULE}"/>'
    for i, b in enumerate(bars):
        x = i * band + (band - bw) / 2
        y1, y2 = Y(max(b["a"], b["b"])), Y(min(b["a"], b["b"]))
        col = "var(--c-total)" if b.get("total") else ("var(--c1)" if b['value'] >= 0 else "var(--c4)")
        sign = "" if b.get("total") else ("+" if b['value'] > 0 else "")
        tip = f"{b.get('label', '')}: {sign}{nf(b['value'], dec)} {unit}"
        g += f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y2 - y1, 2):.1f}" rx="3" fill="{col}">{_tip(tip)}</rect>'
        if i < n - 1:
            nx = (i + 1) * band + (band - bw) / 2
            g += f'<line x1="{x + bw:.1f}" x2="{nx:.1f}" y1="{Y(b["b"]):.1f}" y2="{Y(b["b"]):.1f}" stroke="{FAINT}" stroke-dasharray="3 3"/>'
        lab = nf(b['value'], dec) if b.get("total") else ("+" if b['value'] > 0 else "−") + nf(abs(b['value']), dec)
        g += _t(x + bw / 2, y1 - 10, lab, a="middle", fill=TXT, fw=600, fs=15)
        g += _t(x + bw / 2, H - bot + 22, b.get('label', ''), a="middle", fs=13, fill=TXT if b.get("total") else MUT)
    return _svg(W, H, g)


def funnel(d: dict[str, Any], W: float, H: float) -> str:
    labels = d["x"]
    vals = [max(_num(v), 0) for v in d["series"][0]["values"]]
    warn = _num(d.get("warn_below") or 0)
    n = len(vals)
    row = H / n
    bh = row - 10
    mx = vals[0] or 1
    lw, cw = 210, 150
    aw = W - lw - cw
    g = ""
    for i, v in enumerate(vals):
        w = max(aw * v / mx, 4)
        x, y = lw + (aw - w) / 2, i * row + 5
        g += _t(lw - 16, y + bh / 2, labels[i], a="end", db="central", fill=TXT, fs=15)
        tip = f"{labels[i]}: {nf(v)}"
        g += f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{bh:.1f}" rx="4" fill="var(--s-acc)" opacity="{max(1 - i * 0.13, 0.3):.2f}">{_tip(tip)}</rect>'
        g += _t(lw + aw / 2, y + bh / 2, nf(v), a="middle", db="central", fill="var(--s-on-acc)", fw=700, fs=16)
        if i > 0 and vals[i - 1]:
            c = 100 * v / vals[i - 1]
            col = "var(--s-amber)" if warn and c < warn else TXT
            g += _t(W, y + bh / 2 - 8, nf(c) + "%", a="end", db="central", fill=col, fw=700, fs=17)
            g += _t(W, y + bh / 2 + 12, "от пред. этапа", a="end", db="central", fs=12)
    return _svg(W, H, g)


def bullet(d: dict[str, Any], W: float, H: float) -> str:
    rows, unit = d["rows"], str(d.get("unit") or "")
    lw, vw, n, bh = 220, 170, len(rows), 18
    row = H / n
    mx = max(max(_num(r["fact"]), _num(r["plan"])) for r in rows) * 1.1 or 1

    def X(v: float) -> float:
        return lw + (W - lw - vw) * v / mx

    g = ""
    for i, r in enumerate(rows):
        fact, plan = _num(r["fact"]), _num(r["plan"])
        y = i * row + (row - bh) / 2
        pct = 100 * fact / plan if plan else 0
        ok = pct >= 100
        g += _t(lw - 16, y + bh / 2, r['label'], a="end", db="central", fill=TXT, fs=15)
        g += f'<rect x="{lw}" y="{y - 6:.1f}" width="{X(mx) - lw:.1f}" height="{bh + 12}" rx="4" fill="var(--s-tint)"/>'
        tip = f"{r['label']}: факт {nf(fact)} / план {nf(plan)}"
        g += f'<path d="{_r_right(lw, y, X(fact) - lw, bh)}" fill="{"var(--s-acc)" if ok else "var(--c1)"}">{_tip(tip)}</path>'
        g += f'<line x1="{X(plan):.1f}" x2="{X(plan):.1f}" y1="{y - 9:.1f}" y2="{y + bh + 9:.1f}" stroke="{TXT}" stroke-width="2.5"/>'
        g += _t(W, y + bh / 2 - 8, nf(pct) + "% плана", a="end", db="central", fill="var(--s-acc)" if ok else "var(--s-amber)", fw=700, fs=15)
        g += _t(W, y + bh / 2 + 12, f"{nf(fact)} из {nf(plan)}{unit}", a="end", db="central", fs=12)
    return _svg(W, H, g)


def dumbbell(d: dict[str, Any], W: float, H: float) -> str:
    rows, unit, dec = d["rows"], str(d.get("unit") or ""), int(d.get("dec") or 0)
    a_name, b_name = str(d.get("a_name") or "До"), str(d.get("b_name") or "После")
    lw, vw, n = 240, 100, len(rows)
    row = (H - 26) / n
    mx = nice_max(max(max(_num(r["a"]), _num(r["b"])) for r in rows))

    def X(v: float) -> float:
        return lw + (W - lw - vw) * v / mx

    g = ""
    for tv in (0, mx / 2, mx):
        g += f'<line x1="{X(tv):.1f}" x2="{X(tv):.1f}" y1="0" y2="{H - 26}" stroke="{RULE}"/>'
        g += _t(X(tv), H - 6, nf(tv, dec) + unit, a="middle", fs=12, fill=FAINT)
    for i, r in enumerate(rows):
        a, b = _num(r["a"]), _num(r["b"])
        y = i * row + row / 2
        g += _t(lw - 16, y, r['label'], a="end", db="central", fill=TXT, fs=15)
        g += f'<line x1="{X(a):.1f}" x2="{X(b):.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--c1)" stroke-width="3" opacity=".5"/>'
        tip = f"{r['label']} · {a_name}: {nf(a, dec)}"
        g += f'<circle cx="{X(a):.1f}" cy="{y:.1f}" r="7" fill="{SURF}" stroke="var(--c-total)" stroke-width="2.5">{_tip(tip)}</circle>'
        tip = f"{r['label']} · {b_name}: {nf(b, dec)}"
        g += f'<circle cx="{X(b):.1f}" cy="{y:.1f}" r="8" fill="var(--s-acc)" stroke="{SURF}" stroke-width="2">{_tip(tip)}</circle>'
        g += _t(max(X(a), X(b)) + 14, y, f"{nf(a, dec)} → {nf(b, dec)}", db="central", fill=TXT, fw=600, fs=14)
    return _svg(W, H, g)


def heatmap(d: dict[str, Any], W: float, H: float) -> str:
    """HTML table (not SVG): values are text, background is one-hue sequential."""
    rows, cols, values = d["rows"], d["cols"], d["values"]
    unit, dec = str(d.get("unit") or ""), int(d.get("dec") or 0)
    flat = [_num(v) for r in values for v in r]
    lo, hi = min(flat), max(flat)
    span = (hi - lo) or 1

    def cell(v: float) -> str:
        a = (v - lo) / span
        bg = f"color-mix(in srgb, var(--s-acc) {(0.08 + a * 0.62) * 100:.0f}%, transparent)"
        fg = "var(--s-on-acc)" if a > 0.7 else "var(--s-text)"
        return f'<td class="hm-cell" style="background:{bg};color:{fg}" title="{escape(nf(v, dec) + unit)}">{nf(v, dec)}{escape(unit)}</td>'

    head = "".join(f"<th>{escape(str(c))}</th>" for c in cols)
    body = "".join(
        f'<tr><td class="hm-row">{escape(str(r))}</td>{"".join(cell(_num(v)) for v in values[i])}</tr>'
        for i, r in enumerate(rows)
    )
    return (
        f'<div class="hm"><table class="t hm-t"><thead><tr><th></th>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
        f'<div class="hm-scale cap"><span>{nf(lo, dec)}{escape(unit)}</span><i></i>'
        f"<span>{nf(hi, dec)}{escape(unit)}</span></div></div>"
    )


def spark(values: list[Any], W: float = 200, H: float = 44) -> str:
    vals = [_num(v) for v in values]
    mx, mn, n = max(vals), min(vals), len(vals)
    rng = (mx - mn) or 1

    def X(i: int) -> float:
        return 2 + (W - 8) * i / max(n - 1, 1)

    def Y(v: float) -> float:
        return H - 4 - (H - 10) * (v - mn) / rng

    pts = [f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals)]
    return (
        f'<svg class="spark" viewBox="0 0 {W:.0f} {H:.0f}" width="100%" height="{H:.0f}" '
        f'preserveAspectRatio="none"><path d="M{X(0):.1f},{H:.0f}L{"L".join(pts)}L{X(n - 1):.1f},{H:.0f}Z" '
        f'fill="var(--s-acc-soft)"/><polyline points="{" ".join(pts)}" fill="none" '
        f'stroke="var(--s-acc)" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{X(n - 1):.1f}" cy="{Y(vals[-1]):.1f}" r="3.5" fill="var(--s-acc)"/></svg>'
    )


RENDERERS: dict[str, Callable[[dict[str, Any], float, float], str]] = {
    "bar-v": bar_v,
    "bar-h": bar_h,
    "line": line,
    "area": area,
    "grouped": grouped,
    "stacked100": stacked100,
    "donut": donut,
    "waterfall": waterfall,
    "funnel": funnel,
    "bullet": bullet,
    "dumbbell": dumbbell,
    "heatmap": heatmap,
}


# ---------------------------------------------------------------- selection


def _series(chart: dict[str, Any]) -> list[dict[str, Any]]:
    s = chart.get("series")
    return [x for x in s if isinstance(x, dict) and isinstance(x.get("values"), list)] if isinstance(s, list) else []


def chart_types_for(chart: dict[str, Any]) -> list[tuple[str, str]]:
    """All chart types this data can be drawn as: [(type, reason_if_unavailable)].

    An empty reason means the type is available.
    """
    out: list[tuple[str, str]] = []
    xs = chart.get("x") if isinstance(chart.get("x"), list) else []
    ser = _series(chart)
    n_ser = len(ser)
    lens_ok = bool(ser) and all(len(s["values"]) == len(xs) for s in ser) and len(xs) > 0
    long_labels = any(len(str(x)) > 12 for x in xs)

    def add(t: str, ok: bool, why: str) -> None:
        out.append((t, "" if ok else why))

    add("bar-v", lens_ok and n_ser == 1 and len(xs) <= 8 and not long_labels, "нужен 1 ряд до 8 коротких категорий")
    add("bar-h", lens_ok and n_ser == 1 and len(xs) <= 12, "нужен 1 ряд до 12 категорий")
    add("line", lens_ok and 1 <= n_ser <= 3 and len(xs) >= 4, "нужно 1–3 ряда от 4 точек")
    add("area", lens_ok and n_ser == 1 and len(xs) >= 6, "нужен 1 ряд от 6 точек")
    add("grouped", lens_ok and 2 <= n_ser <= 3 and len(xs) <= 6, "нужно 2–3 ряда до 6 категорий")
    add("donut", lens_ok and n_ser == 1 and 2 <= len(xs) <= 5, "нужно 2–5 частей одного целого")
    funnel_ok = lens_ok and n_ser == 1 and 3 <= len(xs) <= 6
    if funnel_ok:
        v = [_num(x) for x in ser[0]["values"]]
        funnel_ok = all(v[i] >= v[i + 1] for i in range(len(v) - 1))
    add("funnel", funnel_ok, "нужно 3–6 этапов с невозрастающими значениями")
    rows = chart.get("rows") if isinstance(chart.get("rows"), list) else []
    add(
        "stacked100",
        bool(rows) and 2 <= len(rows) <= 6 and all(isinstance(r, dict) and isinstance(r.get("values"), list) for r in rows) and bool(chart.get("series_names")),
        "нужны группы с долями частей",
    )
    add(
        "bullet",
        bool(rows) and 2 <= len(rows) <= 6 and all(isinstance(r, dict) and "fact" in r and "plan" in r for r in rows),
        "нужны строки с фактом и планом",
    )
    add(
        "dumbbell",
        bool(rows) and 3 <= len(rows) <= 8 and all(isinstance(r, dict) and "a" in r and "b" in r for r in rows),
        "нужны пары значений до/после",
    )
    steps = chart.get("steps") if isinstance(chart.get("steps"), list) else []
    add("waterfall", 3 <= len(steps) <= 8, "нужны шаги от начального значения к итогу")
    add(
        "heatmap",
        isinstance(chart.get("values"), list) and bool(chart.get("cols")) and bool(chart.get("rows")) and all(isinstance(r, list) for r in chart.get("values") or []),
        "нужна матрица строк и столбцов",
    )
    return out


_INTENT_ORDER = {
    "compare": ("bar-v", "grouped", "bar-h"),
    "rank": ("bar-h", "bar-v"),
    "trend": ("area", "line", "bar-v", "grouped"),
    "share": ("donut", "bar-h"),
    "structure": ("stacked100",),
    "bridge": ("waterfall",),
    "conversion": ("funnel", "bar-h"),
    "target": ("bullet",),
    "change": ("dumbbell",),
    "matrix": ("heatmap",),
}


def pick_chart_type(chart: dict[str, Any]) -> str | None:
    """Pick the chart type from intent + data shape. None if nothing fits."""
    avail = {t for t, why in chart_types_for(chart) if not why}
    want = str(chart.get("type") or "").strip()
    if want in avail:
        return want
    intent = str(chart.get("intent") or "").strip().lower()
    order = _INTENT_ORDER.get(intent, ())
    ser = _series(chart)
    for t in order:
        # Trend with several series → lines, not area.
        if t == "area" and len(ser) != 1:
            continue
        if t in avail:
            return t
    for t in ("bar-v", "bar-h", "line", "grouped", "stacked100", "bullet", "dumbbell", "waterfall", "funnel", "donut", "heatmap"):
        if t in avail:
            return t
    return None


def render_chart(chart: dict[str, Any], W: float, H: float) -> str:
    t = pick_chart_type(chart)
    if not t:
        return '<p class="body">Данных для графика недостаточно.</p>'
    data = dict(chart)
    if t == "stacked100":
        data["series_names"] = [str(x) for x in chart.get("series_names") or []]
    return RENDERERS[t](data, W, H)


def legend_names(chart: dict[str, Any]) -> list[str]:
    """Series names that need a legend box (≥2 series, not direct-labelled)."""
    t = pick_chart_type(chart)
    if t == "grouped":
        return [str(s.get("name") or "") for s in _series(chart)]
    if t == "stacked100":
        return [str(x) for x in chart.get("series_names") or []]
    return []
