"""Стиль «Монохром»: свои композиции макетов поверх той же модели слотов.

Чёрный, белый и бледно-голубой; крупные светлые заголовки, «пилюли»,
скруглённые панели, слайды делятся на чёрную и светлую части.
Стили — webapp/canvas-kit/themes.css (html[data-leo-theme="mono"]).
Каждая функция получает тот же Ctx, что и базовая отрисовка, и пишет
data-slot / data-group / data-item так же — режим правок работает без изменений.
"""

from __future__ import annotations

from html import escape
from typing import Any, Callable

from assistant.lib import canvas_charts
from assistant.lib import canvas_templates as ct

Ctx = ct.Ctx
_s, _lst, _dicts = ct._s, ct._lst, ct._dicts

_ARROW = (
    '<span class="m-arrow" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M17 7 7 17M7 8v9h9"/></svg></span>'
)
_CHECK = (
    '<span class="m-check" aria-hidden="true"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg></span>'
)


def _slide(surface: str, inner: str, cls: str = "") -> str:
    return f'<div class="m-slide m-{surface}{(" " + cls) if cls else ""}">{inner}</div>'


def _num(i: int) -> str:
    return f"{i + 1:02d}."


def _n(cls: str, n: int) -> str:
    """Класс с числом элементов — CSS подбирает компоновку под количество."""
    return f"{cls} n-{n}"


def _head(c: Ctx, size: str = "m-display-md", right: str = "") -> str:
    return (
        '<div class="m-head"><div class="m-head-l">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", f"m-display {size}", force=True)
        + "</div>"
        + (right or _ARROW)
        + "</div>"
    )


# ================================================================== cover


def _cover_goals(c: Ctx) -> tuple[str, str]:
    goals = _lst(c.slots.get("goals"))
    n = len(goals)
    rows = "".join(
        f'<div class="m-goal" {c.item_attrs("goals", i)}><span class="m-goal-n">{_num(i)}</span>'
        f'{c.t(f"goals.{i}", "p", "m-goal-t", force=True)}</div>'
        for i in range(n)
    )
    left = (
        '<div class="m-col">'
        + '<div class="m-row-top">' + c.t("eyebrow", "p", "m-pill") + _ARROW + "</div>"
        + c.t("title", "h1", "m-display m-push", force=True)
        + "</div>"
    )
    right = (
        '<div class="m-panel m-panel-dark m-bleed">'
        + c.t("lede", "p", "m-lede m-panel-lede")
        + c.group("goals", rows, _n("m-goals", n))
        + "</div>"
    )
    return _slide("white", '<div class="m-split m-split-60">' + left + right + "</div>"), ""


def _cover_stat(c: Ctx) -> tuple[str, str]:
    left = (
        '<div class="m-col">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h1", "m-display m-push", force=True)
        + c.t("lede", "p", "m-lede")
        + "</div>"
    )
    right = (
        '<div class="m-panel m-panel-ice m-stat">'
        + _ARROW
        + '<p class="m-stat-v">' + c.t("hero_stat.value", "span", "", force=True)
        + c.t("hero_stat.unit", "span", "m-stat-u") + "</p>"
        + c.t("hero_stat.label", "p", "m-stat-l", force=True)
        + c.t("hero_stat.note", "p", "m-pill m-pill-dark")
        + "</div>"
    )
    return _slide("black", '<div class="m-split m-split-55">' + left + right + "</div>"), ""


def _cover_min(c: Ctx) -> tuple[str, str]:
    meta = _dicts(c.slots.get("meta"))
    pills = "".join(
        f'<span class="m-meta" {c.item_attrs("meta", i)}>{c.t(f"meta.{i}.label", "span", "m-meta-l", force=True)}'
        f'{c.t(f"meta.{i}.value", "span", "m-meta-v", force=True)}</span>'
        for i in range(len(meta))
    )
    inner = (
        '<div class="m-row-top"><span class="m-logo" aria-hidden="true"><i></i><i></i><i></i><i></i></span>' + _ARROW + "</div>"
        + c.t("title", "h1", "m-display m-display-xl m-push", force=True)
        + c.t("lede", "p", "m-lede")
        + c.group("meta", pills, "m-metas")
    )
    return _slide("black", inner), ""


