"""Данные виджетов дашборда: сырые точки/строки → готовая к рисованию модель.

Один и тот же расчёт видят приложение (рисует SVG), MCP (read_dataset / get_dashboard) и Лео
(отвечает на вопросы по цифрам). Время точек хранится в UTC; дни/недели/месяцы считаются в часовом
поясе зрителя. Точки «на весь день» (прислана дата без времени) относятся к своей дате в любом поясе.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from assistant.lib import dashboard_widgets as W
from assistant.stores import dashboards as store

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

DEFAULT_TZ = "Europe/Moscow"
PERIOD_DAYS = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
OTHER = "Прочее"
WEEKDAYS = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def _tz(name: Any):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(str(name or DEFAULT_TZ))
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


class Ctx:
    def __init__(self, period: str = W.DEFAULT_PERIOD, tz: Any = DEFAULT_TZ, now: Optional[datetime] = None):
        self.period = period if period in PERIOD_DAYS else W.DEFAULT_PERIOD
        self.tz = _tz(tz)
        self.now = (now or datetime.now(timezone.utc)).astimezone(self.tz)
        self.today = self.now.date()

    def range(self, period: Optional[str] = None) -> tuple[date, date]:
        """[начало, конец) периода в локальных датах: последние N дней, включая сегодня."""
        days = PERIOD_DAYS[period if period in PERIOD_DAYS else self.period]
        end = self.today + timedelta(days=1)
        return end - timedelta(days=days), end

    def prev_range(self, period: Optional[str] = None) -> tuple[date, date]:
        a, b = self.range(period)
        n = b - a
        return a - n, a


def _period_of(ctx: Ctx, opts: dict[str, Any]) -> str:
    p = opts.get("period") or "dash"
    return ctx.period if p == "dash" else p


def _local(ctx: Ctx, p: dict[str, Any]) -> datetime:
    if p.get("all_day"):
        d = date.fromisoformat(p["ts"][:10])
        return datetime(d.year, d.month, d.day, 12, tzinfo=ctx.tz)
    t = datetime.fromisoformat(p["ts"]).replace(tzinfo=timezone.utc)
    return t.astimezone(ctx.tz)


def _utc_bound(ctx: Ctx, d: date, pad_days: int) -> str:
    t = datetime(d.year, d.month, d.day, tzinfo=ctx.tz) + timedelta(days=pad_days)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _load(ctx: Ctx, ds_id: int, a: date, b: date, series: Optional[list[str]] = None) -> list[tuple[datetime, str, float]]:
    raw = store.points(ds_id, _utc_bound(ctx, a, -1), _utc_bound(ctx, b, 1), series or None)
    out = []
    for p in raw:
        t = _local(ctx, p)
        if a <= t.date() < b:
            out.append((t, p["series"], p["value"]))
    return out


def _agg(name: str, vals: list[float]) -> Optional[float]:
    if not vals:
        return 0.0 if name in ("sum", "count") else None
    if name == "sum":
        return float(sum(vals))
    if name == "avg":
        return float(sum(vals)) / len(vals)
    if name == "min":
        return float(min(vals))
    if name == "max":
        return float(max(vals))
    if name == "count":
        return float(len(vals))
    return float(vals[-1])  # last


def _step(ctx: Ctx, period: str, want: str) -> str:
    if want == "hour" and PERIOD_DAYS.get(period, 30) <= 7:
        return "hour"
    if want in ("day", "week", "month"):
        return want
    return {"7d": "day", "30d": "day", "90d": "week", "365d": "month"}.get(period, "day")


def _bucket(step: str, t: datetime) -> str:
    if step == "hour":
        return t.strftime("%Y-%m-%dT%H")
    if step == "week":
        monday = t.date() - timedelta(days=t.weekday())
        return monday.isoformat()
    if step == "month":
        return t.strftime("%Y-%m")
    return t.date().isoformat()


def _buckets(step: str, a: date, b: date) -> list[str]:
    out: list[str] = []
    if step == "hour":
        d = a
        while d < b:
            out += [f"{d.isoformat()}T{h:02d}" for h in range(24)]
            d += timedelta(days=1)
        return out
    if step == "week":
        d = a - timedelta(days=a.weekday())
        while d < b:
            out.append(d.isoformat())
            d += timedelta(days=7)
        return out
    if step == "month":
        y, m = a.year, a.month
        while date(y, m, 1) < b:
            out.append(f"{y:04d}-{m:02d}")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out
    d = a
    while d < b:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def bucket_label(step: str, key: str) -> str:
    try:
        if step == "hour":
            d = date.fromisoformat(key[:10])
            return f"{d.day} {MONTHS[d.month - 1]} {key[11:13]}:00"
        if step == "month":
            y, m = key.split("-")
            return f"{MONTHS[int(m) - 1]} {y}"
        d = date.fromisoformat(key)
        return f"{d.day} {MONTHS[d.month - 1]}" + (" (нед.)" if step == "week" else "")
    except Exception:
        return key


def _series_list(ds: dict[str, Any], wanted: list[str], limit: int) -> list[str]:
    if wanted:
        return wanted[:limit]
    names = store.series_names(ds["id"])
    return names[:limit] or [""]


def _series_label(ds: dict[str, Any], s: str) -> str:
    return s or ds.get("title") or ds.get("key") or "Значение"


def _timeline(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any], limit: int) -> dict[str, Any]:
    period = _period_of(ctx, opts)
    step = _step(ctx, period, opts.get("step") or "auto")
    a, b = ctx.range(period)
    names = _series_list(ds, list(opts.get("series") or []), limit)
    pts = _load(ctx, ds["id"], a, b, [n for n in names if n] if any(names) else None)
    keys = _buckets(step, a, b)
    agg = opts.get("agg") or "sum"
    grid: dict[str, dict[str, list[float]]] = {n: defaultdict(list) for n in names}
    for t, s, v in pts:
        if s in grid:
            grid[s][_bucket(step, t)].append(v)
    series = []
    for n in names:
        vals = [_agg(agg, grid[n].get(k, [])) if (agg in ("sum", "count") or grid[n].get(k)) else None for k in keys]
        series.append({"name": _series_label(ds, n), "values": vals})
    return {
        "step": step,
        "keys": keys,
        "labels": [bucket_label(step, k) for k in keys],
        "series": series,
        "period": period,
    }


def _fmt(opts: dict[str, Any], ds: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {"format": opts.get("format") or "number", "unit": opts.get("unit") or (ds or {}).get("unit") or ""}


def _metric_value(ctx: Ctx, ds: dict[str, Any], agg: str, series: str, a: date, b: date) -> tuple[Optional[float], int]:
    pts = _load(ctx, ds["id"], a, b, [series] if series else None)
    if not series and len({s for _t, s, _v in pts}) > 1 and agg == "last":
        # «последнее» по нескольким сериям — сумма последних значений каждой серии
        last: dict[str, float] = {}
        for _t, s, v in pts:
            last[s] = v
        return (float(sum(last.values())) if last else None), len(pts)
    return _agg(agg, [v for _t, _s, v in pts]), len(pts)


def _number(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    period = _period_of(ctx, opts)
    agg = opts.get("agg") or "sum"
    series = opts.get("series") or ""
    a, b = ctx.range(period)
    pa, pb = ctx.prev_range(period)
    value, n = _metric_value(ctx, ds, agg, series, a, b)
    prev, pn = _metric_value(ctx, ds, agg, series, pa, pb)
    if agg == "last" and value is None:
        # нет точек в периоде — последнее известное значение
        before = store.points(ds["id"], "", _utc_bound(ctx, b, 0), [series] if series else None)
        value = before[-1]["value"] if before else None
    delta = None
    if value is not None and prev not in (None, 0) and pn:
        delta = (value - prev) / abs(prev) * 100.0
    out = {"value": value, "prev": prev if pn else None, "delta_pct": delta, "good": opts.get("good") or "up",
           "period": period, "empty": n == 0 and value is None, **_fmt(opts, ds)}
    if opts.get("spark", True):
        tl = _timeline(ctx, ds, {"period": period, "agg": agg, "series": [series] if series else []}, 1)
        out["spark"] = tl["series"][0]["values"] if tl["series"] else []
    return out


def _goal(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    gp = opts.get("goal_period") or "month"
    t = ctx.today
    if gp == "month":
        a = t.replace(day=1)
        b = (a + timedelta(days=32)).replace(day=1)
    elif gp == "quarter":
        qm = (t.month - 1) // 3 * 3 + 1
        a = date(t.year, qm, 1)
        b = date(t.year + (1 if qm == 10 else 0), 1 if qm == 10 else qm + 3, 1)
    elif gp == "year":
        a, b = date(t.year, 1, 1), date(t.year + 1, 1, 1)
    else:
        a, b = ctx.range()
    value, _n = _metric_value(ctx, ds, opts.get("agg") or "sum", opts.get("series") or "", a, b)
    target = float(opts.get("target") or 0)
    pct = (value or 0) / target * 100.0 if target else None
    total_days = (b - a).days
    passed = max(0, min(total_days, (t - a).days + 1))
    expected = passed / total_days * 100.0 if total_days else None
    return {"value": value or 0.0, "target": target, "pct": pct, "days_left": max(0, (b - t).days - 1),
            "on_pace": (pct is not None and expected is not None and pct + 0.01 >= expected),
            "goal_period": gp, **_fmt(opts, ds)}


def _by_series(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> list[tuple[str, float]]:
    period = _period_of(ctx, opts)
    a, b = ctx.range(period)
    agg = opts.get("agg") or "sum"
    grp: dict[str, list[float]] = defaultdict(list)
    for _t, s, v in _load(ctx, ds["id"], a, b):
        grp[s].append(v)
    items = [(_series_label(ds, s), _agg(agg, v) or 0.0) for s, v in grp.items()]
    items.sort(key=lambda x: -x[1])
    return items


def _by_rows(ds: dict[str, Any], label_f: str, value_f: str) -> list[tuple[str, float]]:
    acc: dict[str, float] = {}
    for r in store.rows(ds["id"]):
        lab = str(r.get(label_f) if r.get(label_f) is not None else "—")[:80]
        v = store._num(r.get(value_f))  # type: ignore[attr-defined]
        if v is None:
            continue
        acc[lab] = acc.get(lab, 0.0) + v
    items = list(acc.items())
    items.sort(key=lambda x: -x[1])
    return items


def _pairs(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> list[tuple[str, float]]:
    if ds["kind"] == "table":
        return _by_rows(ds, opts["label_field"], opts["value_field"])
    return _by_series(ctx, ds, opts)


def _ranking(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    items = _pairs(ctx, ds, opts)[: int(opts.get("top") or 10)]
    return {"items": [{"label": l, "value": v} for l, v in items], **_fmt(opts, ds)}


def _share(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    items = [(l, v) for l, v in _pairs(ctx, ds, opts) if v > 0]
    n = int(opts.get("max_parts") or 6)
    if len(items) > n:
        rest = sum(v for _l, v in items[n - 1:])
        items = items[: n - 1] + [(OTHER, rest)]
    total = sum(v for _l, v in items)
    return {"items": [{"label": l, "value": v, "pct": (v / total * 100.0 if total else 0.0)} for l, v in items],
            "total": total, **_fmt(opts, ds)}


def _sparkgrid(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    top = _by_series(ctx, ds, opts)[: int(opts.get("top") or 8)]
    raw_names = {_series_label(ds, s): s for s in store.series_names(ds["id"])}
    tl = _timeline(ctx, ds, {**opts, "series": [raw_names.get(l, l) for l, _v in top]}, 15)
    spark = {s["name"]: s["values"] for s in tl["series"]}
    return {"items": [{"label": l, "value": v, "spark": spark.get(l, [])} for l, v in top], **_fmt(opts, ds)}


def _heatmap(ctx: Ctx, ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    period = _period_of(ctx, opts)
    a, b = ctx.range(period)
    agg = opts.get("agg") or "sum"
    pts = _load(ctx, ds["id"], a, b)
    cells: dict[tuple[int, int], list[float]] = defaultdict(list)
    if (opts.get("layout") or "weekday_hour") == "weekday_hour":
        rows, cols = list(WEEKDAYS), [f"{h:02d}" for h in range(24)]
        for t, _s, v in pts:
            cells[(t.weekday(), t.hour)].append(v)
    else:
        names = store.series_names(ds["id"])[:12] or [""]
        days = _buckets("day", a, b)
        rows = [_series_label(ds, s) for s in names]
        cols = [bucket_label("day", d) for d in days]
        idx = {d: i for i, d in enumerate(days)}
        for t, s, v in pts:
            if s in names and t.date().isoformat() in idx:
                cells[(names.index(s), idx[t.date().isoformat()])].append(v)
    matrix = [[(_agg(agg, cells.get((r, c), [])) if cells.get((r, c)) else None) for c in range(len(cols))]
              for r in range(len(rows))]
    mx = max([v for row in matrix for v in row if v is not None] or [0])
    return {"rows": rows, "cols": cols, "matrix": matrix, "max": mx, **_fmt(opts, ds)}


def _num(v: Any) -> Optional[float]:
    return store._num(v)  # type: ignore[attr-defined]


def _col_labels(ds: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in (ds.get("schema") or {}).get("columns") or []}


def _funnel(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    out = []
    first = None
    prev = None
    for r in store.rows(ds["id"], 30):
        v = _num(r.get(opts["value_field"]))
        if v is None:
            continue
        first = v if first is None else first
        out.append({"label": str(r.get(opts["label_field"]) or "—")[:60], "value": v,
                    "from_prev": (v / prev * 100.0 if prev else None),
                    "from_first": (v / first * 100.0 if first else None)})
        prev = v
    return {"items": out, "show_conversion": bool(opts.get("show_conversion", True))}


def _grouped(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    labels = _col_labels(ds)
    fields = list(opts["value_fields"])
    cats = []
    for r in store.rows(ds["id"], 40):
        cats.append({"label": str(r.get(opts["label_field"]) or "—")[:40],
                     "values": [_num(r.get(f)) for f in fields]})
    return {"fields": [{"name": f, "label": labels.get(f, {}).get("label") or f} for f in fields],
            "categories": cats, "orientation": opts.get("orientation") or "vertical", **_fmt(opts, ds)}


def _bullet(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    out = []
    for r in store.rows(ds["id"], 30):
        fact, plan = _num(r.get(opts["fact_field"])), _num(r.get(opts["plan_field"]))
        if fact is None:
            continue
        pct = fact / plan * 100.0 if plan else None
        level = "ok"
        if pct is not None and pct < opts["bad_below"]:
            level = "bad"
        elif pct is not None and pct < opts["warn_below"]:
            level = "warn"
        out.append({"label": str(r.get(opts["label_field"]) or "—")[:60], "fact": fact, "plan": plan,
                    "pct": pct, "level": level})
    return {"items": out, **_fmt(opts, ds)}


def _waterfall(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    run = 0.0
    steps = []
    for r in store.rows(ds["id"], 30):
        v = _num(r.get(opts["value_field"]))
        if v is None:
            continue
        steps.append({"label": str(r.get(opts["label_field"]) or "—")[:40], "value": v, "start": run, "end": run + v})
        run += v
    steps.append({"label": opts.get("total_label") or "Итог", "value": run, "start": 0.0, "end": run, "total": True})
    return {"steps": steps, **_fmt(opts, ds)}


def _scatter(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    pts = []
    for r in store.rows(ds["id"], 500):
        x, y = _num(r.get(opts["x_field"])), _num(r.get(opts["y_field"]))
        if x is None or y is None:
            continue
        lab = str(r.get(opts["label_field"]) or "")[:60]
        pts.append({"label": lab, "x": x, "y": y, "hl": bool(opts.get("highlight")) and lab == opts.get("highlight")})
    return {"points": pts, "x_label": opts.get("x_label") or opts["x_field"],
            "y_label": opts.get("y_label") or opts["y_field"]}


def _table(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    rows = store.rows(ds["id"], 1000)
    cols = opts.get("columns") or []
    if not cols:
        seen: list[str] = []
        for r in rows[:50]:
            for k in r:
                if k not in seen:
                    seen.append(k)
        cols = [{"name": k, "label": k, "type": "number" if all(isinstance(r.get(k), (int, float)) or r.get(k) is None
                                                               for r in rows[:50]) else "text"} for k in seen[:12]]
    names = [c["name"] for c in cols]
    totals = {}
    for f in opts.get("totals") or []:
        totals[f] = sum(v for v in (_num(r.get(f)) for r in rows) if v is not None)
    return {"columns": cols, "rows": [{k: r.get(k) for k in names} for r in rows], "total_rows": len(rows),
            "totals": totals, "sort_field": opts.get("sort_field") or "", "sort_dir": opts.get("sort_dir") or "desc",
            "search": bool(opts.get("search", True)), "status_filter": opts.get("status_filter") or "",
            "limit": int(opts.get("limit") or 50)}


def _pivot(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    acc: dict[tuple[str, str], list[float]] = defaultdict(list)
    rlab: list[str] = []
    clab: list[str] = []
    for r in store.rows(ds["id"]):
        rk, ck = str(r.get(opts["row_field"]) or "—")[:40], str(r.get(opts["col_field"]) or "—")[:40]
        v = _num(r.get(opts["value_field"]))
        if v is None:
            continue
        if rk not in rlab:
            rlab.append(rk)
        if ck not in clab:
            clab.append(ck)
        acc[(rk, ck)].append(v)
    rlab, clab = rlab[:40], clab[:24]
    agg = opts.get("agg") or "sum"
    m = [[(_agg(agg, acc[(r, c)]) if acc.get((r, c)) else None) for c in clab] for r in rlab]
    allv = [v for row in m for v in row if v is not None]
    return {"rows": rlab, "cols": clab, "matrix": m,
            "row_totals": [sum(v for v in row if v is not None) for row in m],
            "col_totals": [sum(m[i][j] for i in range(len(rlab)) if m[i][j] is not None) for j in range(len(clab))],
            "max": max(allv) if allv else 0.0, "totals_meaningful": agg in ("sum", "count"), **_fmt(opts, ds)}


def _cohort(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    acc: dict[tuple[str, int], float] = {}
    cohorts: list[str] = []
    steps: set[int] = set()
    for r in store.rows(ds["id"]):
        c = str(r.get(opts["cohort_field"]) or "—")[:30]
        s = _num(r.get(opts["step_field"]))
        v = _num(r.get(opts["value_field"]))
        if s is None or v is None:
            continue
        if c not in cohorts:
            cohorts.append(c)
        steps.add(int(s))
        acc[(c, int(s))] = v
    st = sorted(steps)[:24]
    cohorts = cohorts[:36]
    return {"cohorts": cohorts, "steps": st, "matrix": [[acc.get((c, s)) for s in st] for c in cohorts]}


_STATE = {"ok": "ok", "good": "ok", "up": "ok", "green": "ok", "true": "ok", "1": "ok",
          "warn": "warn", "warning": "warn", "yellow": "warn", "slow": "warn",
          "fail": "fail", "error": "fail", "down": "fail", "red": "fail", "false": "fail", "0": "fail", "bad": "fail"}


def _status(ds: dict[str, Any], opts: dict[str, Any]) -> dict[str, Any]:
    out = []
    for r in store.rows(ds["id"], 60):
        st = _STATE.get(str(r.get(opts["state_field"])).strip().lower(), "warn")
        out.append({"label": str(r.get(opts["label_field"]) or "—")[:60], "state": st,
                    "note": str(r.get(opts.get("note_field") or "") or "")[:80] if opts.get("note_field") else ""})
    return {"items": out}


def _kpi_row(ctx: Ctx, dash: dict[str, Any], opts: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    out = []
    for it in opts.get("items") or []:
        ds = by_key.get(it["dataset"])
        if not ds:
            out.append({"label": it.get("label"), "missing": True})
            continue
        v = _number(ctx, ds, {**it, "period": opts.get("period") or "dash", "spark": False})
        out.append({"label": it.get("label"), **v})
    return {"items": out}


def widget_view(ctx: Ctx, dash: dict[str, Any], w: dict[str, Any], by_id: dict[int, dict[str, Any]],
                by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Модель одного виджета для рисования. Ошибка в данных — {"error": "..."} вместо падения всего дашборда."""
    k = w["kind"]
    opts = w.get("options") or {}
    ds = by_id.get(w.get("dataset_id")) if w.get("dataset_id") else None
    base = {"id": w["id"], "kind": k, "title": w.get("title") or (ds or {}).get("title") or W.WIDGET_TYPES.get(k, {}).get("label", k),
            "width": w.get("width") or 1, "dataset": (ds or {}).get("key"), "options": opts}
    try:
        if W.WIDGET_TYPES.get(k, {}).get("sources") and not ds:
            return {**base, "error": "Источник удалён"}
        fn: dict[str, Callable[[], dict[str, Any]]] = {
            "number": lambda: _number(ctx, ds, opts),
            "goal": lambda: _goal(ctx, ds, opts),
            "line": lambda: {**_timeline(ctx, ds, opts, 5), "target": opts.get("target"), **_fmt(opts, ds)},
            "bars": lambda: {**_timeline(ctx, ds, opts, 5), "target": opts.get("target"),
                             "stacked": bool(opts.get("stacked")), "highlight_last": bool(opts.get("highlight_last", True)),
                             **_fmt(opts, ds)},
            "area": lambda: {**_timeline(ctx, ds, opts, 5), "stacked": bool(opts.get("stacked")), **_fmt(opts, ds)},
            "stacked100": lambda: _timeline(ctx, ds, {**opts, "agg": "sum"}, 6),
            "ranking": lambda: _ranking(ctx, ds, opts),
            "share": lambda: _share(ctx, ds, opts),
            "sparkgrid": lambda: _sparkgrid(ctx, ds, opts),
            "heatmap": lambda: _heatmap(ctx, ds, opts),
            "kpi_row": lambda: _kpi_row(ctx, dash, opts, by_key),
            "funnel": lambda: _funnel(ds, opts),
            "grouped": lambda: _grouped(ds, opts),
            "bullet": lambda: _bullet(ds, opts),
            "waterfall": lambda: _waterfall(ds, opts),
            "scatter": lambda: _scatter(ds, opts),
            "table": lambda: _table(ds, opts),
            "pivot": lambda: _pivot(ds, opts),
            "cohort": lambda: _cohort(ds, opts),
            "status": lambda: _status(ds, opts),
            "feed": lambda: {"items": store.events(ds["id"], int(opts.get("limit") or 10))},
            "text": lambda: {"text": opts.get("text") or ""},
        }
        if k not in fn:
            return {**base, "error": "Неизвестный тип виджета"}
        return {**base, "data": fn[k]()}
    except Exception as e:
        print(f"[dashboards] widget={w.get('id')} kind={k} err={e!r}")
        return {**base, "error": "Не удалось посчитать — проверьте данные источника"}


