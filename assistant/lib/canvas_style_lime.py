"""Стиль «Лайм»: свои композиции макетов поверх той же модели слотов.

По мотивам инвесторских презентаций: белые и чёрные слайды, крупные тонкие заголовки слева сверху,
кислотно-лаймовые градиенты в столбиках и плашках, колонки с тонкими разделителями, 3D-объекты
и абстракции в лаймовой гамме (webapp/canvas-kit/objects-lime, abstracts/lime-N).
Стили — webapp/canvas-kit/themes.css (html[data-leo-theme="lime"], классы .l-*).
Каждая функция получает тот же Ctx, что и базовая отрисовка, и пишет
data-slot / data-group / data-item так же — режим правок работает без изменений.
"""

from __future__ import annotations

import re
from html import escape
from typing import Any, Callable

from assistant.lib import canvas_charts
from assistant.lib import canvas_templates as ct

Ctx = ct.Ctx
_s, _lst, _dicts = ct._s, ct._lst, ct._dicts

# Знак в углу слайда — три столбика роста.
_MARK = (
    '<span class="l-mark" aria-hidden="true"><svg width="22" height="18" viewBox="0 0 22 18" fill="currentColor">'
    '<rect x="0" y="8" width="6" height="10" rx="1.5"/><rect x="8" y="3" width="6" height="15" rx="1.5"/>'
    '<rect x="16" y="0" width="6" height="18" rx="1.5"/></svg></span>'
)
_CHECK = (
    '<span class="l-check" aria-hidden="true"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" '
    'stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 5 5 9-10"/></svg></span>'
)
_NUM_RE = re.compile(r"[-−]?\d+(?:[.,]\d+)?")


def _slide(surface: str, inner: str, cls: str = "") -> str:
    """surface: white | black | mint (светло-лаймовый, для «Своего макета»)."""
    return f'<div class="l-slide l-{surface}{(" " + cls) if cls else ""}">{inner}</div>'


def _num(i: int) -> str:
    return f"{i + 1:02d}"


def _n(cls: str, n: int) -> str:
    return f"{cls} n-{n}"


def _head(c: Ctx, note_path: str = "", sub_path: str = "") -> str:
    """Заголовок слева сверху, справа — пояснение (если есть) и знак."""
    note = c.t(note_path, "p", "l-note") if note_path else ""
    return (
        '<div class="l-head"><div class="l-head-l">'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h2", "l-title", force=True)
        + (c.t(sub_path, "p", "l-sub") if sub_path else "")
        + "</div>"
        + f'<div class="l-head-r">{note}{_MARK}</div>'
        + "</div>"
    )


def _value_of(text: str) -> float | None:
    m = _NUM_RE.search(str(text or "").replace(" ", "").replace(" ", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ".").replace("−", "-"))
    except ValueError:
        return None


def _shares(values: list[float | None], floor: float = 18.0) -> list[float]:
    """Доли для высоты/ширины полос: по числам, если они есть у всех, иначе по убыванию."""
    n = len(values)
    if n and all(v is not None and v > 0 for v in values):
        mx = max(values) or 1  # type: ignore[type-var]
        return [max(floor, 100.0 * float(v) / float(mx)) for v in values]  # type: ignore[arg-type]
    return [max(floor, 100.0 - i * (60.0 / max(n - 1, 1))) for i in range(n)]


# ================================================================== cover


def _cover_goals(c: Ctx) -> tuple[str, str]:
    goals = _lst(c.slots.get("goals"))
    n = len(goals)
    cols = "".join(
        f'<div class="l-col" {c.item_attrs("goals", i)}>'
        f'{c.ico(f"goals.{i}", "ico l-col-ico") or f"""<span class="l-col-n">{_num(i)}</span>"""}'
        f'{c.t(f"goals.{i}", "p", "l-col-t", force=True)}</div>'
        for i in range(n)
    )
    inner = (
        '<div class="l-cover">'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h1", "l-display", force=True)
        + c.t("lede", "p", "l-lede")
        + "</div>"
        + c.group("goals", cols, _n("l-cols l-push", n))
    )
    return _slide("black", inner), '<div class="dec dec-lime"></div>'


