"""Выгрузка заметки / записи журнала файлом: PDF, Word (.docx) или текст (.txt).

Содержимое то же, что у PDF (journal_pdf.note_export_row / запись журнала): заголовок, дата, ссылки, тело.
Word собираем сами (docx — zip с XML), без внешних зависимостей.
"""

from __future__ import annotations

import html as html_lib
import re
import zipfile
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape as _xml

from assistant.lib.journal_pdf import (
    _FOOTER_RE,
    _HTML_TAG_RE,
    _filename_for_row,
    _looks_like_markdown,
    _operation_label,
    build_journal_pdf,
)
from assistant.lib.usage_store import (
    journal_meta_from_raw,
    journal_source_links_from_raw,
    journal_text_from_raw,
)

FORMATS = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain; charset=utf-8",
}

_BLOCK_TAGS = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre", "tr"}
_SKIP_TAGS = {"script", "style", "head", "footer", "svg", "button"}


class _Block:
    __slots__ = ("kind", "runs", "level", "marker", "cells")

    def __init__(self, kind: str, *, level: int = 0, marker: str = "") -> None:
        self.kind = kind  # p | h1..h3 | li | quote | pre | table
        self.runs: list[tuple[str, frozenset[str], str]] = []  # (текст, стили, href)
        self.level = level
        self.marker = marker
        self.cells: list[list[str]] = []

    def text(self) -> str:
        return "".join(r[0] for r in self.runs)


