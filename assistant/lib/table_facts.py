"""Итоги таблиц заметки, посчитанные кодом, — чтобы модель не считала сама.

Модели (особенно маленькие) путаются в арифметике: вместо суммы по кварталам берут
первый столбец. Поэтому перед запросом к модели считаем суммы и средние по строкам
и столбцам каждой таблицы и передаём их готовым блоком с пометкой «не пересчитывай».
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

MAX_TABLES = 8
MAX_LINE = 1200

_TOTAL_RE = re.compile(r"^\s*(итого|всего|сумма|total|sum)\b", re.IGNORECASE)
_NUM_RE = re.compile(r"^[+\-]?\d+(?:[.,]\d+)?$")


class _Tables(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._cell: Optional[list[str]] = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack and self._stack[-1]:
            self._cell = []
        elif tag in ("br", "p") and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._stack and self._stack[-1]:
            self._stack[-1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._stack:
            t = self._stack.pop()
            if not self._stack:  # вложенные таблицы не считаем отдельно
                self.tables.append([r for r in t if r])

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_number(cell: str) -> Optional[float]:
    """«1 200 000», «95,5», «−3,1», «12%», «1 434 ₽» → число; текст — None."""
    t = str(cell or "").strip().replace("−", "-").replace("–", "-")
    t = re.sub(r"[\s  ]", "", t)
    t = re.sub(r"(%|₽|руб\.?|\$|€|р\.?)$", "", t, flags=re.IGNORECASE)
    if not t or not _NUM_RE.match(t):
        return None
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def fmt(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        s = f"{int(round(x)):,}".replace(",", " ")
    else:
        s = f"{x:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def _table_facts(rows: list[list[str]], n: int) -> str:
    if len(rows) < 2:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header_is_text = all(parse_number(c) is None for c in rows[0][1:] if c)
    header = rows[0] if header_is_text else [f"столбец {j + 1}" for j in range(width)]
    body = rows[1:] if header_is_text else rows
    # Столбцы «Итого» и строки «Итого» в сумму не берём — иначе посчитаем дважды.
    skip_cols = {j for j, h in enumerate(header) if _TOTAL_RE.match(h or "")}
    data = [r for r in body if not _TOTAL_RE.match(r[0] or "")]
    num_cols = [
        j for j in range(1, width)
        if j not in skip_cols and sum(1 for r in data if parse_number(r[j]) is not None) >= 1
    ]
    if not num_cols:
        return ""
    lines = []
    row_parts = []
    for r in data:
        vals = [parse_number(r[j]) for j in num_cols]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2 and r[0]:
            row_parts.append(f"{r[0]}: сумма {fmt(sum(vals))}, среднее {fmt(sum(vals) / len(vals))}")
    if row_parts:
        lines.append("По строкам (" + ", ".join(header[j] or f"столбец {j + 1}" for j in num_cols) + "): " + "; ".join(row_parts))
    col_parts = []
    grand = 0.0
    for j in num_cols:
        vals = [v for v in (parse_number(r[j]) for r in data) if v is not None]
        if len(vals) >= 2:
            col_parts.append(f"{header[j] or f'столбец {j + 1}'}: сумма {fmt(sum(vals))}, среднее {fmt(sum(vals) / len(vals))}")
        grand += sum(vals)
    if col_parts:
        lines.append("По столбцам: " + "; ".join(col_parts))
    if len(num_cols) >= 2 and len(data) >= 2:
        lines.append(f"Всего по таблице: {fmt(grand)}")
    if not lines:
        return ""
    title = " | ".join(c for c in header if c)[:120]
    return f"Таблица {n} ({title}):\n" + "\n".join(line[:MAX_LINE] for line in lines)


def facts_from_html(html: str) -> str:
    """Блок с итогами всех таблиц HTML (пусто, если считать нечего)."""
    if not html or "<table" not in html.lower():
        return ""
    p = _Tables()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 — кривой HTML не должен ломать запрос
        return ""
    blocks = [b for b in (_table_facts(t, i + 1) for i, t in enumerate(p.tables[:MAX_TABLES])) if b]
    if not blocks:
        return ""
    return (
        "[Итоги таблиц заметки — посчитаны программой точно. Для сумм, средних и итогов бери числа отсюда, "
        "сам не пересчитывай]\n" + "\n\n".join(blocks) + "\n[/Итоги таблиц]"
    )