def _cover_stat(c: Ctx) -> tuple[str, str]:
    left = (
        '<div class="l-cover">'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h1", "l-display", force=True)
        + c.t("lede", "p", "l-lede")
        + "</div>"
    )
    tile = (
        '<div class="l-hero-tile">'
        + c.t("hero_stat.label", "p", "l-hero-tile-l", force=True)
        + '<p class="l-hero-tile-v">' + c.t("hero_stat.value", "span", "", force=True)
        + c.t("hero_stat.unit", "span", "l-unit") + "</p>"
        + c.t("hero_stat.note", "p", "l-hero-tile-n")
        + "</div>"
    )
    return _slide("black", '<div class="l-split l-split-60">' + left + tile + "</div>"), ""


def _cover_min(c: Ctx) -> tuple[str, str]:
    meta = _dicts(c.slots.get("meta"))
    cells = "".join(
        f'<div class="l-meta" {c.item_attrs("meta", i)}>{c.t(f"meta.{i}.label", "p", "l-meta-l", force=True)}'
        f'{c.t(f"meta.{i}.value", "p", "l-meta-v", force=True)}</div>'
        for i in range(len(meta))
    )
    inner = (
        f'<div class="l-row-top">{_MARK}</div>'
        + '<div class="l-cover l-push">'
        + c.t("title", "h1", "l-display l-display-xl", force=True)
        + c.t("lede", "p", "l-lede")
        + "</div>"
        + c.group("meta", cells, "l-metas")
    )
    return _slide("black", inner), '<div class="dec dec-lime"></div>'


# ================================================================== context


def _ctx_stats(c: Ctx) -> tuple[str, str]:
    stats = _dicts(c.slots.get("stats"))
    n = len(stats)
    vals = [_value_of(_s(x.get("value"))) for x in stats]
    units = {re.sub(r"[\d\s.,−+-]", "", _s(x.get("value"))) for x in stats}
    widths = _shares(vals, floor=30.0) if len(units) <= 1 else [100.0] * n  # разные единицы — полосы одной длины
    cols = "".join(
        f'<div class="l-stat l-stat-lg" {c.item_attrs("stats", i)}>{c.t(f"stats.{i}.value", "p", "l-stat-v", force=True)}'
        f'<i class="l-stat-bar{" is-hot" if i == 0 else ""}" style="width:{widths[i]:.0f}%"></i>'
        f'{c.t(f"stats.{i}.label", "p", "l-stat-l", force=True)}</div>'
        for i in range(n)
    )
    return _slide("white", _head(c) + c.group("stats", cols, _n("l-stats l-stats-top", n)) + c.t("body", "p", "l-sub l-push")), ""


def _ctx_agenda(c: Ctx) -> tuple[str, str]:
    agenda = _dicts(c.slots.get("agenda"))
    n = len(agenda)
    rows = "".join(
        f'<div class="l-row" {c.item_attrs("agenda", i)}>'
        f'{c.ico(f"agenda.{i}", "ico l-row-ico") or f"""<span class="l-row-n">{_num(i)}</span>"""}'
        f'{c.t(f"agenda.{i}.title", "p", "l-row-t", force=True)}{c.t(f"agenda.{i}.note", "p", "l-row-x")}</div>'
        for i in range(n)
    )
    left = f'<div class="l-col-head l-col-end">{_MARK}' + c.t("title", "h2", "l-display l-display-md l-push", force=True) + "</div>"
    return _slide("white", '<div class="l-split l-split-40">' + left + c.group("agenda", rows, _n("l-rows l-rows-fill", n)) + "</div>"), ""


# ================================================================== insight


def _callout(c: Ctx) -> tuple[str, str]:
    inner = (
        f'<div class="l-row-top">{c.t("eyebrow", "p", "l-eyebrow")}{_MARK}</div>'
        + '<div class="l-statement l-push">'
        + c.t("label", "p", "l-tag")
        + c.t("claim", "h2", "l-claim", force=True)
        + c.t("note", "p", "l-lede")
        + "</div>"
    )
    return _slide("black", inner), '<div class="dec dec-lime"></div>'


