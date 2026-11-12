"""Строка в таблицу документа — для сценариев («Если → То: добавить строку в таблицу»).

Таблица в документе — обычный HTML <table> (первая строка — шапка). Код находит таблицы,
дописывает строку в конец (первая ячейка с data-leo-item — по нему строку потом убирают)
и ставит столбец «Источник»: что внёс Лео и откуда. Модель сюда не ходит.
"""

from __future__ import annotations

import html as _h
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from assistant.lib.board_doc import table_rows

SOURCE_COLUMN = "Источник"

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.S | re.I)
_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<(t[dh])\b[^>]*>.*?</\1>", re.S | re.I)
_HEADING_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")

_NUM_COL = re.compile(r"сумм|стоим|цен|руб|₽|оплач|платеж|платёж|итог|amount|price", re.I)
_DATE_COL = re.compile(r"дат|когда|срок|date", re.I)


def _text(s: str) -> str:
    return " ".join(_h.unescape(_TAG_RE.sub(" ", s or "")).split())


def parse_note_ref(ref: Any) -> tuple[Optional[int], str]:
    """Ссылка на документ или его номер → (note_id, sheet_id). Лист — из &sheet= (иначе основной)."""
    raw = str(ref or "").strip()
    if raw.isdigit():
        return int(raw), ""
    try:
        u = urlparse(raw)
    except ValueError:
        return None, ""
    qs = parse_qs(u.query)
    if u.fragment and "=" in u.fragment:
        qs = {**parse_qs(u.fragment), **qs}
    nid = (qs.get("note") or qs.get("n") or [""])[0]
    if not str(nid).isdigit():
        m = re.search(r"[?&#;]note=(\d+)", raw)
        nid = m.group(1) if m else ""
    sheet = (qs.get("sheet") or [""])[0]
    return (int(nid) if str(nid).isdigit() else None), (sheet if str(sheet).isdigit() else "")


def tables_in(html_text: str) -> list[dict[str, Any]]:
    """Таблицы документа: номер, подпись (заголовок над таблицей или шапка), столбцы, сколько строк."""
    src = str(html_text or "")
    out = []
    for i, m in enumerate(_TABLE_RE.finditer(src)):
        rows = table_rows(m.group(0))
        cols = rows[0] if rows else []
        label = ""
        heads = list(_HEADING_RE.finditer(src[max(0, m.start() - 2000):m.start()]))
        if heads:
            label = _text(heads[-1].group(2))[:80]
        if not label:
            label = " · ".join(c for c in cols if c)[:80] or f"Таблица {i + 1}"
        out.append({"index": i, "label": label, "columns": cols, "rows": max(0, len(rows) - 1)})
    return out


def data_columns(columns: list[str]) -> list[str]:
    """Столбцы, которые заполняет модель (без «Источника» — его ставит код)."""
    return [c for c in columns if c and c.strip().lower() != SOURCE_COLUMN.lower()]


def check_value(column: str, value: str) -> str:
    """Проверка кодом: в сумме и дате должна быть цифра, иначе ячейку оставляем пустой."""
    v = " ".join(str(value or "").split())[:300]
    if not v:
        return ""
    if (_NUM_COL.search(column) or _DATE_COL.search(column)) and not re.search(r"\d", v):
        return ""
    return v


def _cell(inner: str, tag: str = "td", attrs: str = "") -> str:
    return f'<{tag} colspan="1" rowspan="1"{attrs}><p>{inner}</p></{tag}>'


def source_html(url: str, label: str) -> str:
    """«Лео · сообщение» со ссылкой на сообщение (или без ссылки — у обычных групп её нет)."""
    if url:
        return f'Лео · <a href="{_h.escape(url)}" target="_blank">{_h.escape(label or "сообщение")}</a>'
    return "Лео · " + _h.escape(label or "сообщение")


def _add_source_column(table: str) -> str:
    rows = list(_ROW_RE.finditer(table))
    if not rows:
        return table
    out, pos = [], 0
    for n, r in enumerate(rows):
        out.append(table[pos:r.start()])
        row = r.group(0)
        cell = _cell(SOURCE_COLUMN, "th") if n == 0 and "<th" in row.lower() else _cell(SOURCE_COLUMN if n == 0 else "")
        out.append(row[: row.lower().rfind("</tr>")] + cell + "</tr>")
        pos = r.end()
    out.append(table[pos:])
    return "".join(out)


def append_row(
    html_text: str, table_index: int, values: dict[str, str], *, item_id: str, source: str
) -> tuple[str, list[str]]:
    """Дописать строку в таблицу №table_index. Возвращает (новый HTML, столбцы таблицы).

    values — по названиям столбцов; «Источник» нет — добавляем столбец. Таблицы нет — LookupError.
    """
    src = str(html_text or "")
    tables = list(_TABLE_RE.finditer(src))
    if table_index < 0 or table_index >= len(tables):
        raise LookupError("Таблица не найдена")
    m = tables[table_index]
    table = m.group(0)
    cols = (table_rows(table) or [[]])[0]
    if not any(c.strip().lower() == SOURCE_COLUMN.lower() for c in cols):
        table = _add_source_column(table)
        cols = cols + [SOURCE_COLUMN]
    cells = []
    for i, c in enumerate(cols):
        inner = source if c.strip().lower() == SOURCE_COLUMN.lower() else _h.escape(values.get(c, ""), quote=False)
        cells.append(_cell(inner, attrs=f' data-leo-item="{item_id}"' if i == 0 else ""))
    row = "<tr>" + "".join(cells) + "</tr>"
    low = table.lower()
    at = low.rfind("</tbody>")
    if at < 0:
        at = low.rfind("</table>")
    table = table[:at] + row + table[at:]
    return src[: m.start()] + table + src[m.end():], cols


def remove_row(html_text: str, item_id: str) -> tuple[str, bool]:
    """Убрать строку, которую вписал Лео (по data-leo-item первой ячейки)."""
    src = str(html_text or "")
    needle = f'data-leo-item="{item_id}"'
    for r in _ROW_RE.finditer(src):
        if needle in r.group(0):
            return src[: r.start()] + src[r.end():], True
    return src, False
