"""Извлечение текста из документов компании (без новых зависимостей)."""

from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_DOC_BYTES = 2 * 1024 * 1024
MAX_DOC_CHARS = 50_000
ALLOWED_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".docx"}

_PRODUCT_HINTS = (
    "продукт",
    "product",
    "каталог",
    "sku",
    "прайс",
    "оферт",
    "тариф",
    "price",
)
_TEAM_HINTS = (
    "сотрудник",
    "команд",
    "staff",
    "team",
    "people",
    "оргструктур",
    "org chart",
)


def detect_doc_kind(filename: str, caption: str = "") -> str:
    blob = f"{filename} {caption}".lower()
    if any(k in blob for k in _PRODUCT_HINTS):
        return "products"
    if any(k in blob for k in _TEAM_HINTS):
        return "team"
    return "general"


def kind_label(kind: str) -> str:
    return {
        "products": "продукты",
        "team": "команда",
        "live": "живой документ",
    }.get(kind, "общий")


def decode_text(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ET.fromstring(xml)
    w_p = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    w_t = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    paras: list[str] = []
    for p in root.iter(w_p):
        line = "".join((t.text or "") for t in p.iter(w_t)).strip()
        if line:
            paras.append(line)
    return "\n".join(paras)


def extract_document_text(
    filename: str,
    data: bytes,
    mime: str = "",
) -> tuple[str, str | None]:
    if not data:
        return "", "Файл пустой."
    if len(data) > MAX_DOC_BYTES:
        return "", "Файл слишком большой (макс. 2 МБ). Пришлите .txt/.md/.csv/.docx."
    ext = Path(filename or "").suffix.lower()
    if not ext and mime:
        if "word" in mime:
            ext = ".docx"
        elif mime.startswith("text/") or mime.endswith("json"):
            ext = ".txt"
    if ext == ".pdf":
        return "", "PDF пока не поддерживается — пришлите .txt, .md, .csv или .docx."
    if ext not in ALLOWED_EXT:
        return (
            "",
            f"Формат {ext or mime or 'файла'} не поддерживается. "
            "Пришлите .txt, .md, .csv или .docx.",
        )
    try:
        if ext == ".docx":
            text = extract_docx(data)
        else:
            text = decode_text(data)
    except Exception:
        return "", "Не удалось прочитать файл. Проверьте, что это текст или Word (.docx)."
    text = text.strip()
    if not text:
        return "", "В файле нет текста."
    if len(text) > MAX_DOC_CHARS:
        text = text[: MAX_DOC_CHARS - 1] + "…"
    return text, None


_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# Встроенные форматы Excel с датой/временем (ECMA-376, 18.8.30).
_XLSX_DATE_FMT_IDS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 45, 46, 47})


def _xlsx_col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return max(n - 1, 0)


def _xlsx_date_styles(zf: zipfile.ZipFile) -> set[int]:
    """Индексы стилей ячеек (атрибут s), у которых формат — дата/время."""
    try:
        root = ET.fromstring(zf.read("xl/styles.xml"))
    except KeyError:
        return set()
    custom: set[int] = set()
    fmts = root.find(f"{_XLSX_NS}numFmts")
    for nf in fmts if fmts is not None else []:
        # Без текста в кавычках и [цвет]/[$-локаль]: «0.00" дн"» — не дата.
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", (nf.get("formatCode") or "").lower())
        if any(ch in code for ch in "dmyhs"):
            custom.add(int(nf.get("numFmtId") or 0))
    out: set[int] = set()
    xfs = root.find(f"{_XLSX_NS}cellXfs")
    for i, xf in enumerate(xfs if xfs is not None else []):
        fid = int(xf.get("numFmtId") or 0)
        if fid in _XLSX_DATE_FMT_IDS or fid in custom:
            out.add(i)
    return out


def _xlsx_serial_to_text(raw: str) -> str:
    from datetime import datetime, timedelta

    try:
        val = float(raw)
    except ValueError:
        return raw
    if not 0 <= val < 2958466:
        return raw
    dt = datetime(1899, 12, 30) + timedelta(days=val)
    if val == int(val):
        return dt.strftime("%Y-%m-%d")
    if val < 1:
        return dt.strftime("%H:%M:%S")
    return dt.strftime("%Y-%m-%d %H:%M")