def _quote(c: Ctx) -> tuple[str, str]:
    inner = (
        '<span class="l-qmark" aria-hidden="true">“</span>'
        + c.t("quote", "blockquote", "l-quote", force=True)
        + '<div class="l-quote-by l-push">' + c.t("who", "p", "l-quote-who") + c.t("context", "p", "l-sub") + "</div>"
    )
    return _slide("white", inner), ""


def _evidence(c: Ctx) -> tuple[str, str]:
    ev = _dicts(c.slots.get("evidence"))
    n = len(ev)
    widths = _shares([_value_of(_s(e.get("fact"))) for e in ev], floor=30.0)
    rows = "".join(
        f'<div class="l-prog" {c.item_attrs("evidence", i)}>'
        f'{c.t(f"evidence.{i}.text", "p", "l-prog-l", force=True)}'
        f'<div class="l-prog-track"><div class="l-prog-bar{" is-hot" if i == 0 else ""}" style="width:{widths[i]:.0f}%">'
        f'{c.t(f"evidence.{i}.fact", "span", "l-prog-v", force=True)}</div></div></div>'
        for i in range(n)
    )
    left = (
        '<div class="l-col-head">'
        + c.t("claim", "h2", "l-title", force=True)
        + c.t("note", "p", "l-sub")
        + "</div>"
    )
    body = '<div class="l-split l-split-45 l-center">' + left + c.group("evidence", rows, _n("l-progs", n)) + "</div>"
    return _slide("black", f'<div class="l-row-top"><span></span>{_MARK}</div>' + body), ""


# ================================================================== metrics


def _metrics_kpi(c: Ctx) -> tuple[str, str]:
    kpis = _dicts(c.slots.get("kpis"))
    n = len(kpis)
    vals = [_value_of(_s(k.get("value"))) for k in kpis]
    if len({_s(k.get("unit")) for k in kpis}) <= 1:
        heights = _shares(vals, floor=22.0)  # одна единица — высоты по значениям
        hot = max(range(n), key=lambda i: heights[i]) if n else -1
    else:  # разные единицы сравнивать нельзя: столбики одной высоты, выделен первый
        hot = 0
        heights = [78.0 if i == hot else 46.0 for i in range(n)]
    cols = ""
    for i, k in enumerate(kpis):
        delta = ""
        if _s(k.get("delta")):
            good = _s(k.get("delta_good")) or "up"
            sign = _s(k.get("delta")).lstrip()[:1]
            ok = (sign in "+▲" and good == "up") or (sign in "-−▼" and good == "down")
            delta = (f'<p class="l-delta{" is-good" if ok else ""}">{c.t(f"kpis.{i}.delta", "span")}'
                     f' {c.t(f"kpis.{i}.delta_note", "span", "l-delta-note")}</p>')
        cols += (
            f'<div class="l-kpi{" is-hot" if i == hot else ""}" {c.item_attrs("kpis", i)}>'
            f'{c.t(f"kpis.{i}.label", "p", "l-kpi-l", force=True)}'
            f'<p class="l-kpi-v">{c.t(f"kpis.{i}.value", "span", "", force=True)}{c.t(f"kpis.{i}.unit", "span", "l-unit")}</p>'
            f'{delta}<div class="l-kpi-plot"><i class="l-col-bar" style="height:{heights[i]:.0f}%"></i></div></div>'
        )
    return _slide("white", _head(c, "note") + c.group("kpis", cols, _n("l-kpis", n))), ""


def _metrics_hero(c: Ctx) -> tuple[str, str]:
    cmp_ = _dicts(c.slots.get("compare"))
    cells = "".join(
        f'<div class="l-stat" {c.item_attrs("compare", i)}>{c.t(f"compare.{i}.value", "p", "l-stat-v", force=True)}'
        f'{c.t(f"compare.{i}.label", "p", "l-stat-l", force=True)}</div>'
        for i in range(len(cmp_))
    )
    inner = (
        f'<div class="l-row-top">{c.t("kpis.0.label", "p", "l-eyebrow", force=True)}{_MARK}</div>'
        + '<p class="l-hero-v l-push">' + c.t("kpis.0.value", "span", "", force=True)
        + c.t("kpis.0.unit", "span", "l-unit") + "</p>"
        + c.t("note", "p", "l-lede")
        + c.group("compare", cells, _n("l-stats", len(cmp_)))
    )
    return _slide("black", inner), ""


