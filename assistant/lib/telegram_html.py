"""Безопасный HTML для Telegram (parse_mode HTML и Rich Message HTML)."""

from __future__ import annotations

import html
import re

_HTML_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_HAS_HTML_TAG_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_ALLOWED_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "a",
        "code",
        "pre",
        "br",
        "blockquote",
        "details",
        "summary",
        "tg-spoiler",
    }
)
_MD_TABLE_ROW_RE = re.compile(r"^\s*\|?.+\|.+\|?\s*$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_SPEAKER_BLOCK_RE = re.compile(
    r"^(Спикер[^\n:]*):\s*\n?(.*)$",
    re.DOTALL | re.IGNORECASE,
)
_RICH_BLOCK_RE = re.compile(
    r"<table\b|<details\b|<h[1-6]\b|<footer\b|"
    r"<tg-reference\b|<figure\b|<ul\b|<li\b",
    re.IGNORECASE,
)


def uses_html_markup(text: str) -> bool:
    return bool(_HAS_HTML_TAG_RE.search(text or ""))


def needs_rich_message(text: str) -> bool:
    s = (text or "").strip()
    if not s:
        return False
    if _RICH_BLOCK_RE.search(s):
        return True
    return _markdown_table_block(s) is not None


def html_to_plain(text: str) -> str:
    s = (text or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = _HTML_TAG_RE.sub("", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def deep_unescape(text: str) -> str:
    s = str(text or "")
    while True:
        unescaped = html.unescape(s)
        if unescaped == s:
            return s
        s = unescaped


def escape_html_text(chunk: str) -> str:
    s = re.sub(
        r"&(?!amp;|lt;|gt;|quot;|nbsp;|#\d+;|#x[\da-fA-F]+;)",
        "&amp;",
        chunk or "",
    )
    return re.sub(r"<(?![/]?br\b)", "&lt;", s, flags=re.IGNORECASE)


def _markdown_table_block(text: str) -> str | None:
    lines = (text or "").splitlines()
    for i in range(len(lines) - 1):
        if not _MD_TABLE_ROW_RE.match(lines[i] or ""):
            continue
        if not _MD_TABLE_SEP_RE.match(lines[i + 1] or ""):
            continue
        block: list[str] = [lines[i], lines[i + 1]]
        j = i + 2
        while j < len(lines) and _MD_TABLE_ROW_RE.match(lines[j] or ""):
            block.append(lines[j])
            j += 1
        return "\n".join(block)
    return None


def markdown_tables_to_pre(text: str) -> str:
    lines = (text or "").splitlines()
    if not lines:
        return text or ""
    out: list[str] = []
    i = 0
    while i < len(lines):
        if (
            i < len(lines) - 1
            and _MD_TABLE_ROW_RE.match(lines[i] or "")
            and _MD_TABLE_SEP_RE.match(lines[i + 1] or "")
        ):
            block = [lines[i], lines[i + 1]]
            j = i + 2
            while j < len(lines) and _MD_TABLE_ROW_RE.match(lines[j] or ""):
                block.append(lines[j])
                j += 1
            out.append("<pre>" + html.escape("\n".join(block)) + "</pre>")
            i = j
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.IGNORECASE | re.DOTALL)
_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


def _table_html_to_pre_lines(table_html: str) -> str:
    parsed_rows: list[list[str]] = []
    for row in _TR_RE.findall(table_html or ""):
        cell_texts = [html_to_plain(c).strip() for c in _CELL_RE.findall(row)]
        if cell_texts:
            parsed_rows.append(cell_texts)
    if not parsed_rows:
        return html_to_plain(table_html).strip()
    col_count = max(len(r) for r in parsed_rows)
    widths = [
        max((len(r[c]) if c < len(r) else 0) for r in parsed_rows)
        for c in range(col_count)
    ]
    lines: list[str] = []
    for row in parsed_rows:
        parts: list[str] = []
        for c in range(col_count):
            text = row[c] if c < len(row) else ""
            width = max(widths[c], 1)
            parts.append(text.ljust(width) if c < len(row) - 1 else text)
        lines.append("  ".join(parts))
    return "\n".join(lines)


def html_tables_to_pre(text: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        inner = _table_html_to_pre_lines(m.group(0))
        return "<pre>" + html.escape(inner.strip()) + "</pre>"

    return _TABLE_RE.sub(_repl, text or "")


def format_transcription_html(text: str) -> str:
    """Транскрипт → Rich Message таблица Спикер | Текст."""
    return speakers_transcription_table(text)


def prepare_classic_html(text: str) -> str:
    s = markdown_tables_to_pre((text or "").strip())
    s = html_tables_to_pre(s)
    return sanitize_telegram_html(s)


def prepare_rich_html(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    if not uses_html_markup(s):
        s = format_transcription_html(s)
    s = re.sub(r"</?(p|div)\b[^>]*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def sanitize_telegram_html(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return ""
    s = re.sub(
        r"<h([1-6])\b[^>]*>(.*?)</h\1>",
        lambda m: f"<b>{m.group(2).strip()}</b><br><br>",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(r"</?(p|div)\b[^>]*>", "<br><br>", s, flags=re.IGNORECASE)
    s = re.sub(r"<li\b[^>]*>", "• ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li>", "<br>", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(ul|ol)\b[^>]*>", "<br>", s, flags=re.IGNORECASE)
    s = re.sub(r"(<br\s*/?>\s*){3,}", "<br><br>", s, flags=re.IGNORECASE)
    s = re.sub(
        r"<span\b[^>]*class\s*=\s*['\"]tg-spoiler['\"][^>]*>",
        "<tg-spoiler>",
        s,
        flags=re.IGNORECASE,
    )
    s = re.sub(r"</span>", "", s, flags=re.IGNORECASE)

    out: list[str] = []
    pos = 0
    for m in re.finditer(r"<(/?)([a-zA-Z0-9-]+)([^>]*)>", s):
        if m.start() > pos:
            chunk = s[pos : m.start()]
            out.append(escape_html_text(chunk))
        closing, tag, attrs = m.group(1), m.group(2).lower(), m.group(3) or ""
        if tag not in _ALLOWED_TAGS:
            pos = m.end()
            continue
        if tag == "a" and not closing:
            href_m = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if not href_m:
                pos = m.end()
                continue
            href = html.escape(html.unescape(href_m.group(1).strip()), quote=True)
            out.append(f'<a href="{href}">')
        elif tag == "br":
            out.append("<br>")
        elif tag == "blockquote" and not closing:
            expandable = "expandable" in attrs.lower()
            out.append(
                '<blockquote expandable>' if expandable else "<blockquote>"
            )
        elif tag == "details" and not closing:
            open_attr = bool(re.search(r"\bopen\b", attrs, re.I))
            out.append('<details open>' if open_attr else "<details>")
        else:
            out.append(f"</{tag}>" if closing else f"<{tag}>")
        pos = m.end()
    tail = s[pos:]
    out.append(escape_html_text(tail))
    return "".join(out).strip()


def split_telegram_html(text: str, max_len: int = 3800) -> list[str]:
    s = (text or "").strip()
    if len(s) <= max_len:
        return [s] if s else []
    parts = re.split(r"(<br\s*/?>\s*<br\s*/?>)", s, flags=re.IGNORECASE)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        candidate = buf + part
        if len(candidate) <= max_len:
            buf = candidate
            continue
        if buf.strip():
            chunks.append(buf.strip())
        if len(part) <= max_len:
            buf = part
        else:
            plain = html_to_plain(part)
            for i in range(0, len(plain), max_len):
                chunks.append(html.escape(plain[i : i + max_len]))
            buf = ""
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [s[:max_len]]


def format_transcription_html(text: str) -> str:
    """Транскрипт → Rich Message таблица Спикер | Текст."""
    from assistant.lib.telegram_rich import speakers_transcription_table

    return speakers_transcription_table(text)