def dataset_public(ds: dict[str, Any], now: Optional[datetime] = None) -> dict[str, Any]:
    return {k: ds[k] for k in ("id", "key", "kind", "title", "unit", "schema", "expected_every_min", "key_hint",
                               "last_push_at", "last_push_count", "last_error", "last_error_at", "created_at")} | {
        "stale": store.stale(ds, now), "count": store.counts(ds["id"], ds["kind"]),
    }


def dashboard_view(dash: dict[str, Any], *, period: str = W.DEFAULT_PERIOD, tz: Any = DEFAULT_TZ,
                   now: Optional[datetime] = None, with_data: bool = True) -> dict[str, Any]:
    ctx = Ctx(period, tz, now)
    dss = store.datasets(dash["id"])
    by_id = {d["id"]: d for d in dss}
    by_key = {d["key"]: d for d in dss}
    ws = store.widgets(dash["id"])
    out = {k: dash[k] for k in ("id", "org_id", "title", "description", "updated_at", "archived")}
    out["my_role"] = dash.get("my_role") or ""
    out["period"] = ctx.period
    out["datasets"] = [dataset_public(d, now) for d in dss]
    pushes = [d["last_push_at"] for d in dss if d.get("last_push_at")]
    out["last_push_at"] = max(pushes) if pushes else ""
    out["stale"] = any(x["stale"] for x in out["datasets"])
    if with_data:
        out["widgets"] = [widget_view(ctx, dash, w, by_id, by_key) for w in ws]
    else:
        out["widgets"] = [{"id": w["id"], "kind": w["kind"], "title": w["title"], "width": w["width"],
                           "dataset": (by_id.get(w["dataset_id"]) or {}).get("key"), "options": w["options"]} for w in ws]
    return out