# ================================================================== charts


def _chart_notes(c: Ctx) -> str:
    notes = _dicts(c.slots.get("notes"))
    cards = "".join(
        f'<div class="l-cnote" {c.item_attrs("notes", i)}>{c.t(f"notes.{i}.label", "p", "l-cnote-l", force=True)}'
        f'{c.t(f"notes.{i}.text", "p", "l-cnote-t", force=True)}</div>'
        for i in range(len(notes))
    )
    src = '<p class="l-cap">Источник: ' + c.t("source", "span") + "</p>" if c.has("source") else ""
    return c.group("notes", cards, "l-cnotes") + src


def _chart_frame(ctype: str) -> Callable[[Ctx], tuple[str, str]]:
    def render(c: Ctx) -> tuple[str, str]:
        data = dict(c.slots.get("chart") if isinstance(c.slots.get("chart"), dict) else {})
        data["type"] = ctype
        names = canvas_charts.legend_names(data)
        legend = ""
        if names:
            legend = '<div class="l-legend">' + "".join(
                f'<span><i style="background:{canvas_charts.C[i % 4]}"></i>{escape(n)}</span>' for i, n in enumerate(names)
            ) + "</div>"
        svg = canvas_charts.render_chart(data, 760, 400)
        side = '<div class="l-chart-side">' + legend + _chart_notes(c) + "</div>"
        body = f'<div class="l-chart-row"><div class="l-chart">{svg}</div>{side}</div>'
        return _slide("white", _head(c, sub_path="sub") + body), ""

    return render


def _bar_v(c: Ctx) -> tuple[str, str]:
    """«Use of Funds»: колонки с тонкими разделителями, градиентные столбики, крупные значения под ними."""
    ch = c.slots.get("chart") if isinstance(c.slots.get("chart"), dict) else {}
    xs = [_s(x) for x in _lst(ch.get("x"))]
    series = _dicts(ch.get("series"))
    vals = [canvas_charts._num(v) for v in _lst((series[0] if series else {}).get("values"))][: len(xs)]
    if len(series) != 1 or not vals or len(vals) > 8:
        return _chart_frame("bar-v")(c)
    dec = int(ch.get("dec") or 0)
    unit = _s(ch.get("unit"))
    hi = ch.get("highlight")
    mx = max(vals) or 1
    n = len(vals)
    cols = ""
    for i, v in enumerate(vals):
        h = max(6.0, 100.0 * v / mx)
        hot = (hi is not None and int(hi) == i) or (hi is None and v == mx)
        tone = "hot" if hot else f"t{min(3, round(3 * (1 - v / mx)))}"
        cols += (
            f'<div class="l-fund"><div class="l-fund-plot"><i class="l-fund-bar l-fund-{tone}" style="height:{h:.1f}%"></i></div>'
            f'<div class="l-fund-cap"><span class="l-fund-v">{canvas_charts.nf(v, dec)}{escape(unit)}</span>'
            f'<span class="l-fund-l">{escape(xs[i]) if i < len(xs) else ""}</span></div></div>'
        )
    body = f'<div class="l-funds" style="--n:{max(n, 1)}">{cols}</div>' + _chart_notes(c)
    return _slide("black", _head(c, sub_path="sub") + body), ""


# ================================================================== comparison

_MARK_CELL = {"yes": ("l-yes", "✓"), "partial": ("l-part", "~"), "no": ("l-no", "—")}


