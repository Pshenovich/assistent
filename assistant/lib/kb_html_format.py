"""Конвертация HTML базы знаний (Bitrix24 и др.) в читаемый Rich Markdown."""

from __future__ import annotations

import html
import re

_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", re.IGNORECASE | re.DOTALL)
_HEADING_RE = re.compile(
    r"<h([1-6])\b[^>]*>(.*?)</h\1>",
    re.IGNORECASE | re.DOTALL,
)
_LINK_RE = re.compile(
    r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_SECTION_START_RE = re.compile(
    r"(?P<label>"
    r"Корпоративный диск|"
    r"Тестовый кабинет[^:\n]{0,80}|"
    r"Номер [^:\n]{0,40}|"
    r"Звонки|"
    r"Траскрибация [^:\n]{0,40}|"
    r"ОКК кабинет[^:\n]{0,80}"
    r")",
    re.IGNORECASE,
)


def _collapse_ws(text: str) -> str:
    s = re.sub(r"[ \t]+\n", "\n", (text or "").strip())
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _format_link(label: str, url: str) -> str:
    label_text = _inline_html_to_text(label).strip()
    href = html.unescape((url or "").strip())
    if not href:
        return label_text
    if not label_text or label_text.lower() in {"ссылка", "link", "url", "перейти"}:
        return href
    if label_text == href:
        return href
    return f"{label_text}: {href}"


def _inline_html_to_text(fragment: str) -> str:
    s = fragment or ""
    s = _LINK_RE.sub(lambda m: _format_link(m.group(2), m.group(1)), s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>\s*<p\b[^>]*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?p\b[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:ul|ol|div|span|strong|b|em|i|u)\b[^>]*>", "", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    return _collapse_ws(s)


def _two_col_rows_to_sections(rows: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for label_raw, value_raw in rows:
        label = _inline_html_to_text(label_raw).strip().rstrip(":")
        value = _inline_html_to_text(value_raw).strip()
        if not label and value:
            parts.append(value)
            continue
        if not value:
            parts.append(f"**{label}:**")
            continue
        if "\n" in value or len(value) > 48:
            parts.append(f"**{label}:**\n{value}")
        else:
            parts.append(f"**{label}:** {value}")
    return "\n\n".join(parts).strip()


def _table_html_to_markdown(table_html: str) -> str:
    parsed_rows: list[list[str]] = []
    for row in _TR_RE.findall(table_html or ""):
        cells = _CELL_RE.findall(row)
        if cells:
            parsed_rows.append(list(cells))
    if not parsed_rows:
        return _inline_html_to_text(table_html)

    sections: list[str] = []
    for row in parsed_rows:
        if len(row) == 2:
            sections.append(_two_col_rows_to_sections([(row[0], row[1])]))
            continue
        if len(row) == 1:
            block = _inline_html_to_text(row[0]).strip()
            if block:
                sections.append(block)
            continue
        label = _inline_html_to_text(row[0]).strip().rstrip(":")
        rest = "\n".join(
            block
            for block in (_inline_html_to_text(cell).strip() for cell in row[1:])
            if block
        )
        if label and rest:
            sections.append(f"**{label}:**\n{rest}")
        elif label:
            sections.append(f"**{label}:**")
        elif rest:
            sections.append(rest)
    return "\n\n".join(sections).strip()


def html_to_kb_markdown(raw: str) -> str:
    """HTML блока базы знаний → структурированный markdown."""
    s = (raw or "").strip()
    if not s:
        return ""

    def _table_repl(match: re.Match[str]) -> str:
        return "\n\n" + _table_html_to_markdown(match.group(0)) + "\n\n"

    s = _TABLE_RE.sub(_table_repl, s)
    s = _HEADING_RE.sub(
        lambda m: f"\n\n## {_inline_html_to_text(m.group(2))}\n\n",
        s,
    )
    s = _LINK_RE.sub(lambda m: _format_link(m.group(2), m.group(1)), s)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</p>\s*<p\b[^>]*>", "\n\n", s, flags=re.IGNORECASE)
    s = re.sub(r"</?p\b[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.IGNORECASE)
    s = re.sub(r"</li>", "", s, flags=re.IGNORECASE)
    s = re.sub(r"</?(?:ul|ol|div)\b[^>]*>", "\n", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    return _collapse_ws(s)


def repair_mashed_kb_text(text: str) -> str:
    """Починка уже проиндексированного «слипшегося» текста (до пересинхронизации)."""
    s = (text or "").strip()
    if not s:
        return ""

    s = re.sub(r"(?P<w>[а-яёa-z])(https?://)", r"\g<w>\n\2", s, flags=re.IGNORECASE)
    s = re.sub(r"(?P<w>[а-яёa-z])(@\w+)", r"\g<w>\n\2", s, flags=re.IGNORECASE)
    s = re.sub(r"(?P<w>[а-яё])(?=[А-ЯЁ])", r"\g<w>\n\n", s)
    s = re.sub(r"(?i)(корпоративный диск)\s*(ссылка)", r"\1: \2", s)
    s = re.sub(r"(?i)(тестовый кабинет[^:\n]{0,80}?)(\s*)(testcabinet)", r"\1:\n\3", s)
    s = re.sub(r"(?i)(номер [^:\n]{0,40}?)(\s*)(\+?\d)", r"\1:\n\3", s)
    s = re.sub(r"(?i)(звонки)(\s*)(звонки подключены)", r"\1:\n\3", s)
    s = re.sub(r"(?i)(траскрибация [^:\n]{0,40}?)(\s*)(https?://)", r"\1:\n\3", s)
    s = re.sub(r"(?i)(окк кабинет[^:\n]{0,80}?)(\s*)(https?://)", r"\1:\n\3", s)
    s = _SECTION_START_RE.sub(
        lambda m: (
            m.group(0)
            if m.start() >= 2 and s[m.start() - 2 : m.start()] == "**"
            else f"\n\n{m.group('label')}"
        ),
        s,
    )
    s = re.sub(r"^\n+", "", s)
    s = re.sub(r"(?<=\S)\s+(\d+\.\s)", r"\n\1", s)
    return _collapse_ws(s)


def _convert_pipe_table_lines(text: str) -> str:
    """Rich Markdown-таблицы с пустыми ячейками ломают ответ — переводим в секции."""
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("|") or line.count("|") < 2:
            out.append(raw)
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        cells = [c for c in cells if c]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
            continue
        if len(cells) == 1:
            out.append(cells[0])
        elif len(cells) == 2:
            label = cells[0].rstrip(":")
            out.append(f"**{label}:** {cells[1]}")
        else:
            label = cells[0].rstrip(":")
            rest = "\n".join(c for c in cells[1:] if c)
            out.append(f"**{label}:**\n{rest}" if rest else f"**{label}:**")
    return "\n".join(out)


def normalize_kb_answer_markdown(text: str) -> str:
    body = _convert_pipe_table_lines(text)
    body = repair_mashed_kb_text(body)
    lines: list[str] = []
    seen_titles: set[str] = set()
    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if stripped.startswith("## "):
            title = stripped[3:].strip().lower()
            if title in seen_titles:
                continue
            seen_titles.add(title)
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()
