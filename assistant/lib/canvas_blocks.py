"""«Свой макет» (custom.grid): слайд из блоков на сетке.

Для редких случаев, когда ни один макет не подходит. Компоновку задаёт модель
(или пользователь словами), а вид каждого блока — стиль презентации: блоки
получают классы cb-*, их оформляют canvas-kit/templates.css (analytic) и
themes.css (mono). Поэтому свой слайд всегда в стиле презентации и
переключается вместе с ней.

slots: {eyebrow?, title?, surface?: light|dark|ice (для светлых стилей),
        rows: [{blocks: [BLOCK] 1–4}] 1–4}
BLOCK: {type, span: 1–12 (доля ширины строки), …поля типа}
"""

from __future__ import annotations

import secrets
from html import escape
from typing import Any

from assistant.lib import canvas_charts
from assistant.lib import canvas_templates as ct

Ctx = ct.Ctx
_s, _lst, _dicts = ct._s, ct._lst, ct._dicts

BLOCK_TYPES = ("heading", "text", "list", "number", "card", "quote", "pill", "image", "chart")
MAX_ROWS = 4
MAX_BLOCKS = 4

SCHEMA = (
    "eyebrow?, title?: str≤80, surface?: light|dark|ice, rows: [{blocks: [BLOCK] 1–4}] 1–4. "
    "BLOCK = {type, span: 1–12 (доля ширины строки)} + поля типа: "
    "heading {text≤80} | text {text≤300} | list {items: [str≤80] 1–6} | "
    "number {value≤10, unit?≤16, label?≤60} | card {title≤40, text?≤160, tone?: plain|accent|dark} | "
    "quote {text≤200, who?≤60} | pill {text≤30} | "
    "image {caption?≤140, image_prompt≤300 (на английском)} | chart {chart: как у роли chart}. "
    "Блоки одной строки стоят рядом слева направо, строки идут сверху вниз: «слева X, справа Y» — ОДНА строка "
    "из двух блоков; «внизу Z» — следующая строка. Несколько пунктов в одной колонке — блок list или card. "
    "Тексты — только по заметке и запросу, без заглушек вроде «Причина 1» / «Описание». "
    "Только если ни один другой макет не подходит или пользователь сам описал раскладку."
)


def _span(b: dict[str, Any]) -> int:
    try:
        n = int(b.get("span") or 0)
    except (TypeError, ValueError):
        n = 0
    return max(1, min(12, n or 6))


def _block_type(b: dict[str, Any]) -> str:
    t = str(b.get("type") or "text").strip().lower()
    return t if t in BLOCK_TYPES else "text"


def rows_of(c: Ctx) -> list[dict[str, Any]]:
    rows = _dicts(c.slots.get("rows"))[:MAX_ROWS]
    for r in rows:
        blocks = _dicts(r.get("blocks"))[:MAX_BLOCKS]
        r["blocks"] = blocks
        for b in blocks:
            if _block_type(b) == "image" and not _s(b.get("key")):
                b["key"] = secrets.token_hex(3)  # стабильная привязка картинки к блоку
    return rows


def _block(c: Ctx, r: int, i: int, b: dict[str, Any]) -> str:
    p = f"rows.{r}.blocks.{i}"
    t = _block_type(b)
    attrs = c.item_attrs(f"rows.{r}.blocks", i)
    if t == "heading":
        inner = c.t(f"{p}.text", "h3", "cb-heading", force=True)
    elif t == "text":
        inner = c.t(f"{p}.text", "p", "cb-text", force=True)
    elif t == "list":
        items = _lst(b.get("items"))
        inner = c.group(
            f"{p}.items",
            "".join(
                f'<li {c.item_attrs(f"{p}.items", j)}>{c.ico(f"{p}.items.{j}", "ico cb-li-ico")}{c.t(f"{p}.items.{j}", "span", "", force=True)}</li>'
                for j in range(len(items))
            ),
            "cb-list",
        )
    elif t == "number":
        inner = (
            '<p class="cb-num">' + c.t(f"{p}.value", "span", "", force=True)
            + c.t(f"{p}.unit", "span", "cb-num-u") + "</p>"
            + c.t(f"{p}.label", "p", "cb-num-l")
        )
    elif t == "card":
        tone = str(b.get("tone") or "plain").lower()
        tone = tone if tone in ("plain", "accent", "dark") else "plain"
        return (
            f'<div class="cb cb-card cb-tone-{tone}" {attrs}>'
            + c.t(f"{p}.title", "p", "cb-card-h", force=True)
            + c.t(f"{p}.text", "p", "cb-card-t")
            + "</div>"
        )
    elif t == "quote":
        inner = c.t(f"{p}.text", "blockquote", "cb-quote", force=True) + c.t(f"{p}.who", "p", "cb-who")
    elif t == "pill":
        inner = c.t(f"{p}.text", "p", "cb-pill", force=True)
    elif t == "image":
        mid = f"{c.sid}-m-{_s(b.get('key'))}"
        inner = ct.media_slot_html(c, mid, _s(b.get("image_prompt")), "cb-media") + c.t(f"{p}.caption", "p", "g-cap cb-cap", force=True)
    else:  # chart
        data = dict(b.get("chart") if isinstance(b.get("chart"), dict) else {})
        w = max(360, int(1120 * _span(b) / 12))
        try:
            svg = canvas_charts.render_chart(data, w, 300)
        except Exception:  # noqa: BLE001 — кривые данные не должны ронять слайд
            svg = '<p class="cb-text">Нет данных для графика</p>'
        inner = f'<div class="cb-chart">{svg}</div>'
    extra = " cb-sym" if t == "pill" and _is_symbol(_s(b.get("text"))) else ""
    return f'<div class="cb cb-is-{t}{extra}" {attrs}>{inner}</div>'