def _cmp_two(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))[:2]
    tagged = any(_s(x.get("tag")) for x in opts)
    panels = ""
    for i, o in enumerate(opts):
        pts = _lst(o.get("points"))
        items = "".join(
            f'<li {c.item_attrs(f"options.{i}.points", j)}>{c.ico(f"options.{i}.points.{j}", "ico l-li-ico") or _CHECK}'
            f'{c.t(f"options.{i}.points.{j}", "span", "", force=True)}</li>'
            for j in range(len(pts))
        )
        hot = bool(_s(o.get("tag"))) or (i == 1 and not tagged)
        panels += (
            f'<div class="l-option{" is-hot" if hot else ""}">'
            f'<div class="l-option-h">{c.t(f"options.{i}.name", "p", "l-option-name", force=True)}{c.t(f"options.{i}.tag", "p", "l-tag")}</div>'
            + c.group(f"options.{i}.points", items, "l-points")
            + "</div>"
        )
    verdict = c.t("verdict", "p", "l-verdict")
    return _slide("white", _head(c) + f'<div class="l-options">{panels}</div>' + verdict), ""


def _cmp_table(c: Ctx) -> tuple[str, str]:
    cols = _lst(c.slots.get("cols"))
    rows = _dicts(c.slots.get("rows"))
    k = len(cols)
    head = '<div class="l-trow l-thead"><span class="l-tcell l-tcrit">' + escape(
        _s(c.slots.get("criterion_label")) or "Критерий"
    ) + "</span>" + "".join(
        f'<span class="l-tcell" data-slot="cols.{i}" data-leo-id="{c.sid}-cols.{i}">{escape(_s(h))}</span>'
        for i, h in enumerate(cols)
    ) + "</div>"
    body = ""
    for ri, r in enumerate(rows):
        cells = _lst(r.get("cells"))
        tds = ""
        for ci in range(k):
            v = _s(cells[ci]) if ci < len(cells) else ""
            mark = _MARK_CELL.get(v.lower())
            if mark:
                tds += f'<span class="l-tcell"><b class="l-tmark {mark[0]}">{mark[1]}</b></span>'
            else:
                tds += f'<span class="l-tcell" data-slot="rows.{ri}.cells.{ci}" data-leo-id="{c.sid}-rows.{ri}.cells.{ci}">{escape(v)}</span>'
        body += (
            f'<div class="l-trow" {c.item_attrs("rows", ri)}>'
            f'<span class="l-tcell l-tcrit" data-slot="rows.{ri}.criterion" data-leo-id="{c.sid}-rows.{ri}.criterion">'
            f'{escape(_s(r.get("criterion")))}</span>{tds}</div>'
        )
    table = f'<div class="{_n("l-table", len(rows))}" style="--k:{max(k, 1)}">{head}' + c.group("rows", body, "l-tbody") + "</div>"
    return _slide("white", _head(c) + table), ""


def _before_after(c: Ctx) -> tuple[str, str]:
    def side(key: str, tone: str, default: str) -> str:
        block = c.slots.get(key) if isinstance(c.slots.get(key), dict) else {}
        pts = _lst(block.get("points"))
        items = "".join(
            f'<li {c.item_attrs(f"{key}.points", j)}>{c.ico(f"{key}.points.{j}", "ico l-li-ico")}'
            f'{c.t(f"{key}.points.{j}", "span", "", force=True)}</li>'
            for j in range(len(pts))
        )
        label = c.t(f"{key}.label", "p", "l-tag") or f'<p class="l-tag">{default}</p>'
        return f'<div class="l-ba l-ba-{tone}">{label}' + c.group(f"{key}.points", items, "l-ba-list") + "</div>"

    body = (
        '<div class="l-ba-row">' + side("before", "light", "Было")
        + '<span class="l-ba-arrow" aria-hidden="true">→</span>'
        + side("after", "dark", "Стало") + "</div>"
    )
    return _slide("white", _head(c) + body), ""


# ================================================================== content