# ================================================================== context


def _ctx_stats(c: Ctx) -> tuple[str, str]:
    stats = _dicts(c.slots.get("stats"))
    n = len(stats)
    tiles = "".join(
        f'<div class="m-fact{" is-inverse" if i == 0 else ""}" {c.item_attrs("stats", i)}>'
        f'<span class="m-fact-ico" aria-hidden="true">→</span>'
        f'{c.t(f"stats.{i}.value", "p", "m-fact-v", force=True)}'
        f'{c.t(f"stats.{i}.label", "p", "m-fact-l", force=True)}</div>'
        for i in range(n)
    )
    head = _head(c, right=c.t("body", "p", "m-lede m-lede-sm m-head-note") or _ARROW)
    return _slide("white", head + c.group("stats", tiles, _n("m-facts", n))), ""


def _ctx_agenda(c: Ctx) -> tuple[str, str]:
    agenda = _dicts(c.slots.get("agenda"))
    n = len(agenda)
    rows = "".join(
        f'<div class="m-agenda-row" {c.item_attrs("agenda", i)}><span class="m-agenda-n">{i + 1}</span>'
        f'<div class="m-agenda-b">{c.t(f"agenda.{i}.title", "p", "m-agenda-t", force=True)}'
        f'{c.t(f"agenda.{i}.note", "p", "m-agenda-note")}</div></div>'
        for i in range(n)
    )
    left = '<div class="m-col">' + c.t("title", "h2", "m-display", force=True) + '<span class="m-push">' + _ARROW + "</span></div>"
    return _slide("black", '<div class="m-split m-split-40">' + left + c.group("agenda", rows, _n("m-agenda", n)) + "</div>"), ""


# ================================================================== insight


def _callout(c: Ctx) -> tuple[str, str]:
    inner = (
        '<div class="m-statement">'
        + c.t("label", "p", "m-pill m-pill-dark")
        + c.t("claim", "h2", "m-claim", force=True)
        + c.t("note", "p", "m-lede m-center-t")
        + _ARROW
        + "</div>"
    )
    return _slide("ice", c.t("eyebrow", "p", "m-pill") + inner), ""


def _quote(c: Ctx) -> tuple[str, str]:
    inner = (
        '<span class="m-qmark" aria-hidden="true">“</span>'
        + c.t("quote", "blockquote", "m-quote", force=True)
        + '<div class="m-quote-by">' + c.t("who", "p", "m-pill m-pill-dark") + c.t("context", "p", "m-lede m-lede-sm") + "</div>"
    )
    return _slide("ice", inner), ""


def _evidence(c: Ctx) -> tuple[str, str]:
    ev = _dicts(c.slots.get("evidence"))
    n = len(ev)
    stacked = any(len(_s(e.get("fact"))) > ct.EVIDENCE_FACT_INLINE_MAX for e in ev)
    rows = "".join(
        f'<div class="m-ev" {c.item_attrs("evidence", i)}>'
        f'{c.t(f"evidence.{i}.fact", "p", "m-ev-f", force=True)}'
        f'{c.t(f"evidence.{i}.text", "p", "m-ev-t", force=True)}</div>'
        for i in range(n)
    )
    left = (
        '<div class="m-col">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("claim", "h2", "m-display m-display-md m-push", force=True)
        + c.t("note", "p", "m-lede m-lede-sm")
        + "</div>"
    )
    cls = _n("m-evs", n) + (" is-stacked" if stacked else "")
    right = '<div class="m-panel m-panel-dark m-bleed">' + c.group("evidence", rows, cls) + "</div>"
    return _slide("white", '<div class="m-split m-split-45">' + left + right + "</div>"), ""


# ================================================================== metrics