_XLSX_MAX_DISTINCT = 5000
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _xlsx_number(val: str) -> float | None:
    if val.startswith("+"):  # телефоны — не числа
        return None
    raw = val.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def _fmt_num(x: float) -> str:
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    return f"{x:.2f}".rstrip("0").rstrip(".")


class _XlsxColumn:
    """Сводка столбца по всем строкам листа — даже тем, что не влезли в запрос."""

    def __init__(self) -> None:
        self.filled = 0
        self.nums = 0
        self.total = 0.0
        self.lo: float | None = None
        self.hi: float | None = None
        self.dates = 0
        self.first_date = ""
        self.last_date = ""
        self.counts: dict[str, int] = {}
        self.overflow = False

    def add(self, val: str) -> None:
        if not val:
            return
        self.filled += 1
        if _ISO_DATE_RE.match(val):
            self.dates += 1
            if not self.first_date or val < self.first_date:
                self.first_date = val
            if val > self.last_date:
                self.last_date = val
        else:
            num = _xlsx_number(val)
            if num is not None:
                self.nums += 1
                self.total += num
                self.lo = num if self.lo is None else min(self.lo, num)
                self.hi = num if self.hi is None else max(self.hi, num)
        key = val[:80]
        if key in self.counts:
            self.counts[key] += 1
        elif len(self.counts) < _XLSX_MAX_DISTINCT:
            self.counts[key] = 1
        else:
            self.overflow = True

    def describe(self, rows: int) -> str:
        if not self.filled:
            return ""
        out = f"заполнено {self.filled} из {rows}"
        if self.dates >= 0.9 * self.filled:
            return f"{out}; даты с {self.first_date} по {self.last_date}"
        if self.nums >= 0.9 * self.filled and self.nums:
            avg = self.total / self.nums
            return (
                f"{out}; числа: сумма {_fmt_num(self.total)}, среднее {_fmt_num(avg)}, "
                f"мин {_fmt_num(self.lo or 0)}, макс {_fmt_num(self.hi or 0)}"
            )
        top = sorted(self.counts.items(), key=lambda kv: -kv[1])
        if not self.overflow and len(top) <= 30:
            return out + "; значения: " + ", ".join(f"{k} — {n}" for k, n in top)
        many = f"больше {_XLSX_MAX_DISTINCT}" if self.overflow else str(len(top))
        if top[0][1] == 1:
            return f"{out}; {many} разных значений, повторов нет"
        return f"{out}; {many} разных значений, чаще всего: " + ", ".join(
            f"{k} — {n}" for k, n in top[:10]
        )


def _xlsx_sheet(
    zf: zipfile.ZipFile, path: str, shared: list[str], date_styles: set[int], keep: int
) -> tuple[list[str], int, list[str], list[_XlsxColumn]]:
    """(первые строки текстом в пределах keep символов, всего строк, заголовок, сводки столбцов)."""
    lines: list[str] = []
    kept = 0
    full = True
    total = 0
    header: list[str] = []
    cols: list[_XlsxColumn] = []
    for _ev, el in ET.iterparse(zf.open(path)):
        if el.tag != f"{_XLSX_NS}row":
            continue
        cells: list[str] = []
        for c in el.iter(f"{_XLSX_NS}c"):
            t = c.get("t") or "n"
            if t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{_XLSX_NS}t"))
            else:
                v = c.find(f"{_XLSX_NS}v")
                val = (v.text or "") if v is not None else ""
                if t == "s" and val.isdigit():
                    i = int(val)
                    val = shared[i] if i < len(shared) else ""
                elif t == "b":
                    val = "ИСТИНА" if val == "1" else "ЛОЖЬ"
                elif t == "n" and val and int(c.get("s") or 0) in date_styles:
                    val = _xlsx_serial_to_text(val)
            ref = c.get("r")
            col = _xlsx_col_index(ref) if ref else len(cells)
            while len(cells) < col:
                cells.append("")
            cells.append(" ".join(val.split()))
        el.clear()
        while cells and not cells[-1]:
            cells.pop()
        if not cells:
            continue
        total += 1
        line = "\t".join(cells)
        if full and kept + len(line) + 1 <= keep:
            lines.append(line)
            kept += len(line) + 1
        else:
            full = False
        if total == 1 and all(_xlsx_number(x) is None for x in cells if x):
            header = cells
            continue
        while len(cols) < len(cells):
            cols.append(_XlsxColumn())
        for j, val in enumerate(cells):
            cols[j].add(val)
    return lines, total, header, cols