def headline(dash: dict[str, Any], *, tz: Any = DEFAULT_TZ, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Главная цифра для карточки в списке: первый number / goal / kpi_row."""
    ctx = Ctx(W.DEFAULT_PERIOD, tz, now)
    dss = store.datasets(dash["id"])
    by_id = {d["id"]: d for d in dss}
    by_key = {d["key"]: d for d in dss}
    for w in store.widgets(dash["id"]):
        if w["kind"] not in ("number", "goal", "kpi_row"):
            continue
        v = widget_view(ctx, dash, w, by_id, by_key)
        if v.get("error"):
            continue
        d = v["data"]
        if w["kind"] == "kpi_row":
            first = next((x for x in d.get("items") or [] if not x.get("missing")), None)
            if not first:
                continue
            return {"label": first.get("label"), "value": first.get("value"), "delta_pct": first.get("delta_pct"),
                    "good": first.get("good"), "format": first.get("format"), "unit": first.get("unit"), "spark": []}
        return {"label": v["title"], "value": d.get("value"), "delta_pct": d.get("delta_pct"), "good": d.get("good", "up"),
                "format": d.get("format"), "unit": d.get("unit"), "spark": d.get("spark") or []}
    return None


# ---------- текстом (для Лео и MCP) ----------


def fmt_num(v: Any, fmt: str = "number", unit: str = "") -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if fmt == "percent":
        s = f"{f:.1f}".rstrip("0").rstrip(".") + "%"
    elif abs(f) >= 100 or float(f).is_integer():
        s = f"{f:,.0f}".replace(",", " ")
    else:
        s = f"{f:.2f}".rstrip("0").rstrip(".")
    if fmt == "money" and not unit:
        unit = "₽"
    return f"{s} {unit}".strip()


def describe(view: dict[str, Any], *, max_points: int = 12) -> str:
    """Короткая текстовая сводка дашборда — цифры каждого виджета (без тысяч точек)."""
    lines = [f"Дашборд «{view['title']}» (период {view['period']}, обновлён {view.get('last_push_at') or 'нет данных'})"]
    for w in view.get("widgets") or []:
        if w.get("error"):
            lines.append(f"- {w['title']}: {w['error']}")
            continue
        d = w.get("data") or {}
        k = w["kind"]
        f, u = d.get("format", "number"), d.get("unit", "")
        if k == "number":
            dl = f", {d['delta_pct']:+.1f}% к прошлому периоду" if d.get("delta_pct") is not None else ""
            lines.append(f"- {w['title']}: {fmt_num(d.get('value'), f, u)}{dl}")
        elif k == "goal":
            lines.append(f"- {w['title']}: {fmt_num(d.get('value'), f, u)} из {fmt_num(d.get('target'), f, u)}"
                         + (f" ({d['pct']:.0f}%)" if d.get("pct") is not None else ""))
        elif k == "kpi_row":
            parts = [f"{x.get('label')} {fmt_num(x.get('value'), x.get('format', 'number'), x.get('unit', ''))}"
                     for x in d.get("items") or []]
            lines.append(f"- {w['title']}: " + "; ".join(parts))
        elif k in ("line", "bars", "area", "stacked100"):
            for s in d.get("series") or []:
                pairs = [(l, v) for l, v in zip(d.get("labels") or [], s["values"]) if v is not None][-max_points:]
                lines.append(f"- {w['title']} / {s['name']}: " + ", ".join(f"{l}: {fmt_num(v, f, u)}" for l, v in pairs))
        elif k in ("ranking", "share", "sparkgrid"):
            lines.append(f"- {w['title']}: " + ", ".join(
                f"{x['label']} {fmt_num(x['value'], f, u)}" + (f" ({x['pct']:.0f}%)" if "pct" in x else "")
                for x in (d.get("items") or [])[:max_points]))
        elif k == "funnel":
            lines.append(f"- {w['title']}: " + " → ".join(f"{x['label']} {fmt_num(x['value'])}" for x in d.get("items") or []))
        elif k == "bullet":
            lines.append(f"- {w['title']}: " + ", ".join(
                f"{x['label']} {fmt_num(x['fact'], f)} из {fmt_num(x['plan'], f)}" for x in (d.get("items") or [])[:max_points]))
        elif k == "status":
            lines.append(f"- {w['title']}: " + ", ".join(f"{x['label']} {x['state']}" for x in d.get("items") or []))
        elif k == "feed":
            lines.append(f"- {w['title']}: " + "; ".join(f"{x['ts'][:16]} {x['text']}" for x in (d.get("items") or [])[:5]))
        elif k == "table":
            lines.append(f"- {w['title']}: таблица, {d.get('total_rows', 0)} строк"
                         + ("; итоги: " + ", ".join(f"{k2} {fmt_num(v)}" for k2, v in d["totals"].items()) if d.get("totals") else ""))
        elif k == "waterfall":
            lines.append(f"- {w['title']}: " + ", ".join(f"{x['label']} {fmt_num(x['value'], f, u)}" for x in d.get("steps") or []))
        elif k == "text":
            continue
        else:
            lines.append(f"- {w['title']}: виджет {k}")
    return "\n".join(lines)
