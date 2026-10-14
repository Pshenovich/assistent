"""Template web-canvas decks: slot data → server-rendered HTML.

The model sends slides as ``{role, variant?, slots}``. The server validates the
slots, picks a layout variant from the data shape, and renders the deck from the
approved layout catalog. The deck JSON is embedded in the sheet HTML
(``<script type="application/json" id="leo-deck">``) and stays the source of
truth: text edited inline (``data-slot`` nodes) is synced back into the slots,
and switching variants or adding/removing items re-renders from the slots.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional

from assistant.lib import canvas_charts

DECK_VERSION = 1
DECK_THEME = "analytic"
MAX_SLIDES = 14
_TEXT_CAP = 600

ROLES: tuple[tuple[str, str], ...] = (
    ("cover", "Обложка"),
    ("context", "Контекст"),
    ("insight", "Вывод"),
    ("metrics", "Метрики"),
    ("chart", "График"),
    ("comparison", "Сравнение"),
    ("content", "Контент"),
    ("plan", "План"),
    ("closing", "Финал"),
    ("appendix", "Приложение"),
)
ROLE_IDS = tuple(r for r, _ in ROLES)
_ROLE_ALIASES = {
    "title": "cover",
    "intro": "cover",
    "agenda": "context",
    "insights": "insight",
    "quote": "insight",
    "kpi": "metrics",
    "metric": "metrics",
    "graph": "chart",
    "compare": "comparison",
    "profile": "content",
    "text": "content",
    "timeline": "plan",
    "process": "plan",
    "next": "closing",
    "ask": "closing",
    "notes": "appendix",
}

_VISUALS = ("torus", "cube", "orb", "prism", "rings")


# ---------------------------------------------------------------- slot paths


def get_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def set_path(obj: Any, path: str, value: Any) -> bool:
    parts = str(path).split(".")
    cur = obj
    for part in parts[:-1]:
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False
    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
        return True
    if isinstance(cur, list) and last.isdigit() and int(last) < len(cur):
        cur[int(last)] = value
        return True
    return False


def _s(v: Any) -> str:
    """Slot value → trimmed plain text."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return canvas_charts.nf(v, 0 if float(v).is_integer() else 1)
    return re.sub(r"\s+", " ", str(v)).strip()[:_TEXT_CAP]


def _lst(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _dicts(v: Any) -> list[dict[str, Any]]:
    return [x for x in _lst(v) if isinstance(x, dict)]


# ---------------------------------------------------------------- rendering ctx


class Ctx:
    """Per-slide render helpers: every editable text node gets data-slot."""

    def __init__(self, slide: dict[str, Any], media: dict[str, str]):
        self.slide = slide
        self.sid = str(slide["id"])
        self.slots = slide.get("slots") or {}
        self.media = media

    def val(self, path: str) -> str:
        return _s(get_path(self.slots, path))

    def has(self, path: str) -> bool:
        return bool(self.val(path))

    def t(self, path: str, tag: str = "p", cls: str = "", style: str = "", force: bool = False) -> str:
        """Editable text node; omitted when empty unless force (edit placeholder)."""
        v = self.val(path)
        if not v and not force:
            return ""
        c = f' class="{cls}"' if cls else ""
        st = f' style="{style}"' if style else ""
        return (
            f'<{tag}{c}{st} data-slot="{escape(path)}" '
            f'data-leo-id="{self.sid}-{escape(path)}">{escape(v)}</{tag}>'
        )

    def group(self, path: str, inner: str, cls: str = "", style: str = "") -> str:
        g = find_group(self.slide.get("variant", ""), path)
        lo, hi = (g.min, g.max) if g else (0, 99)
        c = f' class="{cls}"' if cls else ""
        st = f' style="{style}"' if style else ""
        return f'<div{c}{st} data-group="{escape(path)}" data-min="{lo}" data-max="{hi}">{inner}</div>'

    def item_attrs(self, path: str, i: int) -> str:
        return f'data-item="{escape(path)}.{i}"'


def _cols(n: int, long_text: bool = False, max_cols: int = 4) -> int:
    if n <= 0:
        return 1
    if n == 4 and long_text:
        return 2
    if n <= max_cols:
        return n
    return 3


def _eyebrow_title(c: Ctx, sub_path: str = "") -> str:
    out = c.t("eyebrow", "p", "eyebrow") + c.t("title", "h2", "title", force=True)
    if sub_path:
        out += c.t(sub_path, "p", "sub")
    return out


# ---------------------------------------------------------------- variants


@dataclass
class Group:
    path: str  # pattern, "*" for list index (e.g. "options.*.points")
    min: int
    max: int
    new: Callable[[], Any]


@dataclass
class Variant:
    id: str
    role: str
    name: str
    schema: str
    render: Callable[[Ctx], tuple[str, str]]
    check: Callable[[dict[str, Any]], str]
    prefer: Callable[[dict[str, Any]], bool] = lambda s: False
    groups: list[Group] = field(default_factory=list)


VARIANTS: dict[str, Variant] = {}


def _reg(v: Variant) -> None:
    VARIANTS[v.id] = v


def find_group(variant_id: str, path: str) -> Optional[Group]:
    v = VARIANTS.get(variant_id)
    if not v:
        return None
    pat = re.sub(r"\.\d+(?=\.|$)", ".*", str(path))
    for g in v.groups:
        if g.path == pat:
            return g
    return None


def _need(cond: bool, why: str) -> str:
    return "" if cond else why


# --- cover


def _r_cover_goals(c: Ctx) -> tuple[str, str]:
    goals = _lst(c.slots.get("goals"))
    items = "".join(
        f'<div class="card" {c.item_attrs("goals", i)}><span class="num">{i + 1:02d}</span>'
        f'{c.t(f"goals.{i}", "p", "clamp3", force=True)}</div>'
        for i in range(len(goals))
    )
    inner = (
        '<div style="margin-top:auto"></div>'
        + c.t("eyebrow", "p", "eyebrow")
        + c.t("title", "h1", "display", force=True)
        + c.t("lede", "p", "lede")
        + c.group("goals", items, f"grid cols-{_cols(len(goals))}", "margin-bottom:auto")
    )
    return inner, '<div class="dec dec-torus"></div>'


_reg(Variant(
    "cover.goals", "cover", "Заголовок и тезисы",
    "eyebrow?: str≤40, title: str≤70, lede?: str≤140, goals: [str≤90] 2–4",
    _r_cover_goals,
    lambda s: _need(len(_lst(s.get("goals"))) >= 2, "нужно 2–4 тезиса"),
    prefer=lambda s: len(_lst(s.get("goals"))) >= 2,
    groups=[Group("goals", 2, 4, lambda: "Новый тезис")],
))


def _r_cover_stat(c: Ctx) -> tuple[str, str]:
    inner = (
        '<div class="split" style="grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:60px">'
        "<div>"
        + c.t("eyebrow", "p", "eyebrow")
        + c.t("title", "h1", "display", "font-size:52px", force=True)
        + c.t("lede", "p", "lede", "margin:0")
        + '</div><div class="card is-accent" style="padding:36px 34px">'
        + c.t("hero_stat.label", "p", "lbl", force=True)
        + c.t("hero_stat.value", "p", "big acc", "font-size:120px;margin:14px 0 10px", force=True)
        + c.t("hero_stat.unit", "p", "", "font-size:20px")
        + c.t("hero_stat.note", "p", "body", "margin-top:8px")
        + "</div></div>"
    )
    return inner, ""


_reg(Variant(
    "cover.split-stat", "cover", "Заголовок и ключевая цифра",
    "eyebrow?, title: str≤70, lede?: str≤140, hero_stat: {value: str≤8, label: str≤60, unit?: str≤20, note?: str≤60}",
    _r_cover_stat,
    lambda s: _need(bool(_s(get_path(s, "hero_stat.value"))), "нет ключевой цифры"),
    prefer=lambda s: bool(_s(get_path(s, "hero_stat.value"))) and len(_lst(s.get("goals"))) < 2,
))


def _r_cover_min(c: Ctx) -> tuple[str, str]:
    meta = _dicts(c.slots.get("meta"))
    cells = "".join(
        f'<div {c.item_attrs("meta", i)}>{c.t(f"meta.{i}.label", "p", "lbl", "margin:0 0 4px", force=True)}'
        f'{c.t(f"meta.{i}.value", "p", force=True)}</div>'
        for i in range(len(meta))
    )
    inner = (
        '<div class="center-y">'
        + c.t("title", "h1", "display", "font-size:68px;max-width:15ch", force=True)
        + c.t("lede", "p", "lede", "font-size:22px")
        + "</div>"
        + c.group("meta", cells, "", "display:flex;gap:44px;border-top:1px solid var(--s-rule);padding-top:18px;margin-bottom:10px")
    )
    return inner, '<div class="dec dec-orb"></div>'


_reg(Variant(
    "cover.minimal", "cover", "Минимальная обложка",
    "title: str≤70, lede?: str≤120, meta?: [{label: str≤20, value: str≤40}] 0–3",
    _r_cover_min,
    lambda s: _need(bool(_s(s.get("title"))), "нет заголовка"),
    groups=[Group("meta", 0, 3, lambda: {"label": "Поле", "value": "Значение"})],
))

# --- context


def _r_ctx_stats(c: Ctx) -> tuple[str, str]:
    stats = _dicts(c.slots.get("stats"))
    items = "".join(
        f'<div class="card" style="padding:24px" {c.item_attrs("stats", i)}>'
        f'{c.t(f"stats.{i}.value", "p", "big", "font-size:52px;margin:0 0 10px", force=True)}'
        f'{c.t(f"stats.{i}.label", "p", "body clamp3", force=True)}</div>'
        for i in range(len(stats))
    )
    inner = (
        _eyebrow_title(c)
        + c.group("stats", items, f"grid cols-{_cols(len(stats))}", "margin-top:20px")
        + c.t("body", "p", "body", "margin-top:30px;max-width:62ch;font-size:19px")
    )
    return inner, ""


_reg(Variant(
    "context.stats", "context", "Объём данных",
    "eyebrow?, title, stats: [{value: str≤10, label: str≤60}] 2–4, body?: str≤220",
    _r_ctx_stats,
    lambda s: _need(len(_dicts(s.get("stats"))) >= 1, "нет цифр об объёме данных"),
    prefer=lambda s: bool(_dicts(s.get("stats"))),
    groups=[Group("stats", 1, 4, lambda: {"value": "0", "label": "Подпись"})],
))


def _r_ctx_agenda(c: Ctx) -> tuple[str, str]:
    rows = _dicts(c.slots.get("agenda"))
    items = "".join(
        f'<div style="display:grid;grid-template-columns:64px 1fr;gap:18px;align-items:baseline;'
        f'padding:14px 0;border-bottom:1px solid var(--s-rule)" {c.item_attrs("agenda", i)}>'
        f'<span class="big acc" style="font-size:28px">{i + 1}</span><div>'
        f'{c.t(f"agenda.{i}.title", "p", "", "font-size:22px;font-weight:600", force=True)}'
        f'{c.t(f"agenda.{i}.note", "p", "body", "font-size:16px;margin-top:2px")}</div></div>'
        for i in range(len(rows))
    )
    inner = c.t("title", "h2", "title", force=True) + c.group("agenda", items, "", "margin-top:16px;max-width:860px")
    return inner, ""


_reg(Variant(
    "context.agenda", "context", "Содержание",
    "title, agenda: [{title: str≤60, note?: str≤80}] 3–6",
    _r_ctx_agenda,
    lambda s: _need(len(_dicts(s.get("agenda"))) >= 2, "нет списка разделов"),
    prefer=lambda s: bool(_dicts(s.get("agenda"))),
    groups=[Group("agenda", 2, 6, lambda: {"title": "Новый раздел", "note": ""})],
))

# --- insight


def _r_ins_callout(c: Ctx) -> tuple[str, str]:
    inner = (
        c.t("eyebrow", "p", "eyebrow")
        + '<div class="callout center-y" style="max-width:900px">'
        + c.t("label", "p", "lbl amber", "margin:0 0 14px")
        + c.t("claim", "h3", "", "font-family:var(--f-display);font-size:40px;line-height:1.15;letter-spacing:-.02em;margin:0 0 18px;text-wrap:balance", force=True)
        + c.t("note", "p", "body", "font-size:19px;max-width:64ch")
        + "</div>"
    )
    return inner, '<div class="dec dec-ring"></div>'


_reg(Variant(
    "insight.callout", "insight", "Главный вывод",
    "eyebrow?, label?: str≤30, claim: str≤110, note?: str≤260",
    _r_ins_callout,
    lambda s: _need(bool(_s(s.get("claim"))), "нет формулировки вывода"),
))


def _r_ins_quote(c: Ctx) -> tuple[str, str]:
    inner = (
        '<div class="center-y" style="max-width:980px">'
        '<p class="big acc" style="font-size:120px;line-height:.6;margin:0 0 10px">“</p>'
        + c.t("quote", "p", "", "font-family:var(--f-display);font-weight:700;font-size:44px;line-height:1.2;letter-spacing:-.02em;margin:0 0 28px;text-wrap:balance", force=True)
        + c.t("who", "p", "", "font-size:18px")
        + c.t("context", "p", "body", "font-size:16px;margin-top:4px")
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "insight.quote", "insight", "Цитата",
    "quote: str≤180 (дословно из источника), who?: str≤60, context?: str≤80",
    _r_ins_quote,
    lambda s: _need(bool(_s(s.get("quote"))), "нет цитаты из источника"),
    prefer=lambda s: bool(_s(s.get("quote"))),
))