def _content_cards(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    n = len(items)
    used: list[str] = []
    cards = ""
    for i, it in enumerate(items):
        vis, mid, src, vis_html = ct.card_visual(c, i, it, used, "lime-light")
        a3 = (
            f'<div class="a3 l-a3{" a3-photo" if src else ""}" data-media="{mid}" data-leo-id="{mid}" data-a3="{vis}"'
            f'{ct.media_meta_attrs(c.media, mid) if src else ""}>{vis_html}</div>'
        )
        cards += (
            f'<div class="l-card" {c.item_attrs("items", i)}>{a3}<div class="l-card-b">'
            f'<span class="l-card-n">{_num(i)}</span>'
            f'{c.t(f"items.{i}.title", "p", "l-card-h", force=True)}'
            f'{c.t(f"items.{i}.text", "p", "l-card-t")}</div></div>'
        )
    return _slide("white", _head(c) + c.group("items", cards, _n("l-cards", n))), ""


def _profile(c: Ctx) -> tuple[str, str]:
    items = _dicts(c.slots.get("items"))
    n = len(items)
    cells = "".join(
        f'<div class="l-field" {c.item_attrs("items", i)}>{c.ico(f"items.{i}", "ico l-field-ico")}'
        f'{c.t(f"items.{i}.title", "p", "l-field-l", force=True)}{c.t(f"items.{i}.text", "p", "l-field-t", force=True)}</div>'
        for i in range(n)
    )
    obj = ct._object_data_url(ct._pick_visual(_s(c.slots.get("title")) or c.sid, []), "lime-dark")
    bg = f' style="background-image:url(\'{obj}\')"' if obj else ""
    left = (
        f'<div class="l-panel l-panel-obj"><div class="l-panel-art" aria-hidden="true"{bg}></div>'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h2", "l-title l-push", force=True)
        + c.t("tag", "p", "l-tag")
        + "</div>"
    )
    return _slide("white", '<div class="l-split l-split-38 l-split-full">' + left + c.group("items", cells, _n("l-fields l-fields-lg", n)) + "</div>"), ""


def _text_media(c: Ctx) -> tuple[str, str]:
    pts = _lst(c.slots.get("points"))
    rows = "".join(
        f'<li {c.item_attrs("points", i)}>{c.ico(f"points.{i}", "ico l-li-ico") or _CHECK}'
        f'<span {c.slot(f"points.{i}")}>{escape(c.val(f"points.{i}"))}</span></li>'
        for i in range(len(pts))
    )
    mid = f"{c.sid}-media"
    media = ct.media_slot_html(c, mid, c.val("image_prompt"), "l-media")
    left = (
        '<div class="l-col-head">'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h2", "l-title", force=True)
        + c.t("body", "p", "l-lede l-lede-dark", force=True)
        + c.group("points", rows, "l-points l-push")
        + "</div>"
    )
    return _slide("white", '<div class="l-split l-split-50">' + left + media + "</div>"), ""


def _gallery(c: Ctx) -> tuple[str, str]:
    items = ct.gallery_items(c)
    n = max(1, len(items))
    figs = "".join(
        f'<figure class="l-gfig" {c.item_attrs("images", i)}><span class="l-col-n">{_num(i)}</span>'
        + c.t(f"images.{i}.caption", "p", "g-cap l-gcap", force=True)
        + ct.media_slot_html(c, f"{c.sid}-m-{_s(it.get('key'))}", _s(it.get("image_prompt")), "l-gmedia")
        + "</figure>"
        for i, it in enumerate(items)
    )
    return _slide("white", _head(c) + c.group("images", figs, f"l-gallery g-n{n}")), ""


def _custom(c: Ctx) -> tuple[str, str]:
    from assistant.lib import canvas_blocks

    surf = {"light": "white", "dark": "black", "ice": "mint"}[canvas_blocks.surface(c)]
    return _slide(surf, canvas_blocks.render_grid(c), "l-custom"), ""


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
            f'<div class="l-period{" is-hot" if i == 0 else ""}" {c.item_attrs("periods", i)}><span class="l-dot"></span>'
            f'{c.t(f"periods.{i}.label", "p", "l-period-l")}'
            f'{c.t(f"periods.{i}.title", "p", "l-period-h", force=True)}'
            + c.group(f"periods.{i}.items", items, "l-period-list")
            + "</div>"
        )
    body = f'<div class="{_n("l-periods", n)}" style="--n:{max(1, n)}"><div class="l-periods-line"></div>' + c.group("periods", cols, "l-periods-row") + "</div>"
    return _slide("white", _head(c) + body), ""