def _is_symbol(text: str) -> bool:
    """«→», «=», «+» между блоками — центрируем в своей колонке, а не прижимаем влево как метку."""
    t = text.strip()
    return 0 < len(t) <= 3 and not any(ch.isalnum() for ch in t)


def _split_side(rows: list[dict[str, Any]], c: Ctx) -> str:
    """«Фото и текст»: одна строка, фото с краю, есть заголовок → заголовок уходит в колонку текста,
    фото на всю высоту слайда. Возвращает "left"/"right" (где фото) или ""."""
    if len(rows) != 1 or not (_s(c.slots.get("title")) or _s(c.slots.get("eyebrow"))):
        return ""
    blocks = rows[0]["blocks"]
    if len(blocks) < 2:
        return ""
    types = [_block_type(b) for b in blocks]
    if types.count("image") != 1:
        return ""
    if types[0] == "image":
        return "left"
    if types[-1] == "image":
        return "right"
    return ""


def _render_split(c: Ctx, rows: list[dict[str, Any]], side: str) -> str:
    blocks = rows[0]["blocks"]
    img_i = 0 if side == "left" else len(blocks) - 1
    img_html = _block(c, 0, img_i, blocks[img_i])
    rest = "".join(_block(c, 0, i, b) for i, b in enumerate(blocks) if i != img_i)
    rest_span = sum(_span(b) for i, b in enumerate(blocks) if i != img_i)
    head = c.t("eyebrow", "p", "eyebrow") + c.t("title", "h2", "title")
    col = f'<div class="cb-col">{head}{rest}</div>'
    img_fr, col_fr = f"minmax(0, {_span(blocks[img_i])}fr)", f"minmax(0, {rest_span}fr)"
    cols = f"{img_fr} {col_fr}" if side == "left" else f"{col_fr} {img_fr}"
    inner = img_html + col if side == "left" else col + img_html
    row = (
        f'<div class="cb-row" {c.item_attrs("rows", 0)}>'
        + c.group("rows.0.blocks", inner, "cb-blocks cb-split", f"grid-template-columns:{cols}")
        + "</div>"
    )
    return c.group("rows", row, "cb-rows cb-n1 cb-rows-split")


def render_grid(c: Ctx) -> str:
    rows = rows_of(c)
    side = _split_side(rows, c)
    if side:
        return _render_split(c, rows, side)
    rows_html = ""
    for r, row in enumerate(rows):
        blocks = row["blocks"]
        cols = " ".join(f"minmax(0, {_span(b)}fr)" for b in blocks) or "minmax(0, 1fr)"
        rows_html += (
            f'<div class="cb-row" {c.item_attrs("rows", r)}>'
            + c.group(f"rows.{r}.blocks", "".join(_block(c, r, i, b) for i, b in enumerate(blocks)), "cb-blocks", f"grid-template-columns:{cols}")
            + "</div>"
        )
    head = c.t("eyebrow", "p", "eyebrow") + c.t("title", "h2", "title")
    return head + c.group("rows", rows_html, f"cb-rows cb-n{max(1, len(rows))}")


def _r_custom(c: Ctx) -> tuple[str, str]:
    return render_grid(c), ""


def surface(c: Ctx) -> str:
    v = str(c.slots.get("surface") or "light").lower()
    return v if v in ("light", "dark", "ice") else "light"


ct._reg(ct.Variant(
    "custom.grid", "custom", "Свой макет",
    SCHEMA,
    _r_custom,
    lambda s: ct._need(len(_dicts(s.get("rows"))) >= 1, "нет блоков"),
    groups=[
        ct.Group("rows", 1, MAX_ROWS, lambda: {"blocks": [{"type": "text", "span": 12, "text": "Текст"}]}),
        ct.Group("rows.*.blocks", 1, MAX_BLOCKS, lambda: {"type": "card", "span": 4, "title": "Карточка", "text": "Описание"}),
        ct.Group("rows.*.blocks.*.items", 1, 6, lambda: "Пункт"),
    ],
))