class _HtmlBlocks(HTMLParser):
    """HTML заметки → плоский список блоков с форматированными фрагментами."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_Block] = []
        self.cur: _Block | None = None
        self.styles: list[str] = []
        self.href: list[str] = []
        self.lists: list[dict[str, Any]] = []
        self.quote = 0
        self.pre = 0
        self.skip = 0
        self.table: list[list[str]] | None = None
        self.cell: list[str] | None = None

    # --- блоки
    def _close(self) -> None:
        b = self.cur
        self.cur = None
        if b is None:
            return
        if b.kind != "pre":
            # Схлопываем пробелы, как браузер.
            runs = []
            for t, st, h in b.runs:
                t = re.sub(r"[ \t\r\n\xa0]+", " ", t) if t != "\n" else t
                runs.append((t, st, h))
            while runs and not runs[0][0].strip():
                runs.pop(0)
            while runs and not runs[-1][0].strip():
                runs.pop()
            if runs:
                runs[0] = (runs[0][0].lstrip(), runs[0][1], runs[0][2])
                runs[-1] = (runs[-1][0].rstrip(), runs[-1][1], runs[-1][2])
            b.runs = runs
        if b.text().strip():
            self.blocks.append(b)

    def _open(self, kind: str, **kw: Any) -> None:
        self._close()
        if self.quote and kind == "p":
            kind = "quote"
        self.cur = _Block(kind, **kw)

    def _ensure(self) -> _Block:
        if self.cur is None:
            self._open("quote" if self.quote else "p")
        return self.cur  # type: ignore[return-value]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: (v or "") for k, v in attrs}
        if tag in _SKIP_TAGS:
            self.skip += 1
            return
        if self.skip:
            return
        if self.table is not None:
            if tag == "tr":
                self.table.append([])
            elif tag in ("td", "th"):
                self.cell = []
            elif tag == "br" and self.cell is not None:
                self.cell.append(" ")
            return
        if tag == "table":
            self._close()
            self.table = []
            return
        if tag in ("ul", "ol"):
            self._close()
            task = "note-task-list" in a.get("class", "") or a.get("data-type") == "taskList"
            self.lists.append({"ordered": tag == "ol", "n": 0, "task": task})
            return
        if tag == "li":
            lst = self.lists[-1] if self.lists else {"ordered": False, "n": 0, "task": False}
            lst["n"] += 1
            checked = "checked" in a or str(a.get("data-checked", "")).lower() in ("true", "1")
            if lst["task"] or "data-checked" in a:
                marker = "☑" if checked else "☐"
            elif lst["ordered"]:
                marker = f"{lst['n']}."
            else:
                marker = "•"
            self._open("li", level=max(1, len(self.lists)), marker=marker)
            return
        if tag == "blockquote":
            self._close()
            self.quote += 1
            return
        if tag == "pre":
            self._open("pre")
            self.pre += 1
            return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._open("h" + str(min(int(tag[1]), 3)))
            return
        if tag in ("p", "div"):
            # <p> внутри пункта списка — тот же пункт.
            if self.cur is not None and self.cur.kind == "li" and not self.cur.text().strip():
                return
            self._open("p")
            return
        if tag == "br":
            self._ensure().runs.append(("\n", frozenset(self.styles), self.href[-1] if self.href else ""))
            return
        if tag == "input" and a.get("type") == "checkbox":
            return
        if tag in ("b", "strong"):
            self.styles.append("b")
        elif tag in ("i", "em"):
            self.styles.append("i")
        elif tag == "u":
            self.styles.append("u")
        elif tag in ("s", "strike", "del"):
            self.styles.append("s")
        elif tag == "code":
            self.styles.append("code")
        elif tag == "mark":
            self.styles.append("mark")
        elif tag == "a":
            self.href.append(a.get("href", ""))
            return
        else:
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.table is not None:
            if tag in ("td", "th") and self.cell is not None:
                if not self.table:
                    self.table.append([])
                self.table[-1].append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
                self.cell = None
            elif tag == "table":
                rows = [r for r in self.table if r]
                self.table = None
                if rows:
                    b = _Block("table")
                    b.cells = rows
                    self.blocks.append(b)
            return
        if tag in ("ul", "ol"):
            self._close()
            if self.lists:
                self.lists.pop()
            return
        if tag == "blockquote":
            self._close()
            self.quote = max(0, self.quote - 1)
            return
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
            self._close()
            return
        if tag in _BLOCK_TAGS or tag in ("h4", "h5", "h6"):
            if tag in ("p", "div") and self.cur is not None and self.cur.kind == "li":
                return
            self._close()
            return
        mapping = {"b": "b", "strong": "b", "i": "i", "em": "i", "u": "u", "s": "s", "strike": "s", "del": "s", "code": "code", "mark": "mark"}
        if tag in mapping:
            st = mapping[tag]
            for k in range(len(self.styles) - 1, -1, -1):
                if self.styles[k] == st:
                    del self.styles[k]
                    break
        elif tag == "a" and self.href:
            self.href.pop()

    def handle_data(self, data: str) -> None:
        if self.skip or not data:
            return
        if self.table is not None:
            if self.cell is not None:
                self.cell.append(data)
            return
        if not data.strip() and self.cur is None:
            return
        self._ensure().runs.append((data, frozenset(self.styles), self.href[-1] if self.href else ""))

    def finish(self) -> list[_Block]:
        self._close()
        return self.blocks


def _body_html(body: str) -> str:
    """Тело записи (HTML, Markdown или простой текст) → HTML."""
    s = _FOOTER_RE.sub("", body or "").strip()
    if not s:
        return ""
    if _HTML_TAG_RE.search(s) and not _looks_like_markdown(s):
        return s
    if _looks_like_markdown(s) or "**" in s:
        from assistant.lib.note_markdown import markdown_to_html

        return markdown_to_html(s)
    paras = re.split(r"\n{2,}", s)
    return "".join("<p>" + html_lib.escape(p).replace("\n", "<br>") + "</p>" for p in paras if p.strip())


def html_blocks(html: str) -> list[_Block]:
    p = _HtmlBlocks()
    p.feed(html or "")
    p.close()
    return p.finish()


def _doc_head(row: dict[str, Any]) -> dict[str, Any]:
    op = str(row.get("operation") or "")
    raw = row.get("raw_usage_json")
    raw_s = raw if isinstance(raw, str) else None
    meta = journal_meta_from_raw(raw_s)
    links = journal_source_links_from_raw(raw_s)
    body = journal_text_from_raw(raw_s).replace("\r", "").strip()
    if not body:
        raise ValueError("Пустой текст для выгрузки.")
    blocks = html_blocks(_body_html(body))
    title = str(meta.get("main_topic") or "").strip()
    if not title:
        first = next((b for b in blocks if b.kind != "table"), None)
        title = (first.text().strip()[:120] if first else "") or _operation_label(op)
        # Первая строка стала заголовком документа — не повторяем её в теле.
        if first is not None and first.kind in ("h1", "h2", "h3", "p") and first.text().strip() == title:
            blocks.remove(first)
    ts = str(row.get("ts_utc") or "").strip()
    extras = []
    if links.get("source_url"):
        extras.append(("Исходный файл: ", str(links["source_url"])))
    if links.get("telegram_link"):
        extras.append(("Сообщение в Telegram: ", str(links["telegram_link"])))
    return {
        "label": str(row.get("label") or "").strip() or _operation_label(op),
        "title": title,
        "date": ts[:16].replace("T", " ") if ts else "",
        "extras": extras,
        "blocks": blocks,
    }


# --- TXT


def _txt(head: dict[str, Any]) -> bytes:
    out = [head["title"]]
    if head["date"]:
        out.append(f"Дата: {head['date']}")
    for label, url in head["extras"]:
        out.append(label + url)
    out.append("")
    prev = ""
    for b in head["blocks"]:
        if b.kind == "table":
            out.extend(" | ".join(r) for r in b.cells)
            out.append("")
            prev = "table"
            continue
        parts = []
        for t, _st, href in b.runs:
            parts.append(f"{t} ({href})" if href and href not in t and t.strip() else t)
        text = "".join(parts).strip()
        if b.kind in ("h1", "h2", "h3"):
            if out and out[-1] != "":
                out.append("")
            out.append(text.upper() if b.kind == "h1" else text)
            out.append("")
        elif b.kind == "li":
            indent = "  " * (b.level - 1)
            out.append(indent + b.marker + " " + text.replace("\n", "\n" + indent + "  "))
        elif b.kind == "quote":
            out.append("\n".join("> " + ln for ln in text.split("\n")))
            out.append("")
        else:
            if prev == "li" and out[-1] != "":
                out.append("")
            out.append(text)
            out.append("")
        prev = b.kind
    s = "\n".join(out)
    s = re.sub(r"\n{3,}", "\n\n", s).strip() + "\n"
    return s.encode("utf-8")


# --- DOCX


def _run_xml(text: str, styles: frozenset[str] | set[str], *, size: int = 0, color: str = "") -> str:
    rpr = []
    if "code" in styles:
        rpr.append('<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>')
    if "b" in styles:
        rpr.append("<w:b/><w:bCs/>")
    if "i" in styles:
        rpr.append("<w:i/><w:iCs/>")
    if "s" in styles:
        rpr.append("<w:strike/>")
    if color:
        rpr.append(f'<w:color w:val="{color}"/>')
    if size:
        rpr.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    if "u" in styles:
        rpr.append('<w:u w:val="single"/>')
    if "mark" in styles:
        rpr.append('<w:highlight w:val="yellow"/>')
    pr = "<w:rPr>" + "".join(rpr) + "</w:rPr>" if rpr else ""
    pieces = text.split("\n")
    xml = []
    for i, piece in enumerate(pieces):
        if i:
            xml.append(f"<w:r>{pr}<w:br/></w:r>")
        if piece:
            xml.append(f'<w:r>{pr}<w:t xml:space="preserve">{_xml(piece)}</w:t></w:r>')
    return "".join(xml)


class _Docx:
    def __init__(self) -> None:
        self.body: list[str] = []
        self.rels: list[str] = []

    def link_id(self, href: str) -> str:
        rid = f"rIdL{len(self.rels) + 1}"
        self.rels.append(
            f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" '
            f'Target="{_xml(href, {chr(34): "&quot;"})}" TargetMode="External"/>'
        )
        return rid

    def runs(self, runs: list[tuple[str, frozenset[str], str]]) -> str:
        xml = []
        for t, st, href in runs:
            if href and re.match(r"^(https?:|mailto:|tg:)", href, re.I):
                xml.append(f'<w:hyperlink r:id="{self.link_id(href)}">{_run_xml(t, set(st) | {"u"}, color="2B6CB0")}</w:hyperlink>')
            else:
                xml.append(_run_xml(t, st))
        return "".join(xml)

    def para(self, inner: str, *, style: str = "", ppr: str = "") -> None:
        p = (f'<w:pStyle w:val="{style}"/>' if style else "") + ppr
        self.body.append("<w:p>" + (f"<w:pPr>{p}</w:pPr>" if p else "") + inner + "</w:p>")

    def table(self, rows: list[list[str]]) -> None:
        cols = max(len(r) for r in rows)
        col_w = 9638 // cols  # ширина текста A4 с полями 2 см, в twips
        border = "".join(
            f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
            for side in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        xml = [
            '<w:tbl><w:tblPr><w:tblW w:w="9638" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
            f"<w:tblBorders>{border}</w:tblBorders></w:tblPr><w:tblGrid>"
            + "".join(f'<w:gridCol w:w="{col_w}"/>' for _ in range(cols))
            + "</w:tblGrid>"
        ]
        for ri, r in enumerate(rows):
            xml.append("<w:tr>")
            for ci in range(cols):
                cell = r[ci] if ci < len(r) else ""
                xml.append(
                    f'<w:tc><w:tcPr><w:tcW w:w="{col_w}" w:type="dxa"/></w:tcPr><w:p>'
                    + _run_xml(cell, {"b"} if ri == 0 else set())
                    + "</w:p></w:tc>"
                )
            xml.append("</w:tr>")
        xml.append("</w:tbl>")
        self.body.append("".join(xml))
        self.para("")


_STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Arial" w:cs="Arial"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="ru-RU"/></w:rPr></w:rPrDefault>
<w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="120"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="100"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/><w:szCs w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="200" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:pBdr><w:left w:val="single" w:sz="12" w:space="8" w:color="7FA7D9"/></w:pBdr><w:ind w:left="284"/></w:pPr><w:rPr><w:color w:val="404040"/></w:rPr></w:style>
</w:styles>"""