def _kpi_tile(c: Ctx, i: int, k: dict[str, Any], tone: str) -> str:
    delta_html = ""
    if _s(k.get("delta")):
        delta_html = (
            f'<p class="m-delta">{c.t(f"kpis.{i}.delta", "span")}'
            f' {c.t(f"kpis.{i}.delta_note", "span", "m-delta-note")}</p>'
        )
    series = [x for x in _lst(k.get("series")) if isinstance(x, (int, float))]
    sp = canvas_charts.spark(series, W=220, H=40) if len(series) >= 4 else ""
    return (
        f'<div class="m-tile m-tile-{tone}" {c.item_attrs("kpis", i)}>'
        f'<p class="m-tile-v">{c.t(f"kpis.{i}.value", "span", "", force=True)}'
        f'{c.t(f"kpis.{i}.unit", "span", "m-tile-u")}</p>'
        f'{delta_html}{sp}{c.t(f"kpis.{i}.label", "p", "m-tile-l", force=True)}</div>'
    )


def _metrics_kpi(c: Ctx) -> tuple[str, str]:
    kpis = _dicts(c.slots.get("kpis"))
    n = len(kpis)
    tones = ("ice", "graphite", "graphite", "ice", "ice", "graphite")
    tiles = "".join(_kpi_tile(c, i, k, tones[i % len(tones)]) for i, k in enumerate(kpis))
    left = (
        '<div class="m-col">'
        + c.t("title", "h2", "m-display m-display-md", force=True)
        + c.t("eyebrow", "p", "m-pill")
        + c.t("note", "p", "m-lede m-lede-sm m-push")
        + "</div>"
    )
    right = '<div class="m-panel m-panel-dark m-bleed">' + c.group("kpis", tiles, _n("m-tiles", n)) + "</div>"
    split = "m-split-45" if n <= 4 else "m-split-35"
    return _slide("white", f'<div class="m-split {split}">' + left + right + "</div>"), ""


def _metrics_hero(c: Ctx) -> tuple[str, str]:
    cmp_ = _dicts(c.slots.get("compare"))
    tiles = "".join(
        f'<div class="m-cmp" {c.item_attrs("compare", i)}>{c.t(f"compare.{i}.value", "p", "m-cmp-v", force=True)}'
        f'{c.t(f"compare.{i}.label", "p", "m-cmp-l", force=True)}</div>'
        for i in range(len(cmp_))
    )
    left = (
        '<div class="m-col">'
        + c.t("kpis.0.label", "p", "m-pill", force=True)
        + '<p class="m-hero-v m-push">' + c.t("kpis.0.value", "span", "", force=True) + c.t("kpis.0.unit", "span", "m-hero-u") + "</p>"
        + c.t("note", "p", "m-lede")
        + "</div>"
    )
    right = '<div class="m-col m-col-end">' + _ARROW + c.group("compare", tiles, "m-cmps") + "</div>"
    return _slide("black", '<div class="m-split m-split-60">' + left + right + "</div>"), ""


# ================================================================== charts


def _chart_side(c: Ctx) -> str:
    notes = _dicts(c.slots.get("notes"))
    cards = "".join(
        f'<div class="m-note" {c.item_attrs("notes", i)}>{c.t(f"notes.{i}.label", "p", "m-note-l", force=True)}'
        f'{c.t(f"notes.{i}.text", "p", "m-note-t", force=True)}</div>'
        for i in range(len(notes))
    )
    src = '<p class="m-cap">Источник: ' + c.t("source", "span") + "</p>" if c.has("source") else ""
    return (
        '<div class="m-col">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", "m-display m-display-md", force=True)
        + c.t("sub", "p", "m-lede m-lede-sm")
        + c.group("notes", cards, "m-notes m-push")
        + src
        + "</div>"
    )


def _chart_frame(ctype: str) -> Callable[[Ctx], tuple[str, str]]:
    def render(c: Ctx) -> tuple[str, str]:
        data = dict(c.slots.get("chart") if isinstance(c.slots.get("chart"), dict) else {})
        data["type"] = ctype
        names = canvas_charts.legend_names(data)
        legend = ""
        if names:
            legend = '<div class="m-legend">' + "".join(
                f'<span><i style="background:{canvas_charts.C[i % 4]}"></i>{escape(n)}</span>' for i, n in enumerate(names)
            ) + "</div>"
        svg = canvas_charts.render_chart(data, 700, 430)
        panel = f'<div class="m-panel m-panel-ice m-chart">{legend}<div class="m-chart-svg">{svg}</div></div>'
        return _slide("black", '<div class="m-split m-split-38">' + _chart_side(c) + panel + "</div>"), ""

    return render