def _r_ins_evidence(c: Ctx) -> tuple[str, str]:
    ev = _dicts(c.slots.get("evidence"))
    items = "".join(
        f'<div class="card" style="display:grid;grid-template-columns:150px 1fr;gap:18px;align-items:center" {c.item_attrs("evidence", i)}>'
        f'{c.t(f"evidence.{i}.fact", "p", "big acc", "font-size:40px", force=True)}'
        f'{c.t(f"evidence.{i}.text", "p", "clamp3", force=True)}</div>'
        for i in range(len(ev))
    )
    inner = (
        '<div class="split" style="grid-template-columns:minmax(0,1fr) minmax(0,1.1fr)"><div>'
        + c.t("eyebrow", "p", "eyebrow")
        + c.t("claim", "h2", "title", "font-size:42px", force=True)
        + c.t("note", "p", "body", "font-size:19px")
        + "</div>"
        + c.group("evidence", items, "grid cols-1")
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "insight.evidence", "insight", "Вывод и доказательства",
    "claim: str≤90, note?: str≤160, evidence: [{fact: str≤14, text: str≤90}] 2–4",
    _r_ins_evidence,
    lambda s: _need(len(_dicts(s.get("evidence"))) >= 1, "нет фактов-доказательств"),
    prefer=lambda s: len(_dicts(s.get("evidence"))) >= 2,
    groups=[Group("evidence", 1, 4, lambda: {"fact": "0", "text": "Факт из заметки"})],
))

# --- metrics


def _kpi_card(c: Ctx, i: int, k: dict[str, Any]) -> str:
    delta = _s(k.get("delta"))
    good = str(k.get("delta_good") or "up").lower()
    neg = delta.startswith(("-", "−"))
    favourable = (not neg) if good != "down" else neg
    delta_html = ""
    if delta:
        col = "var(--s-acc)" if favourable else "var(--s-amber)"
        arrow = "▼" if neg else "▲"
        delta_html = (
            f'<p style="font-size:15px;font-weight:600;color:{col}">{arrow} '
            f'{c.t(f"kpis.{i}.delta", "span")} {c.t(f"kpis.{i}.delta_note", "span", "body", "font-weight:400")}</p>'
        )
    series = [x for x in _lst(k.get("series")) if isinstance(x, (int, float))]
    sp = canvas_charts.spark(series) if len(series) >= 4 else ""
    return (
        f'<div class="card" style="display:grid;gap:10px;padding:20px;align-content:start" {c.item_attrs("kpis", i)}>'
        f'{c.t(f"kpis.{i}.label", "p", "lbl clamp2", force=True)}'
        f'<p style="display:flex;align-items:baseline;gap:8px">{c.t(f"kpis.{i}.value", "span", "big", "font-size:46px", force=True)}'
        f'{c.t(f"kpis.{i}.unit", "span", "body", "font-size:15px")}</p>{delta_html}{sp}</div>'
    )


