"""Rich Markdown для sendRichMessage (таблицы, чеклисты, сноски)."""

from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

_MONTHS_RU = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

from assistant.lib.telegram_html import deep_unescape, uses_html_markup
from assistant.lib.telegram_rich import promote_section_headings

_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<(t[hd])\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>", re.IGNORECASE)
_FOOTER_RE = re.compile(r"<footer\b[^>]*>(.*?)</footer>", re.IGNORECASE | re.DOTALL)
_LI_RE = re.compile(r"<li\b([^>]*)>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_SECTION_HEADING_MD_RE = re.compile(r"^#{1,3}\s+\S", re.MULTILINE)
_BOLD_HEADING_MD_RE = re.compile(r"^\*\*[^*]+\*\*\s*$", re.MULTILINE)
_TASKS_SECTION_MD_RE = re.compile(
    r"(?:^|\n)(?:#{1,3}\s*|\*\*)📋\s*Задачи(?:\*\*)?\s*\n[\s\S]*?"
    r"(?=\n(?:#{1,3}\s+|\*\*)[^\n]|\Z)",
    re.IGNORECASE,
)
_ZOOM_INLINE_RE = re.compile(
    r"\[\^zoom\]|"
    r"\[🔗\s*Zoom[^\]]*\]\([^)]+\)|"
    r"\[Zoom[^\]]*\]\([^)]*zoom\.us[^)]*\)|"
    r"🔗\s*<a\b[^>]*href\s*=\s*['\"][^'\"]*zoom\.us[^'\"]*['\"][^>]*>.*?</a>|"
    r"<a\b[^>]*href\s*=\s*['\"][^'\"]*zoom\.us[^'\"]*['\"][^>]*>.*?</a>|"
    r"<a\b[^>]*href\s*=\s*['\"]#zoom['\"][^>]*>.*?</a>|"
    r"<footer\b[^>]*>.*?</footer>",
    re.IGNORECASE | re.DOTALL,
)


def _md_escape_cell(text: str) -> str:
    s = deep_unescape(str(text or "").strip())
    return (
        s.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", "")
    )


def _md_link(label: str, url: str) -> str:
    esc = _md_escape_cell(label).replace("[", "\\[").replace("]", "\\]")
    url = str(url or "").strip()
    if url:
        return f"[{esc}]({url})"
    return esc


def _inline_html_to_md(fragment: str) -> str:
    s = fragment or ""

    def repl_tag(m: re.Match[str]) -> str:
        tag = m.group(1).lower()
        attrs = m.group(2) or ""
        inner = m.group(3) or ""
        converted = _inline_html_to_md(inner)
        if tag in {"b", "strong"}:
            return f"**{converted}**" if converted else ""
        if tag in {"i", "em"}:
            return f"*{converted}*" if converted else ""
        if tag in {"u", "ins"}:
            return f"__{converted}__" if converted else ""
        if tag in {"s", "strike", "del"}:
            return f"~~{converted}~~" if converted else ""
        if tag == "code":
            return f"`{converted}`" if converted else ""
        if tag == "tg-spoiler":
            return f"||{converted}||" if converted else ""
        if tag == "a":
            href_m = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', attrs, re.I)
            if href_m:
                href = href_m.group(1).strip()
                if href.startswith("#") or "zoom.us" in href.lower():
                    return ""
                return _md_link(converted, href)
        if tag == "br":
            return "\n"
        return converted

    prev = None
    while prev != s:
        prev = s
        s = re.sub(
            r"<(b|strong|i|em|u|ins|s|strike|del|code|tg-spoiler|a)(\b[^>]*)?>(.*?)</\1>",
            repl_tag,
            s,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    s = _TAG_RE.sub("", s)
    return html.unescape(s)


def _html_table_to_md(table_html: str) -> str:
    rows: list[list[str]] = []
    for row in _TR_RE.findall(table_html or ""):
        cells = [
            _inline_html_to_md(inner).strip()
            for _tag, inner in _CELL_RE.findall(row)
        ]
        if cells:
            rows.append(cells)
    if not rows:
        return _inline_html_to_md(table_html)
    col_count = max(len(r) for r in rows)
    lines: list[str] = []
    for i, row in enumerate(rows):
        texts = row + [""] * (col_count - len(row))
        lines.append("| " + " | ".join(_md_escape_cell(t) for t in texts) + " |")
        if i == 0:
            lines.append("| " + " | ".join(":---" for _ in range(col_count)) + " |")
    return "\n".join(lines)


def _html_ul_to_md(ul_html: str) -> str:
    items = _LI_RE.findall(ul_html or "")
    lines: list[str] = []
    for attrs, inner in items:
        text = _inline_html_to_md(inner).strip()
        if not text:
            continue
        if re.search(r"\bchecked\b", attrs or "", re.I):
            lines.append(f"- [x] {text}")
        else:
            lines.append(f"- {text}")
    return "\n".join(lines)


def _html_orphan_li_to_md(html_text: str) -> str:
    def _repl(m: re.Match[str]) -> str:
        return "\n" + _html_ul_to_md(m.group(0)) + "\n"

    return _LI_RE.sub(
        lambda m: f"\n{_html_ul_to_md(m.group(0))}\n" if "<ul" not in html_text else m.group(0),
        html_text,
    )


def _split_inline_bullets(line: str) -> list[str]:
    s = (line or "").strip()
    if not s:
        return []
    if " • " not in s and not s.startswith("•"):
        return [s]
    parts = re.split(r"\s*•\s+", s.lstrip("•").strip())
    out: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if part.startswith("- "):
            out.append(part)
        else:
            out.append(f"- {part}")
    return out or [s]


def _normalize_summary_markdown(text: str) -> str:
    lines: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        bold_m = re.match(r"^\*\*([^*]+)\*\*$", stripped)
        if bold_m and not stripped.startswith("**http"):
            lines.append(f"## {bold_m.group(1).strip()}")
            continue
        if " • " in stripped or stripped.startswith("• "):
            lines.extend(_split_inline_bullets(stripped))
            continue
        lines.append(line)

    s = "\n".join(lines)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    s = re.sub(r"([^\n])\n(## )", r"\1\n\n\2", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def prepare_journal_qa_markdown(text: str) -> str:
    """Нормализует ответ journal_qa для sendRichMessage (Rich Markdown)."""
    body = (text or "").strip()
    if not body:
        return ""
    if uses_html_markup(body):
        body = html_to_rich_markdown(body)
    else:
        body = _normalize_summary_markdown(body)
    return body.strip()


def prepare_bitrix_markdown(text: str) -> str:
    """Нормализует ответ Bitrix24 для sendRichMessage (Rich Markdown)."""
    return prepare_journal_qa_markdown(text)


def _strip_tasks_section(md: str) -> str:
    return _TASKS_SECTION_MD_RE.sub("\n", (md or "").strip()).strip()


def _strip_zoom_from_body(text: str) -> str:
    """Убирает inline-ссылки на Zoom из тела (остаётся только footer в конце)."""
    s = (text or "").strip()
    s = _ZOOM_INLINE_RE.sub("", s)
    s = re.sub(
        r"<p>\s*(?:<a\b[^>]*href\s*=\s*['\"]#[^'\"]+['\"][^>]*>.*?</a>\s*)"
        r"(?:·\s*<a\b[^>]*>.*?</a>\s*)?</p>",
        "",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(r"\[🔗\s*Zoom[^\]]*\]\([^)]+\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[Zoom[^\]]*\]\([^)]*zoom\.us[^)]*\)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\[\^zoom\]", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*\[\^(?:zoom|source)\]:.*$", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"(?:^|\n)\s*🔗\s*Zoom-встреча\s*(?=\n|$)", "\n", s, flags=re.IGNORECASE)
    s = re.sub(r"^\s*🔗\s*Zoom-встреча\s*$", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"^\s*🔗\s*$", "", s, flags=re.IGNORECASE | re.MULTILINE)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _strip_zoom_inline_refs(text: str) -> str:
    return _strip_zoom_from_body(text)


def html_to_rich_markdown(text: str) -> str:
    """Конвертирует HTML-саммари в Rich Markdown для sendRichMessage."""
    s = (text or "").strip()
    if not s:
        return ""
    if not uses_html_markup(s):
        return _normalize_summary_markdown(s)

    s = promote_section_headings(s)
    s = re.sub(
        r"🔗\s*<a\b[^>]*href\s*=\s*['\"][^'\"]*zoom\.us[^'\"]*['\"][^>]*>.*?</a>",
        "",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(
        r"<a\b[^>]*href\s*=\s*['\"][^'\"]*zoom\.us[^'\"]*['\"][^>]*>.*?</a>",
        "",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(
        r"<a\b[^>]*href\s*=\s*['\"]#[^'\"]+['\"][^>]*>.*?</a>",
        "",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = re.sub(
        r"<p>\s*(?:<a\b[^>]*href\s*=\s*['\"]#[^'\"]+['\"][^>]*>.*?</a>\s*)"
        r"(?:·\s*<a\b[^>]*>.*?</a>\s*)?</p>",
        "",
        s,
        flags=re.IGNORECASE | re.DOTALL,
    )
    s = _FOOTER_RE.sub("", s)

    def _table_repl(m: re.Match[str]) -> str:
        return "\n\n" + _html_table_to_md(m.group(0)) + "\n\n"

    s = re.sub(r"<table\b[^>]*>.*?</table>", _table_repl, s, flags=re.I | re.S)

    def _ul_repl(m: re.Match[str]) -> str:
        return "\n" + _html_ul_to_md(m.group(1)) + "\n"

    s = re.sub(r"<ul\b[^>]*>(.*?)</ul>", _ul_repl, s, flags=re.I | re.S)

    def _li_repl(m: re.Match[str]) -> str:
        return "\n" + _html_ul_to_md(m.group(0)) + "\n"

    s = _LI_RE.sub(_li_repl, s)

    for lvl in range(6, 0, -1):
        s = re.sub(
            rf"<h{lvl}\b[^>]*>(.*?)</h{lvl}>",
            lambda m, level=lvl: "\n\n"
            + ("#" * min(level, 3))
            + " "
            + _inline_html_to_md(m.group(1)).strip()
            + "\n\n",
            s,
            flags=re.I | re.S,
        )

    s = re.sub(
        r"<blockquote\b[^>]*>(.*?)</blockquote>",
        lambda m: "\n> "
        + _inline_html_to_md(m.group(1)).strip().replace("\n", "\n> ")
        + "\n\n",
        s,
        flags=re.I | re.S,
    )
    s = re.sub(r"</?p\b[^>]*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = _inline_html_to_md(s)
    return _normalize_summary_markdown(s)


def _task_line_md(task: dict[str, Any]) -> str:
    assignee = str(task.get("assignee") or "").strip()
    body = str(task.get("task") or "").strip()
    deadline = str(task.get("deadline") or "").strip()
    if not body:
        return ""
    line = f"{assignee} — {body}" if assignee else body
    if deadline:
        line += f" — {deadline}"
    return line


def tasks_checklist_markdown(
    tasks: list[dict[str, Any]],
    *,
    completed: bool,
    heading: str | None = None,
) -> str:
    lines: list[str] = []
    for raw in tasks or []:
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("completed")) != completed:
            continue
        label = _task_line_md(raw)
        if not label:
            continue
        lines.append(f"- [{'x' if completed else ' '}] {label}")
    if not lines:
        return ""
    title = heading or ("✅ Выполненные задачи" if completed else "📋 Задачи")
    return f"## {title}\n" + "\n".join(lines)


def inject_tasks_checklists_markdown(
    summary_md: str, tasks: list[dict[str, Any]]
) -> str:
    done = tasks_checklist_markdown(tasks, completed=True)
    todo = tasks_checklist_markdown(tasks, completed=False)
    section = "\n\n".join(x for x in (done, todo) if x)
    if not section:
        return (summary_md or "").strip()

    body = _strip_tasks_section(summary_md)
    if body:
        return f"{body.rstrip()}\n\n{section}".strip()
    return section


def _rich_footer_link(label: str, url: str) -> str:
    """RichBlockFooter: HTML <footer> + <a href> (markdown-ссылки внутри не рендерятся)."""
    esc_label = html.escape(label)
    esc_url = html.escape(url.strip(), quote=True)
    return f'<footer><a href="{esc_url}">{esc_label}</a></footer>'


def append_yandex_disk_footer_markdown(
    body: str,
    yandex_disk_url: str | None,
    *,
    yandex_disk_saved: bool = False,
) -> str:
    """Добавляет ссылку на папку записи на Яндекс Диске (под Zoom-встречей в футере)."""
    url = (yandex_disk_url or "").strip()
    text = (body or "").strip()
    if url:
        foot = _rich_footer_link("☁️ Запись встречи", url)
        return f"{text}\n\n{foot}" if text else foot
    if yandex_disk_saved:
        foot = (
            "<footer>☁️ Запись на Яндекс Диске. Чтобы в саммари была ссылка: "
            "Профиль → Яндекс Диск → Отключить → снова Подключить</footer>"
        )
        return f"{text}\n\n{foot}" if text else foot
    return text


def format_meeting_date(ts: str | None) -> str:
    raw = (ts or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:16].replace("T", " ")
    return f"{dt.day} {_MONTHS_RU[dt.month - 1]} {dt.year}, {dt.strftime('%H:%M')}"


def _body_has_date_under_headline(body: str) -> bool:
    lines = (body or "").splitlines()
    if len(lines) < 2:
        return False
    return bool(re.match(r"^\*[^*].*\*$", lines[1].strip()))


def _prepend_headline_with_date(body: str, headline: str, ts: str | None) -> str:
    hl = headline.strip()
    if not hl:
        return body
    date_line = format_meeting_date(ts)
    first_line = body.split("\n", 1)[0].strip().lstrip("#").strip().strip("*")
    if first_line == hl:
        if date_line and not _body_has_date_under_headline(body):
            lines = body.split("\n", 1)
            rest = lines[1] if len(lines) > 1 else ""
            return f"{lines[0]}\n*{date_line}*" + (f"\n{rest}" if rest else "")
        return body
    title = f"## {hl}"
    if date_line:
        title += f"\n*{date_line}*"
    return f"{title}\n\n{body}"


def append_source_footer_markdown(
    body: str,
    *,
    source_url: str | None,
    telegram_link: str | None,
) -> str:
    text = _strip_zoom_inline_refs((body or "").strip())
    footers: list[str] = []
    if source_url:
        url = source_url.strip()
        if "zoom.us" in url.lower():
            footers.append(_rich_footer_link("🔗 Zoom-встреча", url))
        else:
            footers.append(_rich_footer_link("Исходный файл", url))
    if telegram_link:
        footers.append(_rich_footer_link("💬 Сообщение в Telegram", telegram_link.strip()))
    if footers:
        text = f"{text}\n\n" + "\n".join(footers) if text else "\n".join(footers)
    return text.strip()


def prepare_summary_markdown(
    summary_text: str,
    *,
    tasks: list[dict[str, Any]] | None = None,
    source_url: str | None = None,
    telegram_link: str | None = None,
    headline: str | None = None,
    ts: str | None = None,
) -> str:
    body = (summary_text or "").strip()
    if uses_html_markup(body):
        body = html_to_rich_markdown(body)
    else:
        body = _normalize_summary_markdown(body)
    body = _strip_zoom_inline_refs(body)
    if tasks:
        body = inject_tasks_checklists_markdown(body, tasks)
    body = append_source_footer_markdown(
        body,
        source_url=source_url,
        telegram_link=telegram_link,
    )
    if headline:
        body = _prepend_headline_with_date(body, headline, ts)
    return body.strip()