_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>"""

_ROOT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>"""


def _docx(head: dict[str, Any]) -> bytes:
    d = _Docx()
    d.para(_run_xml(head["title"], set()), style="Title")
    meta = []
    if head["date"]:
        meta.append(_run_xml(f"{head['label']} · {head['date']}", set(), size=18, color="737373"))
    for label, url in head["extras"]:
        if meta:
            meta.append("<w:r><w:br/></w:r>")
        meta.append(_run_xml(label, set(), size=18, color="737373") + d.runs([(url, frozenset(), url)]))
    if meta:
        d.para("".join(meta), ppr='<w:spacing w:after="240"/>')
    for b in head["blocks"]:
        if b.kind == "table":
            d.table(b.cells)
        elif b.kind in ("h1", "h2", "h3"):
            d.para(d.runs(b.runs), style="Heading" + b.kind[1])
        elif b.kind == "li":
            left = 320 + 360 * (b.level - 1)
            d.para(
                _run_xml(b.marker + "\t", set()) + d.runs(b.runs),
                ppr=f'<w:tabs><w:tab w:val="left" w:pos="{left}"/></w:tabs><w:spacing w:after="60"/><w:ind w:left="{left}" w:hanging="320"/>',
            )
        elif b.kind == "quote":
            d.para(d.runs(b.runs), style="Quote")
        elif b.kind == "pre":
            d.para(_run_xml(b.text(), {"code"}), ppr='<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/>')
        else:
            d.para(d.runs(b.runs))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>'
        + "".join(d.body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="709" w:footer="709" w:gutter="0"/></w:sectPr>'
        "</w:body></w:document>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        + "".join(d.rels)
        + "</Relationships>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + _xml(head["title"][:250]) + "</dc:title></cp:coreProperties>"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        z.writestr("_rels/.rels", _ROOT_RELS_XML)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        z.writestr("docProps/core.xml", core)
    return buf.getvalue()


def build_export(row: dict[str, Any], fmt: str) -> tuple[bytes, str, str, str]:
    """Запись журнала (или note_export_row) → (байты, имя файла, подпись, MIME)."""
    fmt = (fmt or "pdf").strip().lower()
    if fmt not in FORMATS:
        raise ValueError("Неизвестный формат файла.")
    if fmt == "pdf":
        data, fname, caption = build_journal_pdf(row)
        return data, fname, caption, FORMATS["pdf"]
    head = _doc_head(row)
    data = _docx(head) if fmt == "docx" else _txt(head)
    fname = _filename_for_row(row, title=head["title"])[: -len(".pdf")] + "." + fmt
    caption = f"{head['label']}: {head['title']}"[:900]
    return data, fname, caption, FORMATS[fmt]


def note_export_for_user(telegram_user_id: str, note_id: int, fmt: str) -> tuple[bytes, str, str, str]:
    from assistant.lib.journal_pdf import note_export_row
    from assistant.stores import notes as notes_store

    note = notes_store.get_accessible_note(telegram_user_id, int(note_id))
    if not note:
        raise ValueError("Запись не найдена.")
    return build_export(note_export_row(note), fmt)


def journal_export_for_user(telegram_user_id: str, event_id: int, fmt: str) -> tuple[bytes, str, str, str]:
    from assistant.lib.usage_store import get_user_usage_event

    row = get_user_usage_event(telegram_user_id, event_id)
    if not row:
        raise ValueError("Запись не найдена.")
    if str(row.get("operation") or "") not in ("obuchat_transcribe", "summarize"):
        raise ValueError("Выгрузка недоступна для этой записи.")
    return build_export(row, fmt)