def _r_metrics_kpi(c: Ctx) -> tuple[str, str]:
    kpis = _dicts(c.slots.get("kpis"))
    items = "".join(_kpi_card(c, i, k) for i, k in enumerate(kpis))
    inner = (
        _eyebrow_title(c)
        + c.group("kpis", items, f"grid cols-{_cols(len(kpis), max_cols=5)}", "margin-top:22px")
        + c.t("note", "p", "cap", "margin-top:auto")
    )
    return inner, ""


_reg(Variant(
    "metrics.kpi", "metrics", "Ряд показателей",
    "eyebrow?, title, kpis: [{label: str≤40, value: str≤10, unit?: str≤16, delta?: str≤12 (напр. «+9 п.п.», «−38%»), delta_note?: str≤16, delta_good?: up|down, series?: [num]≥4}] 1–5, note?",
    _r_metrics_kpi,
    lambda s: _need(len(_dicts(s.get("kpis"))) >= 1, "нет показателей"),
    prefer=lambda s: len(_dicts(s.get("kpis"))) >= 2,
    groups=[Group("kpis", 1, 5, lambda: {"label": "Показатель", "value": "0"})],
))


def _r_metrics_hero(c: Ctx) -> tuple[str, str]:
    comp = _dicts(c.slots.get("compare"))
    cells = "".join(
        f'<div {c.item_attrs("compare", i)}>{c.t(f"compare.{i}.label", "p", "lbl", "margin:0 0 4px", force=True)}'
        f'{c.t(f"compare.{i}.value", "p", "big", "font-size:30px", force=True)}</div>'
        for i in range(len(comp))
    )
    inner = (
        '<div class="center-y" style="display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:60px;align-items:end"><div>'
        + c.t("kpis.0.label", "p", "lbl", "margin:0 0 10px", force=True)
        + '<p class="big acc" style="font-size:200px">'
        + c.t("kpis.0.value", "span", force=True)
        + c.t("kpis.0.unit", "span", "", "font-size:.5em")
        + '</p></div><div style="padding-bottom:26px">'
        + c.t("note", "p", "", "font-size:22px;margin:0 0 20px;line-height:1.4")
        + c.group("compare", cells, "", "display:flex;gap:30px;border-top:1px solid var(--s-rule);padding-top:16px")
        + "</div></div>"
    )
    return inner, ""


_reg(Variant(
    "metrics.hero", "metrics", "Одна большая цифра",
    "kpis: [{label, value, unit?}] (берётся первый), note?: str≤160, compare?: [{label: str≤20, value: str≤12}] 0–3",
    _r_metrics_hero,
    lambda s: _need(len(_dicts(s.get("kpis"))) >= 1, "нет показателя"),
    prefer=lambda s: len(_dicts(s.get("kpis"))) == 1,
    groups=[Group("compare", 0, 3, lambda: {"label": "Сравнение", "value": "0"})],
))

# --- chart (one variant per chart type)

_CHART_NAMES = {
    "bar-v": "Столбцы",
    "bar-h": "Горизонтальный рейтинг",
    "line": "Линии: тренд",
    "area": "Область: один ряд",
    "grouped": "Сгруппированные столбцы",
    "stacked100": "Структура 100%",
    "donut": "Кольцо: доля целого",
    "waterfall": "Водопад",
    "funnel": "Воронка",
    "bullet": "План / факт",
    "dumbbell": "До / после",
    "heatmap": "Матрица",
}


def _chart_data(slots: dict[str, Any]) -> dict[str, Any]:
    ch = slots.get("chart")
    return ch if isinstance(ch, dict) else {}


def _make_chart_render(ctype: str) -> Callable[[Ctx], tuple[str, str]]:
    def render(c: Ctx) -> tuple[str, str]:
        data = dict(_chart_data(c.slots))
        data["type"] = ctype
        notes = _dicts(c.slots.get("notes"))
        names = canvas_charts.legend_names(data)
        legend = ""
        if names:
            legend = '<div class="legend">' + "".join(
                f'<span><i style="background:{canvas_charts.C[i % 4]}"></i>{escape(n)}</span>'
                for i, n in enumerate(names)
            ) + "</div>"
        src = (
            '<p class="cap" style="margin-top:12px">Источник: ' + c.t("source", "span") + "</p>"
            if c.has("source")
            else ""
        )
        if notes:
            svg = canvas_charts.render_chart(data, 720, 400)
            cards = "".join(
                f'<div class="card" {c.item_attrs("notes", i)}>'
                f'{c.t(f"notes.{i}.label", "p", "lbl", "margin-bottom:6px", force=True)}'
                f'{c.t(f"notes.{i}.text", "p", "clamp4", force=True)}</div>'
                for i in range(len(notes))
            )
            body = (
                '<div class="grow" style="display:grid;grid-template-columns:minmax(0,1.75fr) minmax(0,1fr);gap:44px;align-items:center">'
                f"<div>{legend}{svg}</div>"
                + c.group("notes", cards, "grid cols-1")
                + "</div>"
            )
            return _eyebrow_title(c) + body + src, ""
        svg = canvas_charts.render_chart(data, 1120, 360 if c.has("sub") else 400)
        body = f'{legend}<div class="grow" style="display:flex;align-items:flex-end">{svg}</div>'
        return _eyebrow_title(c, "sub") + body + src, ""

    return render


def _make_chart_check(ctype: str) -> Callable[[dict[str, Any]], str]:
    def check(slots: dict[str, Any]) -> str:
        data = _chart_data(slots)
        if not data:
            return "нет данных для графика"
        for t, why in canvas_charts.chart_types_for(data):
            if t == ctype:
                return why
        return "тип не подходит"

    return check


for _ct, _nm in _CHART_NAMES.items():
    _reg(Variant(
        f"chart.{_ct}", "chart", _nm,
        "",
        _make_chart_render(_ct),
        _make_chart_check(_ct),
        groups=[Group("notes", 0, 3, lambda: {"label": "Вывод", "text": "Что видно на графике"})],
    ))

CHART_SCHEMA = (
    "eyebrow?, title: str≤90 (формулировка вывода), sub?: str≤140 (что и в каких единицах), "
    "chart: {intent: compare|rank|trend|share|structure|bridge|conversion|target|change|matrix, unit?: str, dec?: 0|1, "
    "x?: [str], series?: [{name?: str≤20, values: [num]}], highlight?: index, "
    "rows?: [{label, values}] (structure) | [{label, fact, plan}] (target) | [{label, a, b}] (change) | [str] (matrix), "
    "series_names?: [str] (structure), a_name?/b_name? (change), steps?: [{label, value, total?: bool}] (bridge), "
    "cols?: [str] + values?: [[num]] (matrix), center?: {value, label} (share), warn_below?: num (conversion)}, "
    "notes?: [{label: str≤24, text: str≤120}] 0–3 (график + выводы справа), source: str≤80"
)

# --- comparison

_MARKS = {
    "yes": ("m-yes", "✓", "Да"),
    "да": ("m-yes", "✓", "Да"),
    "partial": ("m-part", "~", "Частично"),
    "частично": ("m-part", "~", "Частично"),
    "no": ("m-no", "–", "Нет"),
    "нет": ("m-no", "–", "Нет"),
}


def _r_cmp_two(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))[:2]
    cols = []
    for oi, o in enumerate(opts):
        pts = _lst(o.get("points"))
        rows = "".join(
            f'<p class="row-rule" {c.item_attrs(f"options.{oi}.points", pi)} '
            f'data-slot="options.{oi}.points.{pi}" data-leo-id="{c.sid}-options.{oi}.points.{pi}">{escape(_s(p))}</p>'
            for pi, p in enumerate(pts)
        )
        accent = " is-accent" if _s(o.get("tag")) else ""
        cols.append(
            f'<div class="card{accent}" style="padding:24px">'
            '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:14px">'
            f'{c.t(f"options.{oi}.name", "p", "", "font-family:var(--f-display);font-weight:800;font-size:24px", force=True)}'
            f'{c.t(f"options.{oi}.tag", "span", "lbl acc")}</div>'
            + c.group(f"options.{oi}.points", rows)
            + "</div>"
        )
    inner = (
        _eyebrow_title(c)
        + f'<div class="grid cols-2" style="margin-top:12px">{"".join(cols)}</div>'
        + c.t("verdict", "p", "body", "margin-top:20px;font-size:17px")
    )
    return inner, ""