def _bar_v(c: Ctx) -> tuple[str, str]:
    ch = c.slots.get("chart") if isinstance(c.slots.get("chart"), dict) else {}
    xs = [_s(x) for x in _lst(ch.get("x"))]
    series = _dicts(ch.get("series"))
    vals = [canvas_charts._num(v) for v in _lst((series[0] if series else {}).get("values"))][: len(xs)]
    if len(series) != 1 or not vals:
        return _chart_frame("bar-v")(c)
    dec = int(ch.get("dec") or 0)
    unit = _s(ch.get("unit"))
    hi = ch.get("highlight")
    mx = max(vals) or 1
    n = len(vals)
    bars = ""
    for i, v in enumerate(vals):
        h = max(8.0, 100.0 * v / mx)
        hot = (hi is not None and int(hi) == i) or (hi is None and i == n - 1)
        tone = "hot" if hot else f"t{round(3 * i / max(n - 1, 1))}"
        bars += (
            f'<div class="m-bar"><span class="m-bar-v">{canvas_charts.nf(v, dec)}{escape(unit)}</span>'
            f'<i class="m-bar-{tone}" style="height:{h:.1f}%"></i></div>'
        )
    labels = "".join(f"<span>{escape(x)}</span>" for x in xs[:n])
    chart = (
        f'<div class="m-bars" style="--n:{max(n, 1)}">'
        f'<div class="m-bars-plot">{bars}</div><div class="m-bars-x">{labels}</div></div>'
    )
    return _slide("black", '<div class="m-split m-split-40">' + _chart_side(c) + chart + "</div>"), ""


# ================================================================== comparison

_MARK = {"yes": "m-yes", "partial": "m-part", "no": "m-no"}
_MARK_GLYPH = {"yes": "✓", "partial": "~", "no": "—"}


def _cmp_two(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))[:2]
    tagged = any(_s(x.get("tag")) for x in opts)
    panels = ""
    for i, o in enumerate(opts):
        pts = _lst(o.get("points"))
        items = "".join(
            f'<li {c.item_attrs(f"options.{i}.points", j)}>{_CHECK}{c.t(f"options.{i}.points.{j}", "span", "", force=True)}</li>'
            for j in range(len(pts))
        )
        tone = "dark" if (_s(o.get("tag")) or (i == 1 and not tagged)) else "ice"
        panels += (
            f'<div class="m-panel m-panel-{tone} m-option">'
            f'<div class="m-option-h">{c.t(f"options.{i}.name", "p", "m-option-name", force=True)}{c.t(f"options.{i}.tag", "p", "m-pill")}</div>'
            + c.group(f"options.{i}.points", items, "m-points")
            + "</div>"
        )
    verdict = c.t("verdict", "p", "m-pill m-pill-dark m-verdict")
    return _slide("white", _head(c) + f'<div class="m-options">{panels}</div>' + verdict), ""


def _cmp_table(c: Ctx) -> tuple[str, str]:
    cols = _lst(c.slots.get("cols"))
    rows = _dicts(c.slots.get("rows"))
    k = len(cols)
    head = '<div class="m-trow m-thead"><span class="m-tcell m-tcrit">' + escape(
        _s(c.slots.get("criterion_label")) or "Критерий"
    ) + "</span>" + "".join(
        f'<span class="m-tcell m-tcol" data-slot="cols.{i}" data-leo-id="{c.sid}-cols.{i}">{escape(_s(h))}</span>'
        for i, h in enumerate(cols)
    ) + "</div>"
    body = ""
    for ri, r in enumerate(rows):
        cells = _lst(r.get("cells"))
        tds = ""
        for ci in range(k):
            v = _s(cells[ci]) if ci < len(cells) else ""
            key = v.lower()
            if key in _MARK:
                tds += f'<span class="m-tcell"><b class="m-mark {_MARK[key]}">{_MARK_GLYPH[key]}</b></span>'
            else:
                tds += (
                    f'<span class="m-tcell" data-slot="rows.{ri}.cells.{ci}" '
                    f'data-leo-id="{c.sid}-rows.{ri}.cells.{ci}">{escape(v)}</span>'
                )
        body += (
            f'<div class="m-trow" {c.item_attrs("rows", ri)}>'
            f'<span class="m-tcell m-tcrit" data-slot="rows.{ri}.criterion" '
            f'data-leo-id="{c.sid}-rows.{ri}.criterion">{escape(_s(r.get("criterion")))}</span>{tds}</div>'
        )
    table = f'<div class="{_n("m-table", len(rows))}" style="--k:{max(k, 1)}">{head}' + c.group("rows", body, "m-tbody") + "</div>"
    return _slide("black", _head(c) + table), ""