def _steps(c: Ctx) -> tuple[str, str]:
    steps = _dicts(c.slots.get("steps"))
    n = len(steps)
    rows = "".join(
        f'<div class="l-step" {c.item_attrs("steps", i)}><span class="l-step-n">{c.ico(f"steps.{i}") or _num(i)}</span>'
        f'<div class="l-step-b">{c.t(f"steps.{i}.title", "p", "l-step-t", force=True)}{c.t(f"steps.{i}.text", "p", "l-step-x")}</div>'
        f'{c.t(f"steps.{i}.owner", "p", "l-step-o")}{c.t(f"steps.{i}.due", "p", "l-step-d")}</div>'
        for i in range(n)
    )
    return _slide("white", _head(c) + c.group("steps", rows, _n("l-steps", n))), ""


# ================================================================== closing


def _next_steps(c: Ctx) -> tuple[str, str]:
    acts = _dicts(c.slots.get("actions"))
    n = len(acts)
    rows = "".join(
        f'<div class="l-act" {c.item_attrs("actions", i)}><span class="l-act-n">{c.ico(f"actions.{i}") or _num(i)}</span>'
        f'{c.t(f"actions.{i}.text", "p", "l-act-t", force=True)}{c.t(f"actions.{i}.due", "p", "l-act-d")}</div>'
        for i in range(n)
    )
    contact = '<p class="l-contact">' + c.t("contact", "span") + "</p>" if c.has("contact") else ""
    left = (
        '<div class="l-col-head">'
        + c.t("eyebrow", "p", "l-eyebrow")
        + c.t("title", "h2", "l-display", force=True)
        + contact
        + "</div>"
    )
    body = '<div class="l-split l-split-50 l-center">' + left + c.group("actions", rows, _n("l-acts", n)) + "</div>"
    return _slide("black", f'<div class="l-row-top"><span></span>{_MARK}</div>' + body), '<div class="dec dec-lime is-low"></div>'


def _ask(c: Ctx) -> tuple[str, str]:
    opts = _dicts(c.slots.get("options"))
    n = len(opts)
    cards = "".join(
        f'<div class="l-askopt{" is-hot" if i == 0 else ""}" {c.item_attrs("options", i)}>'
        f'<span class="l-askopt-n">{c.ico(f"options.{i}") or chr(65 + i)}</span>'
        f'{c.t(f"options.{i}.name", "p", "l-askopt-h", force=True)}{c.t(f"options.{i}.text", "p", "l-askopt-t")}</div>'
        for i in range(n)
    )
    inner = (
        f'<div class="l-row-top">{c.t("label", "p", "l-tag")}{_MARK}</div>'
        + c.t("ask", "h2", "l-claim l-push", force=True)
        + c.t("why", "p", "l-lede")
        + c.group("options", cards, _n("l-askopts", n))
    )
    return _slide("black", inner), ""


# ================================================================== appendix


def _appendix(c: Ctx) -> tuple[str, str]:
    cav = _lst(c.slots.get("caveats"))
    src = _dicts(c.slots.get("sources"))
    cav_html = "".join(
        f'<li {c.item_attrs("caveats", i)}><span class="l-row-n">{_num(i)}</span>{c.t(f"caveats.{i}", "span", "", force=True)}</li>'
        for i in range(len(cav))
    )
    src_html = "".join(
        f'<div class="l-src" {c.item_attrs("sources", i)}>{c.t(f"sources.{i}.name", "p", "l-src-n", force=True)}'
        f'{c.t(f"sources.{i}.detail", "p", "l-src-d")}</div>'
        for i in range(len(src))
    )
    if not c.has("title"):
        ct.set_path(c.slots, "title", "Оговорки и источники")
    body = (
        '<div class="l-split l-split-55">'
        + c.group("caveats", cav_html, "l-caveats")
        + '<div class="l-panel l-panel-light"><p class="l-eyebrow">Источники</p>'
        + c.group("sources", src_html, "l-srcs")
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