_reg(Variant(
    "comparison.two-col", "comparison", "Два варианта",
    "eyebrow?, title, options: [{name: str≤30, tag?: str≤20 (у рекомендуемого), points: [str≤80] 2–6}] ×2, verdict?: str≤140",
    _r_cmp_two,
    lambda s: _need(len(_dicts(s.get("options"))) >= 2, "нужно ровно 2 варианта"),
    prefer=lambda s: len(_dicts(s.get("options"))) == 2,
    groups=[Group("options.*.points", 1, 6, lambda: "Новый пункт")],
))


def _mark(v: str) -> str:
    m = _MARKS.get(v.strip().lower())
    if not m:
        return escape(v)
    return f'<span class="mark {m[0]}"><b>{m[1]}</b>{m[2]}</span>'


def _r_cmp_table(c: Ctx) -> tuple[str, str]:
    cols = _lst(c.slots.get("cols"))
    rows = _dicts(c.slots.get("rows"))
    head = "".join(
        f'<th data-slot="cols.{i}" data-leo-id="{c.sid}-cols.{i}">{escape(_s(h))}</th>' for i, h in enumerate(cols)
    )
    body = ""
    for ri, r in enumerate(rows):
        cells = _lst(r.get("cells"))
        tds = ""
        for ci in range(len(cols)):
            v = _s(cells[ci]) if ci < len(cells) else ""
            if v.lower() in _MARKS:
                tds += f"<td>{_mark(v)}</td>"
            else:
                tds += f'<td data-slot="rows.{ri}.cells.{ci}" data-leo-id="{c.sid}-rows.{ri}.cells.{ci}">{escape(v)}</td>'
        body += (
            f'<tr {c.item_attrs("rows", ri)}><td style="color:var(--s-muted)" data-slot="rows.{ri}.criterion" '
            f'data-leo-id="{c.sid}-rows.{ri}.criterion">{escape(_s(r.get("criterion")))}</td>{tds}</tr>'
        )
    table = (
        f'<table class="t"><thead><tr><th>{escape(_s(c.slots.get("criterion_label")) or "Критерий")}</th>{head}</tr></thead>'
        + c.group("rows", "", "", "display:none")  # limits carrier for add/remove
        + f'<tbody data-group="rows">{body}</tbody></table>'
    )
    return _eyebrow_title(c) + f'<div style="margin-top:40px">{table}</div>', ""


_reg(Variant(
    "comparison.table", "comparison", "Таблица критериев",
    "eyebrow?, title, cols: [str≤20] 2–4, rows: [{criterion: str≤30, cells: [yes|partial|no|str≤24]}] 2–8",
    _r_cmp_table,
    lambda s: _need(len(_lst(s.get("cols"))) >= 2 and len(_dicts(s.get("rows"))) >= 1, "нужна таблица: столбцы и строки"),
    prefer=lambda s: len(_lst(s.get("cols"))) >= 2,
    groups=[Group("rows", 1, 8, lambda: {"criterion": "Критерий", "cells": []})],
))


def _r_cmp_ba(c: Ctx) -> tuple[str, str]:
    def side(key: str, accent: bool) -> str:
        pts = _lst(get_path(c.slots, f"{key}.points"))
        rows = "".join(
            f'<p class="row-rule" style="{"" if accent else "color:var(--s-muted)"}" {c.item_attrs(f"{key}.points", i)} '
            f'data-slot="{key}.points.{i}" data-leo-id="{c.sid}-{key}.points.{i}">{escape(_s(p))}</p>'
            for i, p in enumerate(pts)
        )
        default = "После" if accent else "Сейчас"
        if not c.has(f"{key}.label"):
            set_path(c.slots, f"{key}.label", default)
        return (
            f'<div class="card{" is-accent" if accent else ""}" style="padding:24px">'
            + c.t(f"{key}.label", "p", "lbl" + (" acc" if accent else ""), "margin:0 0 12px", force=True)
            + c.group(f"{key}.points", rows)
            + "</div>"
        )

    inner = (
        _eyebrow_title(c)
        + '<div style="display:grid;grid-template-columns:minmax(0,1fr) 60px minmax(0,1fr);margin-top:14px">'
        + side("before", False)
        + '<div style="display:grid;place-items:center;color:var(--s-acc);font-size:34px">→</div>'
        + side("after", True)
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "comparison.before-after", "comparison", "Было / стало",
    "eyebrow?, title, before: {label?: str≤20, points: [str≤80] 1–5}, after: {label?, points: [str≤80] 1–5}",
    _r_cmp_ba,
    lambda s: _need(bool(_lst(get_path(s, "before.points"))) and bool(_lst(get_path(s, "after.points"))), "нужны пункты «было» и «стало»"),
    prefer=lambda s: bool(_lst(get_path(s, "before.points"))),
    groups=[
        Group("before.points", 1, 5, lambda: "Новый пункт"),
        Group("after.points", 1, 5, lambda: "Новый пункт"),
    ],
))

# --- content