def _before_after(c: Ctx) -> tuple[str, str]:
    def side(key: str, tone: str, default: str) -> str:
        block = c.slots.get(key) if isinstance(c.slots.get(key), dict) else {}
        pts = _lst(block.get("points"))
        items = "".join(
            f'<li {c.item_attrs(f"{key}.points", j)}>{c.t(f"{key}.points.{j}", "span", "", force=True)}</li>'
            for j in range(len(pts))
        )
        label = c.t(f"{key}.label", "p", "m-pill") or f'<p class="m-pill">{default}</p>'
        return f'<div class="m-panel m-panel-{tone} m-ba">{label}' + c.group(f"{key}.points", items, "m-ba-list") + "</div>"

    body = (
        '<div class="m-ba-row">' + side("before", "ice", "Было")
        + '<span class="m-ba-arrow" aria-hidden="true">→</span>'
        + side("after", "dark", "Стало") + "</div>"
    )
    return _slide("white", _head(c) + body), ""


# ================================================================== content


def _content_cards(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    n = len(items)
    cards = "".join(
        f'<div class="m-card{" is-inverse" if (n >= 2 and i == 1) else ""}" {c.item_attrs("items", i)}>'
        f'<span class="m-ico" aria-hidden="true">!</span>'
        f'<p class="m-card-n">{_num(i)}</p>'
        f'{c.t(f"items.{i}.title", "p", "m-card-h", force=True)}'
        f'{c.t(f"items.{i}.text", "p", "m-card-t")}</div>'
        for i in range(n)
    )
    return _slide("white", _head(c) + c.group("items", cards, _n("m-cards", n))), ""


def _profile(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    n = len(items)
    cells = "".join(
        f'<div class="m-field" {c.item_attrs("items", i)}>{c.t(f"items.{i}.title", "p", "m-field-l", force=True)}'
        f'{c.t(f"items.{i}.text", "p", "m-field-t", force=True)}</div>'
        for i in range(n)
    )
    left = (
        '<div class="m-panel m-panel-dark m-profile">'
        + '<span class="m-avatar" aria-hidden="true"></span>'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", "m-display m-display-md m-push", force=True)
        + c.t("tag", "p", "m-pill m-pill-ice-fill")
        + "</div>"
    )
    return _slide("white", '<div class="m-split m-split-38">' + left + c.group("items", cells, _n("m-fields", n)) + "</div>"), ""


def _text_media(c: Ctx) -> tuple[str, str]:
    pts = _lst(c.slots.get("points"))
    rows = "".join(
        f'<p class="m-tag-row" {c.item_attrs("points", i)}>{_CHECK}'
        f'<span data-slot="points.{i}" data-leo-id="{c.sid}-points.{i}">{escape(_s(p))}</span></p>'
        for i, p in enumerate(pts)
    )
    mid = f"{c.sid}-media"
    src = c.media.get(mid, "")
    if src:
        meta = ct.media_meta_attrs(c.media, mid, c.val("image_prompt"))
        media = f'<div class="media-slot tpl-media m-media" data-media="{mid}" data-leo-id="{mid}"{meta}><img class="media-img" alt="" src="{escape(src)}"/></div>'
    else:
        prompt = c.val("image_prompt")
        pending = f' data-leo-img="pending" data-leo-img-prompt="{escape(prompt)}"' if prompt else ""
        media = (
            f'<div class="media-slot tpl-media tpl-media-empty m-media" data-media="{mid}" data-leo-id="{mid}"{pending}>'
            '<div><p class="m-pill" style="margin:0 auto 10px">Изображение</p>'
            '<p style="max-width:28ch">Загрузите или сгенерируйте в режиме редактирования</p></div></div>'
        )
    left = (
        '<div class="m-col">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", "m-display m-display-md", force=True)
        + c.t("body", "p", "m-lede m-lede-sm m-push", force=True)
        + c.group("points", rows, "m-tags")
        + "</div>"
    )
    return _slide("white", '<div class="m-split m-split-50">' + left + media + "</div>"), ""


def _gallery(c: Ctx) -> tuple[str, str]:
    items = ct.gallery_items(c)
    n = max(1, len(items))
    figs = "".join(
        f'<figure class="m-gfig" {c.item_attrs("images", i)}>'
        + ct.media_slot_html(c, f"{c.sid}-m-{_s(it.get('key'))}", _s(it.get("image_prompt")), "m-gmedia")
        + c.t(f"images.{i}.caption", "p", "g-cap m-gcap", force=True)
        + "</figure>"
        for i, it in enumerate(items)
    )
    grid = c.group("images", figs, f"m-ggrid g-n{n}")
    if n == 1:
        # Одно изображение почти на весь слайд: заголовок и подпись поверх.
        head = '<div class="m-gover">' + c.t("eyebrow", "p", "m-pill m-pill-light") + c.t("title", "h2", "m-gtitle") + "</div>"
        return _slide("white", grid + head, "m-gsolo"), ""
    head = (
        '<div class="m-head"><div class="m-head-l">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", "m-display m-display-md")
        + "</div>" + _ARROW + "</div>"
    )
    return _slide("white", head + grid), ""


def _custom(c: Ctx) -> tuple[str, str]:
    from assistant.lib import canvas_blocks

    surf = {"light": "white", "dark": "black", "ice": "ice"}[canvas_blocks.surface(c)]
    return _slide(surf, canvas_blocks.render_grid(c), "m-custom"), ""


# ================================================================== plan


def _timeline(c: Ctx) -> tuple[str, str]:
    periods = _dicts(c.slots.get("periods"))
    n = len(periods)
    cols = ""
    for i, p in enumerate(periods):
        items = "".join(
            f'<li {c.item_attrs(f"periods.{i}.items", j)}>{c.t(f"periods.{i}.items.{j}", "span", "", force=True)}</li>'
            for j in range(len(_lst(p.get("items"))))
        )
        cols += (
            f'<div class="m-step" {c.item_attrs("periods", i)}>'
            '<span class="m-dot"></span>'
            f'{c.t(f"periods.{i}.label", "p", "m-pill m-pill-ice")}'
            f'<div class="m-step-card">{c.t(f"periods.{i}.title", "p", "m-step-h", force=True)}'
            + c.group(f"periods.{i}.items", items, "m-step-list")
            + "</div></div>"
        )
    body = (
        f'<div class="{_n("m-steps", n)}" style="--n:{max(1, n)}"><div class="m-steps-line"></div>'
        + c.group("periods", cols, "m-steps-row")
        + "</div>"
    )
    return _slide("black", _head(c) + body), ""


def _steps(c: Ctx) -> tuple[str, str]:
    steps = _dicts(c.slots.get("steps"))
    n = len(steps)
    rows = "".join(
        f'<div class="m-prog" {c.item_attrs("steps", i)}><span class="m-prog-n">{i + 1:02d}</span>'
        f'<div class="m-prog-b">{c.t(f"steps.{i}.title", "p", "m-prog-t", force=True)}{c.t(f"steps.{i}.text", "p", "m-prog-x")}</div>'
        f'<div class="m-prog-m">{c.t(f"steps.{i}.owner", "span", "m-pill m-pill-sm")}{c.t(f"steps.{i}.due", "span", "m-pill m-pill-sm m-pill-ice-fill")}</div></div>'
        for i in range(n)
    )
    return _slide("black", _head(c) + c.group("steps", rows, _n("m-progs", n))), ""


# ================================================================== closing


def _next_steps(c: Ctx) -> tuple[str, str]:
    acts = _dicts(c.slots.get("actions"))
    n = len(acts)
    rows = "".join(
        f'<div class="m-act" {c.item_attrs("actions", i)}><span class="m-act-n">{i + 1}</span>'
        f'{c.t(f"actions.{i}.text", "p", "m-act-t", force=True)}{c.t(f"actions.{i}.due", "span", "m-pill m-pill-sm m-pill-dark")}</div>'
        for i in range(n)
    )
    contact = (
        '<p class="m-contact"><span class="m-meta-l">Контакт</span>' + c.t("contact", "span", "m-meta-v") + "</p>"
        if c.has("contact")
        else ""
    )
    left = (
        '<div class="m-col">'
        + c.t("eyebrow", "p", "m-pill")
        + c.t("title", "h2", "m-display m-display-xl m-push", force=True)
        + contact
        + "</div>"
    )
    right = '<div class="m-col m-col-end">' + _ARROW + c.group("actions", rows, _n("m-acts", n)) + "</div>"
    return _slide("white", '<div class="m-split m-split-50">' + left + right + "</div>"), ""


def _ask(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))
    n = len(opts)
    cards = "".join(
        f'<div class="m-askopt" {c.item_attrs("options", i)}><span class="m-askopt-n">{chr(65 + i)}</span>'
        f'{c.t(f"options.{i}.name", "p", "m-askopt-h", force=True)}{c.t(f"options.{i}.text", "p", "m-askopt-t")}</div>'
        for i in range(n)
    )
    inner = (
        '<div class="m-row-top">' + c.t("label", "p", "m-pill") + _ARROW + "</div>"
        + c.t("ask", "h2", "m-display m-push", force=True)
        + c.t("why", "p", "m-lede")
        + c.group("options", cards, _n("m-askopts", n))
    )
    return _slide("black", inner), ""


# ================================================================== appendix


def _appendix(c: Ctx) -> tuple[str, str]:
    cav = _lst(c.slots.get("caveats"))
    src = _dicts(c.slots.get("sources"))
    cav_html = "".join(
        f'<li {c.item_attrs("caveats", i)}><span class="m-goal-n">{_num(i)}</span>{c.t(f"caveats.{i}", "span", "", force=True)}</li>'
        for i in range(len(cav))
    )
    src_html = "".join(
        f'<div class="m-src" {c.item_attrs("sources", i)}>{c.t(f"sources.{i}.name", "p", "m-src-n", force=True)}'
        f'{c.t(f"sources.{i}.detail", "p", "m-src-d")}</div>'
        for i in range(len(src))
    )
    body = (
        '<div class="m-split m-split-55 m-appx">'
        + c.group("caveats", cav_html, "m-caveats")
        + '<div class="m-panel m-panel-ice"><p class="m-pill" style="margin-bottom:14px">Источники</p>'
        + c.group("sources", src_html, "m-srcs")
        + "</div></div>"
    )
    return _slide("white", _head(c) + body), ""


RENDERERS: dict[str, Callable[[Ctx], tuple[str, str]]] = {
    "cover.goals": _cover_goals,
    "cover.split-stat": _cover_stat,
    "cover.minimal": _cover_min,
    "context.stats": _ctx_stats,
    "context.agenda": _ctx_agenda,
    "insight.callout": _callout,
    "insight.quote": _quote,
    "insight.evidence": _evidence,
    "metrics.kpi": _metrics_kpi,
    "metrics.hero": _metrics_hero,
    "chart.bar-v": _bar_v,
    "comparison.two-col": _cmp_two,
    "comparison.table": _cmp_table,
    "comparison.before-after": _before_after,
    "content.cards": _content_cards,
    "content.profile": _profile,
    "content.text-media": _text_media,
    "content.gallery": _gallery,
    "custom.grid": _custom,
    "plan.timeline": _timeline,
    "plan.steps": _steps,
    "closing.next-steps": _next_steps,
    "closing.ask": _ask,
    "appendix.notes": _appendix,
}
for _ctype in ("bar-h", "line", "area", "grouped", "stacked100", "donut", "waterfall", "funnel", "bullet", "dumbbell", "heatmap"):
    RENDERERS[f"chart.{_ctype}"] = _chart_frame(_ctype)