def extract_xlsx(data: bytes, *, limit: int = MAX_DOC_CHARS) -> str:
    """Листы .xlsx текстом: строка — значения через табуляцию.

    Лимит делится между листами, чтобы большой лист не вытеснил маленькие. Если строки
    не влезли, в заголовке листа сказано, сколько показано, и добавлена сводка столбцов
    по всем строкам — чтобы модель считала по всей таблице, а не по её началу.
    """
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = set(zf.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for _ev, el in ET.iterparse(zf.open("xl/sharedStrings.xml")):
            if el.tag == f"{_XLSX_NS}si":
                shared.append("".join(t.text or "" for t in el.iter(f"{_XLSX_NS}t")))
                el.clear()
    date_styles = _xlsx_date_styles(zf)
    rels: dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in names:
        for rel in ET.fromstring(zf.read("xl/_rels/workbook.xml.rels")):
            target = (rel.get("Target") or "").lstrip("/")
            rels[rel.get("Id") or ""] = target if target.startswith("xl/") else f"xl/{target}"
    sheets: list[tuple[str, str]] = []
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    for sh in wb.iter(f"{_XLSX_NS}sheet"):
        path = rels.get(sh.get(f"{_REL_NS}id") or "", "")
        if path in names:
            sheets.append((sh.get("name") or "Лист", path))

    parsed = []
    for title, path in sheets:
        lines, total, header, cols = _xlsx_sheet(zf, path, shared, date_styles, limit)
        if not total:
            continue
        summary = ""
        if len(lines) < total:
            data_rows = total - (1 if header else 0)
            items = []
            for j, col in enumerate(cols):
                desc = col.describe(data_rows)
                if desc:
                    name = header[j] if j < len(header) and header[j] else f"столбец {j + 1}"
                    items.append(f"- {name}: {desc}")
            summary = "\n".join(items)[:4000]
        parsed.append([title, lines, total, header, summary])

    # Сначала сводки, остаток строк — поровну, маленьким листам сколько им нужно.
    budget = limit - sum(len(p[4]) for p in parsed)
    sizes = {i: sum(len(x) + 1 for x in p[1]) for i, p in enumerate(parsed)}
    allow: dict[int, int] = {}
    left = sorted(sizes, key=sizes.get)
    while left:
        share = max(budget, 0) // len(left)
        i = left.pop(0)
        allow[i] = min(sizes[i], share)
        budget -= allow[i]

    parts: list[str] = []
    for i, (title, lines, total, header, summary) in enumerate(parsed):
        shown: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > allow[i] and shown:
                break
            shown.append(line)
            used += len(line) + 1
        width = max(len(header), len(lines[0].split("\t")) if lines else 0)
        head = f"Лист «{title}»: {total} строк, {width} столбцов"
        if len(shown) < total:
            head += (
                f" (показаны первые {len(shown)} строк — остальные не влезли в запрос; "
                "ниже сводка столбцов по всем строкам)"
            )
        block = head + "\n" + "\n".join(shown)
        if summary:
            block += "\nСводка по всем строкам листа:\n" + summary
        parts.append(block)
    return "\n\n".join(parts).strip()


def extract_pptx(data: bytes) -> str:
    """Текст слайдов .pptx по порядку."""
    a_t = "{http://schemas.openxmlformats.org/drawingml/2006/main}t"
    a_p = "{http://schemas.openxmlformats.org/drawingml/2006/main}p"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        slides = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[-1]),
        )
        out: list[str] = []
        for i, name in enumerate(slides, 1):
            root = ET.fromstring(zf.read(name))
            lines = [
                "".join(t.text or "" for t in p.iter(a_t)).strip() for p in root.iter(a_p)
            ]
            lines = [x for x in lines if x]
            if lines:
                out.append(f"Слайд {i}:\n" + "\n".join(lines))
    return "\n\n".join(out)