def _r_cards(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    n = len(items)
    long_text = sum(len(_s(x.get("text"))) for x in items) / max(n, 1) > 90
    cols = _cols(n, long_text, max_cols=4)
    small = n > 3 or cols < n
    used: list[str] = []
    cards = ""
    for i, it in enumerate(items):
        vis = str(it.get("visual") or "").strip().lower()
        if vis not in _VISUALS or vis in used:
            vis = next((v for v in _VISUALS if v not in used), _VISUALS[i % len(_VISUALS)])
        used.append(vis)
        inner_vis = "<i></i><i></i><i></i>" if vis == "cube" else ""
        cards += (
            f'<div class="card" style="padding:0;overflow:hidden;display:flex;flex-direction:column" {c.item_attrs("items", i)}>'
            f'<div class="a3{" is-small" if small else ""}"><div class="a3-{vis}">{inner_vis}</div></div>'
            f'<div style="padding:{"16px 20px 18px" if small else "20px 24px 24px"}">'
            f'{c.t(f"items.{i}.title", "p", "h-card clamp2", "font-size:20px" if small else "", force=True)}'
            f'{c.t(f"items.{i}.text", "p", "body clamp4", "font-size:16px" if small else "font-size:17px", force=True)}</div></div>'
        )
    inner = _eyebrow_title(c) + c.group("items", cards, f"grid cols-{cols}", "margin-top:18px;flex:1;align-items:stretch;min-height:0")
    return inner, ""


_reg(Variant(
    "content.cards", "content", "Карточки с 3D-визуалом",
    "eyebrow?, title, items: [{title: str≤40, text: str≤140, visual?: torus|cube|orb|prism|rings}] 1–6",
    _r_cards,
    lambda s: _need(len(_dicts(s.get("items"))) >= 1, "нет пунктов"),
    prefer=lambda s: len(_dicts(s.get("items"))) >= 1,
    groups=[Group("items", 1, 6, lambda: {"title": "Новый пункт", "text": "Опишите или попросите заполнить из заметки"})],
))


def _r_profile(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    cells = "".join(
        f'<div class="card" style="padding:22px" {c.item_attrs("items", i)}>'
        f'{c.t(f"items.{i}.title", "p", "lbl", "margin:0 0 8px", force=True)}'
        f'{c.t(f"items.{i}.text", "p", "clamp3", "font-size:18px", force=True)}</div>'
        for i in range(len(items))
    )
    head = (
        '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:20px"><div>'
        + _eyebrow_title(c)
        + "</div>"
        + c.t("tag", "span", "lbl amber")
        + "</div>"
    )
    cols = 2 if len(items) <= 4 else 3
    return head + c.group("items", cells, f"grid cols-{cols}", "margin-top:10px"), ""


_reg(Variant(
    "content.profile", "content", "Профиль объекта",
    "eyebrow?, title: str≤60, tag?: str≤30, items: [{title: str≤24 (поле), text: str≤140}] 2–6",
    _r_profile,
    lambda s: _need(len(_dicts(s.get("items"))) >= 2, "нужно 2–6 полей"),
    prefer=lambda s: bool(_s(s.get("tag"))),
    groups=[Group("items", 2, 6, lambda: {"title": "Поле", "text": "Описание"})],
))


def _r_text_media(c: Ctx) -> tuple[str, str]:
    pts = _lst(c.slots.get("points"))
    rows = "".join(
        f'<p class="row-rule" {c.item_attrs("points", i)} data-slot="points.{i}" data-leo-id="{c.sid}-points.{i}">{escape(_s(p))}</p>'
        for i, p in enumerate(pts)
    )
    mid = f"{c.sid}-media"
    src = c.media.get(mid, "")
    if src:
        media = f'<div class="media-slot tpl-media" data-media="{mid}" data-leo-id="{mid}"><img class="media-img" alt="" src="{escape(src)}"/></div>'
    else:
        prompt = c.val("image_prompt")
        pending = f' data-leo-img="pending" data-leo-img-prompt="{escape(prompt)}"' if prompt else ""
        media = (
            f'<div class="media-slot tpl-media tpl-media-empty abstract" data-media="{mid}" data-leo-id="{mid}"{pending}>'
            '<div><p class="lbl acc" style="margin:0 0 8px">Изображение</p>'
            '<p style="max-width:30ch">Загрузите или сгенерируйте в режиме редактирования</p></div></div>'
        )
    inner = (
        '<div class="split" style="grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:50px"><div>'
        + _eyebrow_title(c)
        + c.t("body", "p", "body", "font-size:19px;margin-bottom:18px", force=True)
        + c.group("points", rows)
        + f'</div><div style="height:100%">{media}</div></div>'
    )
    return inner, ""


_reg(Variant(
    "content.text-media", "content", "Текст и изображение",
    "eyebrow?, title: str≤80, body: str≤320, points?: [str≤60] 0–3, image_prompt?: str≤300 (на английском)",
    _r_text_media,
    lambda s: _need(bool(_s(s.get("body"))), "нет основного текста"),
    prefer=lambda s: bool(_s(s.get("image_prompt"))) and bool(_s(s.get("body"))),
    groups=[Group("points", 0, 3, lambda: "Новый пункт")],
))

# --- plan


def _r_timeline(c: Ctx) -> tuple[str, str]:
    periods = _dicts(c.slots.get("periods"))
    cols = []
    for i, p in enumerate(periods):
        its = _lst(p.get("items"))
        rows = "".join(
            f'<p class="body row-rule" style="font-size:16px;padding:7px 0" {c.item_attrs(f"periods.{i}.items", j)} '
            f'data-slot="periods.{i}.items.{j}" data-leo-id="{c.sid}-periods.{i}.items.{j}">{escape(_s(x))}</p>'
            for j, x in enumerate(its)
        )
        dot = "var(--s-acc)" if i == 0 else "var(--s-el)"
        cols.append(
            f'<div {c.item_attrs("periods", i)}><span style="display:block;width:20px;height:20px;border-radius:50%;'
            f'background:{dot};border:2px solid var(--s-acc);margin-bottom:18px"></span>'
            f'{c.t(f"periods.{i}.label", "p", "lbl acc", "margin:0 0 6px", force=True)}'
            f'{c.t(f"periods.{i}.title", "p", "h-card", "font-size:22px;margin:0 0 12px", force=True)}'
            + c.group(f"periods.{i}.items", rows)
            + "</div>"
        )
    grid = c.group("periods", "".join(cols), f"grid cols-{_cols(len(periods), max_cols=6)}", "position:relative")
    inner = (
        _eyebrow_title(c)
        + '<div style="position:relative;margin-top:22px"><div style="position:absolute;left:0;right:0;top:9px;height:2px;background:var(--s-rule)"></div>'
        + grid
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "plan.timeline", "plan", "Таймлайн по периодам",
    "eyebrow?, title, periods: [{label: str≤10, title: str≤36, items: [str≤60] 1–5}] 2–6",
    _r_timeline,
    lambda s: _need(len(_dicts(s.get("periods"))) >= 2, "нужно 2–6 периодов"),
    prefer=lambda s: bool(_dicts(s.get("periods"))),
    groups=[
        Group("periods", 2, 6, lambda: {"label": "Период", "title": "Этап", "items": ["Результат"]}),
        Group("periods.*.items", 0, 5, lambda: "Результат"),
    ],
))


def _r_steps(c: Ctx) -> tuple[str, str]:
    steps = _dicts(c.slots.get("steps"))
    rows = "".join(
        f'<div class="card" style="display:grid;grid-template-columns:44px minmax(0,1fr) 190px 90px;gap:16px;align-items:center;padding:14px 20px" {c.item_attrs("steps", i)}>'
        f'<span class="big acc" style="font-size:26px">{i + 1}</span><div>'
        f'{c.t(f"steps.{i}.title", "p", "", "font-weight:600;font-size:18px", force=True)}'
        f'{c.t(f"steps.{i}.text", "p", "body clamp2", "font-size:15px")}</div>'
        f'{c.t(f"steps.{i}.owner", "p", "body", "font-size:15px", force=True)}'
        f'{c.t(f"steps.{i}.due", "p", "", "font-size:15px;text-align:right", force=True)}</div>'
        for i in range(len(steps))
    )
    return _eyebrow_title(c) + c.group("steps", rows, "grid cols-1", "margin-top:10px;gap:10px"), ""


_reg(Variant(
    "plan.steps", "plan", "Шаги с ответственными",
    "eyebrow?, title, steps: [{title: str≤50, text?: str≤100, owner?: str≤24, due?: str≤16}] 2–6",
    _r_steps,
    lambda s: _need(len(_dicts(s.get("steps"))) >= 2, "нужно 2–6 шагов"),
    prefer=lambda s: bool(_dicts(s.get("steps"))),
    groups=[Group("steps", 2, 6, lambda: {"title": "Новый шаг", "owner": "Ответственный", "due": "срок"})],
))

# --- closing


def _r_next(c: Ctx) -> tuple[str, str]:
    acts = _dicts(c.slots.get("actions"))
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;gap:20px;padding:18px 0;border-top:1px solid var(--s-rule);font-size:21px" {c.item_attrs("actions", i)}>'
        f'{c.t(f"actions.{i}.text", "span", "", force=True)}'
        f'{c.t(f"actions.{i}.due", "span", "acc", "white-space:nowrap;font-weight:600")}</div>'
        for i in range(len(acts))
    )
    if not c.has("eyebrow"):
        set_path(c.slots, "eyebrow", "Дальше")
    inner = (
        '<div class="center-y" style="display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.2fr);gap:60px;align-items:center"><div>'
        + c.t("eyebrow", "p", "eyebrow")
        + c.t("title", "h2", "display", "font-size:52px", force=True)
        + c.t("contact", "p", "body", "font-size:18px")
        + "</div>"
        + c.group("actions", rows)
        + "</div>"
    )
    return inner, '<div class="dec dec-torus is-low"></div>'


_reg(Variant(
    "closing.next-steps", "closing", "Следующие шаги",
    "eyebrow?, title: str≤40, actions: [{text: str≤80, due?: str≤16}] 1–5, contact?: str≤60",
    _r_next,
    lambda s: _need(len(_dicts(s.get("actions"))) >= 1, "нет следующих шагов"),
    prefer=lambda s: bool(_dicts(s.get("actions"))),
    groups=[Group("actions", 1, 5, lambda: {"text": "Новое действие", "due": ""})],
))


def _r_ask(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))
    cells = "".join(
        f'<div class="card{" is-accent" if i == 0 else ""}" {c.item_attrs("options", i)}>'
        f'{c.t(f"options.{i}.name", "p", "acc" if i == 0 else "", "font-weight:700;font-size:19px;margin:0 0 6px", force=True)}'
        f'{c.t(f"options.{i}.text", "p", "body clamp3", "font-size:16px", force=True)}</div>'
        for i in range(len(opts))
    )
    if not c.has("label"):
        set_path(c.slots, "label", "Нужно решение")
    inner = (
        '<div class="center-y" style="max-width:1000px">'
        + c.t("label", "p", "lbl amber", "margin:0 0 14px")
        + c.t("ask", "h2", "display", "font-size:50px;max-width:22ch", force=True)
        + c.t("why", "p", "lede", "max-width:60ch")
        + c.group("options", cells, f"grid cols-{max(len(opts), 1)}", "max-width:900px")
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "closing.ask", "closing", "Нужно решение",
    "label?: str≤30, ask: str≤90, why?: str≤180, options?: [{name: str≤30, text: str≤80}] 0–3",
    _r_ask,
    lambda s: _need(bool(_s(s.get("ask"))), "нет запроса к аудитории"),
    prefer=lambda s: bool(_s(s.get("ask"))),
    groups=[Group("options", 0, 3, lambda: {"name": "Вариант", "text": "Что будет"})],
))

# --- appendix


def _r_notes(c: Ctx) -> tuple[str, str]:
    cav = _lst(c.slots.get("caveats"))
    src = _dicts(c.slots.get("sources"))
    cav_html = "".join(
        f'<p class="body row-rule" style="font-size:16px" {c.item_attrs("caveats", i)} data-slot="caveats.{i}" '
        f'data-leo-id="{c.sid}-caveats.{i}">{escape(_s(x))}</p>'
        for i, x in enumerate(cav)
    )
    src_html = "".join(
        f'<div class="row-rule" {c.item_attrs("sources", i)}>{c.t(f"sources.{i}.name", "p", "", "font-size:16px", force=True)}'
        f'{c.t(f"sources.{i}.detail", "p", "cap")}</div>'
        for i in range(len(src))
    )
    if not c.has("title"):
        set_path(c.slots, "title", "Оговорки и источники")
    inner = (
        _eyebrow_title(c)
        + '<div style="display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:50px;margin-top:10px">'
        + '<div><p class="lbl" style="margin:0 0 8px">Оговорки</p>' + c.group("caveats", cav_html) + "</div>"
        + '<div><p class="lbl" style="margin:0 0 8px">Источники</p>' + c.group("sources", src_html) + "</div>"
        + "</div>"
    )
    return inner, ""


_reg(Variant(
    "appendix.notes", "appendix", "Оговорки и источники",
    "eyebrow?, title?, caveats: [str≤140] 1–6, sources?: [{name: str≤60, detail?: str≤80}] 0–6",
    _r_notes,
    lambda s: _need(bool(_lst(s.get("caveats"))) or bool(_dicts(s.get("sources"))), "нет оговорок или источников"),
    groups=[
        Group("caveats", 0, 6, lambda: "Новая оговорка"),
        Group("sources", 0, 6, lambda: {"name": "Источник", "detail": ""}),
    ],
))

_FALLBACK_VARIANT = {
    "cover": "cover.minimal",
    "context": "context.stats",
    "insight": "insight.callout",
    "metrics": "metrics.kpi",
    "chart": "chart.bar-v",
    "comparison": "comparison.two-col",
    "content": "content.cards",
    "plan": "plan.steps",
    "closing": "closing.next-steps",
    "appendix": "appendix.notes",
}


def variants_for_role(role: str) -> list[Variant]:
    return [v for v in VARIANTS.values() if v.role == role]


# ---------------------------------------------------------------- normalize


def normalize_role(raw: Any) -> str:
    r = str(raw or "").strip().lower()
    if r in ROLE_IDS:
        return r
    return _ROLE_ALIASES.get(r, "content")


def _clean(v: Any, depth: int = 0) -> Any:
    """Keep plain JSON data only; cap string length and nesting."""
    if depth > 6:
        return None
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return v.strip()[:_TEXT_CAP]
    if isinstance(v, list):
        return [_clean(x, depth + 1) for x in v[:40]]
    if isinstance(v, dict):
        return {str(k)[:40]: _clean(x, depth + 1) for k, x in list(v.items())[:40]}
    return str(v)[:_TEXT_CAP]


def _clip_groups(variant: Variant, slots: dict[str, Any]) -> None:
    """Trim repeated groups to their maximum (extra items would overflow the stage)."""
    for g in variant.groups:
        if "*" in g.path:
            head, _, tail = g.path.partition(".*.")
            for parent in _lst(slots.get(head)):
                if isinstance(parent, dict) and isinstance(parent.get(tail), list):
                    del parent[tail][g.max:]
        elif isinstance(get_path(slots, g.path), list):
            lst = get_path(slots, g.path)
            del lst[g.max:]


def check_variant(variant_id: str, slots: dict[str, Any]) -> str:
    v = VARIANTS.get(variant_id)
    if not v:
        return "нет такого варианта"
    try:
        return v.check(slots)
    except Exception:  # noqa: BLE001 — malformed model data never breaks render
        return "данные не подходят"


def auto_variant(role: str, slots: dict[str, Any], *, avoid: str = "") -> str:
    if role == "chart":
        t = canvas_charts.pick_chart_type(_chart_data(slots))
        return f"chart.{t}" if t else _FALLBACK_VARIANT["chart"]
    cands = [v for v in variants_for_role(role) if not check_variant(v.id, slots)]
    for pool in (
        [v for v in cands if v.prefer(slots) and v.id != avoid],
        [v for v in cands if v.id != avoid],
        cands,
    ):
        if pool:
            return pool[0].id
    return _FALLBACK_VARIANT.get(role, "content.cards")


def normalize_slide(raw: Any, *, sid: str, prev_variant: str = "") -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    role = normalize_role(src.get("role"))
    slots = _clean(src.get("slots")) if isinstance(src.get("slots"), dict) else {}
    # Tolerate slots given at the top level.
    if not slots:
        slots = {k: _clean(v) for k, v in src.items() if k not in ("role", "variant", "id")}
    want = str(src.get("variant") or "").strip()
    if want and not want.startswith(role + ".") and "." not in want:
        want = f"{role}.{want}"
    if want in VARIANTS and VARIANTS[want].role == role and not check_variant(want, slots):
        variant = want
    else:
        variant = auto_variant(role, slots, avoid=prev_variant)
    _clip_groups(VARIANTS[variant], slots)
    return {"id": sid, "role": role, "variant": variant, "slots": slots}


def _next_id(deck: dict[str, Any]) -> str:
    used = {str(s.get("id")) for s in deck.get("slides") or []}
    i = 1
    while f"s{i}" in used:
        i += 1
    return f"s{i}"


def build_deck(action: dict[str, Any]) -> dict[str, Any]:
    deck: dict[str, Any] = {
        "v": DECK_VERSION,
        "theme": DECK_THEME,
        "title": _s(action.get("title")) or "Презентация",
        "footer": _s(action.get("footer")),
        "slides": [],
    }
    prev = ""
    for raw in _lst(action.get("slides"))[:MAX_SLIDES]:
        if not isinstance(raw, dict):
            continue
        slide = normalize_slide(raw, sid=_next_id(deck), prev_variant=prev)
        deck["slides"].append(slide)
        prev = slide["variant"]
    return deck


def is_template_action(action: dict[str, Any]) -> bool:
    slides = action.get("slides")
    return isinstance(slides, list) and any(isinstance(s, dict) for s in slides)


# ---------------------------------------------------------------- deck HTML

_DECK_JSON_RE = re.compile(
    r'<script[^>]*id=["\']leo-deck["\'][^>]*>(.*?)</script>', re.IGNORECASE | re.DOTALL
)
_DECK_MARK_RE = re.compile(r"<html\b[^>]*\bdata-leo-deck\b", re.IGNORECASE)
_MEDIA_IMG_RE = re.compile(
    r"""<(?:div|figure)\b[^>]*\bdata-media=["']([^"']+)["'][^>]*>\s*<img\b[^>]*\bsrc=["']([^"']+)["']""",
    re.IGNORECASE,
)

_CSS_CACHE: dict[str, Any] = {}


def templates_css() -> str:
    path = Path(__file__).resolve().parents[2] / "webapp" / "canvas-kit" / "templates.css"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    if _CSS_CACHE.get("mtime") != mtime:
        _CSS_CACHE["css"] = path.read_text(encoding="utf-8")
        _CSS_CACHE["mtime"] = mtime
    return str(_CSS_CACHE.get("css") or "")


_FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@500;600;700;800'
    '&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">'
)

# Present mode (one slide at a time) + stage scaling. Ignores keys while the
# editor's review mode is on or focus is in a text field.
_DECK_SCRIPT = (
    '<script id="leo-deck-present">(function(){try{'
    "var root=document.documentElement;root.classList.add('leo-deck-js');"
    "var slides=[].slice.call(document.querySelectorAll('.deck > .slide'));"
    "function fit(s){var r=s.getBoundingClientRect();if(!r.width)return;"
    "s.style.setProperty('--k',Math.min(r.width/1280,r.height/720).toFixed(4));}"
    "var ro=window.ResizeObserver?new ResizeObserver(function(es){es.forEach(function(e){fit(e.target);});}):null;"
    "slides.forEach(function(s){if(ro)ro.observe(s);fit(s);});"
    "window.addEventListener('resize',function(){slides.forEach(fit);});"
    "if(slides.length<2){if(slides[0])slides[0].classList.add('is-active');return;}"
    "var i=0,bar,cnt;"
    "function sync(){slides.forEach(function(s,n){s.classList.toggle('is-active',n===i);});"
    "if(bar)bar.style.width=((i+1)/slides.length*100)+'%';"
    "if(cnt)cnt.textContent=(i+1)+' / '+slides.length;"
    "slides.forEach(fit);}"
    "function go(d){i=Math.max(0,Math.min(slides.length-1,i+d));sync();}"
    "var ui=document.createElement('div');ui.className='leo-present-ui';"
    "ui.innerHTML='<button type=\"button\" data-d=\"-1\" aria-label=\"Назад\">←</button>"
    "<div class=\"leo-present-progress\"><i></i></div><span class=\"leo-present-count\"></span>"
    "<button type=\"button\" data-d=\"1\" aria-label=\"Вперёд\">→</button>';"
    "document.body.appendChild(ui);"
    "bar=ui.querySelector('.leo-present-progress i');cnt=ui.querySelector('.leo-present-count');"
    "ui.addEventListener('click',function(e){var b=e.target.closest('button[data-d]');if(b)go(+b.getAttribute('data-d'));});"
    "document.addEventListener('keydown',function(e){"
    "if(root.classList.contains('leo-review')||root.classList.contains('leo-edit-mode'))return;"
    "var t=e.target;if(t&&(t.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)))return;"
    "if(['ArrowRight','ArrowDown','PageDown',' '].indexOf(e.key)>=0){e.preventDefault();go(1);}"
    "else if(['ArrowLeft','ArrowUp','PageUp'].indexOf(e.key)>=0){e.preventDefault();go(-1);}});"
    "sync();"
    "}catch(err){}})();</script>"
)


def _menu_for(slide: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for v in variants_for_role(slide["role"]):
        why = check_variant(v.id, slide.get("slots") or {})
        out.append({"id": v.id, "name": v.name, "ok": not why, "why": why})
    return out


def render_slide(slide: dict[str, Any], index: int, deck: dict[str, Any], media: dict[str, str]) -> str:
    variant = VARIANTS.get(slide.get("variant", "")) or VARIANTS[_FALLBACK_VARIANT.get(slide.get("role", ""), "content.cards")]
    ctx = Ctx(slide, media)
    try:
        inner, decor = variant.render(ctx)
    except Exception as e:  # noqa: BLE001 — one bad slide must not kill the deck
        print(f"[canvas_templates] render failed slide={slide.get('id')} variant={variant.id} err={e!r}")
        inner, decor = ctx.t("title", "h2", "title", force=True) + '<p class="body">Не удалось отрисовать слайд.</p>', ""
    footer = escape(_s(deck.get("footer")) or _s(deck.get("title")))
    sid = escape(str(slide["id"]))
    return (
        f'<section class="slide tpl" data-leo-id="{sid}" data-slide-id="{sid}" '
        f'data-role="{escape(slide["role"])}" data-variant="{escape(variant.id)}">'
        f'<div class="tpl-stage">{decor}<div class="tpl-in">{inner}</div>'
        f'<div class="tpl-foot"><span>{footer}</span><span>{index + 1:02d}</span></div></div></section>'
    )


def _deck_json_for_embed(deck: dict[str, Any]) -> str:
    payload = dict(deck)
    payload["ui"] = {
        "variants": {s["id"]: _menu_for(s) for s in deck.get("slides") or []},
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Never let user text open or close markup inside the script element.
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_deck_html(deck: dict[str, Any], media: dict[str, str] | None = None) -> str:
    media = media or {}
    slides_html = "".join(render_slide(s, i, deck, media) for i, s in enumerate(deck.get("slides") or []))
    title = escape(_s(deck.get("title")) or "Презентация")
    return (
        f'<!DOCTYPE html><html data-leo-theme="{DECK_THEME}" data-leo-deck="{DECK_VERSION}"><head>'
        f'<meta charset="utf-8"><title>{title}</title>{_FONTS}'
        f'<style id="leo-canvas-tpl">\n{templates_css()}\n</style></head><body>'
        f'<main class="deck">{slides_html}</main>'
        f'<script type="application/json" id="leo-deck">{_deck_json_for_embed(deck)}</script>'
        f"{_DECK_SCRIPT}</body></html>"
    )


def is_template_html(html: str) -> bool:
    return bool(_DECK_MARK_RE.search(html or "")) and bool(_DECK_JSON_RE.search(html or ""))


def extract_deck(html: str) -> Optional[dict[str, Any]]:
    m = _DECK_JSON_RE.search(html or "")
    if not m:
        return None
    raw = m.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("slides"), list):
        return None
    data.pop("ui", None)
    slides = []
    for s in data["slides"]:
        if isinstance(s, dict) and s.get("id") and s.get("variant") in VARIANTS:
            s["slots"] = s.get("slots") if isinstance(s.get("slots"), dict) else {}
            s["role"] = VARIANTS[s["variant"]].role
            slides.append(s)
    data["slides"] = slides
    return data


def extract_media(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _MEDIA_IMG_RE.finditer(html or ""):
        src = m.group(2)
        if src.startswith("data:image/") or src.startswith("/") or src.startswith("https://"):
            out[m.group(1)] = src
    return out


class _SlotTextParser(HTMLParser):
    """Collect plain text of every [data-slot] node, keyed by (slide id, path)."""

    _VOID = {"br", "img", "hr", "input", "meta", "link", "source", "wbr", "area", "col", "embed", "param", "track"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.slide: Optional[str] = None
        self.slide_depth = 0
        self.depth = 0
        self.stack: list[tuple[int, str, list[str]]] = []
        self.out: dict[tuple[str, str], str] = {}
        self.skip_depth: Optional[int] = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in self._VOID:
            if tag == "br" and self.stack:
                self.stack[-1][2].append(" ")
            return
        self.depth += 1
        a = {k: (v or "") for k, v in attrs}
        cls = a.get("class", "")
        if self.skip_depth is None and ("leo-edit-chrome" in cls.split() or tag in ("script", "style")):
            self.skip_depth = self.depth
        if tag == "section" and a.get("data-slide-id"):
            self.slide = a["data-slide-id"]
            self.slide_depth = self.depth
        if self.slide and a.get("data-slot") is not None and self.skip_depth is None:
            self.stack.append((self.depth, a["data-slot"], []))

    def handle_endtag(self, tag: str) -> None:
        if tag in self._VOID:
            return
        if self.stack and self.stack[-1][0] == self.depth:
            _, path, buf = self.stack.pop()
            if self.slide:
                self.out[(self.slide, path)] = re.sub(r"\s+", " ", "".join(buf)).strip()
        if self.skip_depth == self.depth:
            self.skip_depth = None
        if self.slide and self.depth == self.slide_depth:
            self.slide = None
        self.depth = max(0, self.depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth is not None:
            return
        for entry in self.stack:
            entry[2].append(data)


def sync_text_edits(deck: dict[str, Any], html: str) -> dict[str, Any]:
    """Copy inline-edited slot texts from the HTML back into the deck slots."""
    p = _SlotTextParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception:  # noqa: BLE001
        return deck
    by_id = {str(s["id"]): s for s in deck.get("slides") or []}
    for (sid, path), text in p.out.items():
        s = by_id.get(sid)
        if not s:
            continue
        slots = s.setdefault("slots", {})
        new = text[:_TEXT_CAP]
        old = get_path(slots, path)
        if old is None:
            _ensure_path(slots, path)
        if isinstance(old, (int, float)) and not isinstance(old, bool):
            if _s(old) == new:
                continue
        set_path(slots, path, new)
    return deck


def _ensure_path(slots: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    cur: Any = slots
    for i, part in enumerate(parts[:-1]):
        nxt_is_idx = parts[i + 1].isdigit()
        if isinstance(cur, dict):
            if part not in cur or not isinstance(cur[part], (dict, list)):
                cur[part] = [] if nxt_is_idx else {}
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return


def refinalize(html: str) -> Optional[str]:
    """Re-render a template deck from its embedded JSON + inline text edits."""
    deck = extract_deck(html)
    if deck is None:
        return None
    deck = sync_text_edits(deck, html)
    return render_deck_html(deck, extract_media(html))


# ---------------------------------------------------------------- operations


class DeckOpError(ValueError):
    pass


def _slide(deck: dict[str, Any], slide_id: str) -> dict[str, Any]:
    for s in deck.get("slides") or []:
        if str(s.get("id")) == str(slide_id):
            return s
    raise DeckOpError("Слайд не найден")


def op_set_variant(deck: dict[str, Any], slide_id: str, variant_id: str) -> None:
    s = _slide(deck, slide_id)
    v = VARIANTS.get(variant_id)
    if not v or v.role != s["role"]:
        raise DeckOpError("Этот вариант не подходит для слайда")
    why = check_variant(variant_id, s["slots"])
    if why:
        raise DeckOpError(f"Вариант недоступен: {why}")
    s["variant"] = variant_id
    _clip_groups(v, s["slots"])


def _group_list(s: dict[str, Any], group_path: str) -> tuple[Group, list[Any]]:
    g = find_group(s["variant"], group_path)
    if not g:
        raise DeckOpError("В этом блоке нельзя добавлять элементы")
    lst = get_path(s["slots"], group_path)
    if lst is None:
        _ensure_path(s["slots"], group_path + ".0")
        parent_path, _, key = group_path.rpartition(".")
        parent = get_path(s["slots"], parent_path) if parent_path else s["slots"]
        if isinstance(parent, dict):
            parent[key] = []
        lst = get_path(s["slots"], group_path)
    if not isinstance(lst, list):
        raise DeckOpError("Некорректный блок")
    return g, lst


def op_add_item(deck: dict[str, Any], slide_id: str, group_path: str, after: Optional[int] = None) -> None:
    s = _slide(deck, slide_id)
    g, lst = _group_list(s, group_path)
    if len(lst) >= g.max:
        raise DeckOpError(f"Максимум {g.max} элементов")
    pos = len(lst) if after is None else max(0, min(int(after) + 1, len(lst)))
    lst.insert(pos, copy.deepcopy(g.new()))


def op_remove_item(deck: dict[str, Any], slide_id: str, group_path: str, index: int) -> None:
    s = _slide(deck, slide_id)
    g, lst = _group_list(s, group_path)
    if len(lst) <= g.min:
        raise DeckOpError(f"Минимум {g.min} элемента" if g.min else "Нечего удалять")
    i = int(index)
    if not 0 <= i < len(lst):
        raise DeckOpError("Элемент не найден")
    lst.pop(i)


def op_delete_slide(deck: dict[str, Any], slide_id: str) -> None:
    s = _slide(deck, slide_id)
    if len(deck["slides"]) <= 1:
        raise DeckOpError("Нельзя удалить единственный слайд")
    deck["slides"].remove(s)


def op_move_slide(deck: dict[str, Any], slide_id: str, delta: int) -> None:
    s = _slide(deck, slide_id)
    lst = deck["slides"]
    i = lst.index(s)
    j = max(0, min(len(lst) - 1, i + int(delta)))
    lst.insert(j, lst.pop(i))


def apply_op(deck: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    kind = str(op.get("op") or "")
    sid = str(op.get("slide_id") or "")
    if kind == "variant":
        op_set_variant(deck, sid, str(op.get("variant") or ""))
    elif kind == "add_item":
        after = op.get("after")
        op_add_item(deck, sid, str(op.get("group") or ""), int(after) if after is not None else None)
    elif kind == "remove_item":
        op_remove_item(deck, sid, str(op.get("group") or ""), int(op.get("index") or 0))
    elif kind == "delete_slide":
        op_delete_slide(deck, sid)
    elif kind == "move_slide":
        op_move_slide(deck, sid, int(op.get("delta") or 0))
    else:
        raise DeckOpError("Неизвестная операция")
    return deck


def apply_slide_updates(deck: dict[str, Any], action: dict[str, Any]) -> list[str]:
    """Merge a model ``update`` into the deck. Returns touched slide ids.

    ``slides``: [{id, slots, role?, variant?}] replaces slots of existing slides
    (unknown ids are appended); ``insert``: [{after, role, slots}]; ``remove``: [id].
    """
    touched: list[str] = []
    by_id = {str(s["id"]): s for s in deck.get("slides") or []}
    for raw in _lst(action.get("slides")):
        if not isinstance(raw, dict):
            continue
        sid = str(raw.get("id") or "")
        cur = by_id.get(sid)
        if cur is None:
            new = normalize_slide(raw, sid=_next_id(deck))
            deck["slides"].append(new)
            by_id[new["id"]] = new
            touched.append(new["id"])
            continue
        # Модель часто присылает только изменённые поля — остальные слоты сохраняем.
        new_slots = dict(cur["slots"])
        if isinstance(raw.get("slots"), dict):
            new_slots.update(raw["slots"])
        merged = {
            "role": raw.get("role") or cur["role"],
            "variant": raw.get("variant") or cur["variant"],
            "slots": new_slots,
        }
        new = normalize_slide(merged, sid=sid)
        cur.update(new)
        touched.append(sid)
    for raw in _lst(action.get("insert")):
        if not isinstance(raw, dict):
            continue
        new = normalize_slide(raw, sid=_next_id(deck))
        after = str(raw.get("after") or "")
        idx = next((i for i, s in enumerate(deck["slides"]) if str(s["id"]) == after), len(deck["slides"]) - 1)
        deck["slides"].insert(idx + 1, new)
        touched.append(new["id"])
    remove = {str(x) for x in _lst(action.get("remove"))}
    if remove and len(deck["slides"]) > len(remove):
        deck["slides"] = [s for s in deck["slides"] if str(s["id"]) not in remove]
    del deck["slides"][MAX_SLIDES:]
    return touched


# ---------------------------------------------------------------- prompts


def schema_prompt() -> str:
    lines = []
    for role, label in ROLES:
        if role == "chart":
            lines.append(f"- chart ({label}): {CHART_SCHEMA}")
            continue
        for v in variants_for_role(role):
            lines.append(f"- {role} · {v.id} ({v.name}): {v.schema}")
    return "\n".join(lines)


def deck_prompt_json(deck: dict[str, Any], only: Optional[set[str]] = None) -> str:
    slides = [
        {"id": s["id"], "role": s["role"], "variant": s["variant"], "slots": s["slots"]}
        for s in deck.get("slides") or []
        if only is None or str(s["id"]) in only
    ]
    return json.dumps({"title": deck.get("title"), "slides": slides}, ensure_ascii=False)


def outline_prompt(deck: dict[str, Any]) -> str:
    rows = []
    for i, s in enumerate(deck.get("slides") or []):
        title = _s(s["slots"].get("title") or s["slots"].get("claim") or s["slots"].get("ask") or s["slots"].get("quote"))
        rows.append(f"{i + 1}. {s['id']} {s['role']}/{s['variant']}: {title[:80]}")
    return "\n".join(rows)
