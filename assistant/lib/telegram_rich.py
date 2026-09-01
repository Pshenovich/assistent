"""Сборка Rich Message HTML (sendRichMessage): таблицы, сноски, заголовки, чеклисты."""

from __future__ import annotations

import html
import re
from typing import Any

_SPEAKER_BLOCK_RE = re.compile(
    r"^(Спикер[^\n:]*):\s*\n?(.*)$",
    re.DOTALL | re.IGNORECASE,
)
_HAS_HTML_TAG_RE = re.compile(r"</?[a-z][^>]*>", re.IGNORECASE)
_SECTION_BOLD_RE = re.compile(
    r"<b>\s*([^<]{1,80}?)\s*</b>\s*(?:<br\s*/?>\s*)+",
    re.IGNORECASE,
)
_TASKS_HEADING_RE = re.compile(
    r"<h2>\s*📋\s*Задачи\s*</h2>|<b>\s*📋\s*Задачи\s*</b>(?:\s*<br\s*/?>\s*)+",
    re.IGNORECASE,
)
_NEXT_SECTION_RE = re.compile(r"<h[23]>\s*[^\s<]", re.IGNORECASE)


def footnote_reference(ref_id: str, *, label: str = "🔗") -> str:
    rid = re.sub(r"[^\w\-]", "-", (ref_id or "ref").strip()) or "ref"
    return f'<a href="#{html.escape(rid, quote=True)}">{html.escape(label)}</a>'


def footnote_definition(ref_id: str, body_html: str) -> str:
    rid = re.sub(r"[^\w\-]", "-", (ref_id or "ref").strip()) or "ref"
    return f"<footer>[^{html.escape(rid)}]: {body_html}</footer>"


def zoom_meeting_footnote(url: str, *, ref_id: str = "zoom") -> tuple[str, str]:
    """Ссылка-сноска в тексте + определение в footer."""
    u = (url or "").strip()
    if not u:
        return "", ""
    ref = footnote_reference(ref_id, label="🔗 Zoom-встреча")
    foot = footnote_definition(
        ref_id,
        f'<a href="{html.escape(u, quote=True)}">Zoom-встреча</a>',
    )
    return ref, foot


def append_footnotes(body: str, footers: list[str]) -> str:
    text = (body or "").strip()
    parts = [p for p in footers if (p or "").strip()]
    if not parts:
        return text
    sep = "\n" if text else ""
    return f"{text}{sep}{''.join(parts)}"


def promote_section_headings(text: str, *, default_level: int = 2) -> str:
    """<b>Заголовок</b><br><br> → <h2> для разделов саммари/заметок."""
    s = (text or "").strip()
    if not s:
        return ""

    def _repl(m: re.Match[str]) -> str:
        title = (m.group(1) or "").strip()
        if not title:
            return m.group(0)
        lvl = default_level
        if title.startswith(("•", "-", "—")):
            return m.group(0)
        return f"<h{lvl}>{html.escape(title)}</h{lvl}>\n"

    out = _SECTION_BOLD_RE.sub(_repl, s)
    out = re.sub(
        r"<h([1-6])\b[^>]*>(.*?)</h\1>",
        lambda m: f"<h{min(int(m.group(1)), 3)}>{m.group(2).strip()}</h{min(int(m.group(1)), 3)}>",
        out,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return out


def _task_label(task: dict[str, Any]) -> str:
    assignee = str(task.get("assignee") or "").strip()
    body = str(task.get("task") or "").strip()
    deadline = str(task.get("deadline") or "").strip()
    if not body:
        return ""
    if assignee:
        line = f"{html.escape(assignee)} — {html.escape(body)}"
    else:
        line = html.escape(body)
    if deadline:
        line += f" — {html.escape(deadline)}"
    return line


def tasks_checklist_html(
    tasks: list[dict[str, Any]],
    *,
    completed: bool,
    heading: str | None = None,
) -> str:
    items: list[str] = []
    for raw in tasks or []:
        if not isinstance(raw, dict):
            continue
        if bool(raw.get("completed")) != completed:
            continue
        label = _task_label(raw)
        if not label:
            continue
        if completed:
            items.append(f"<li checked>{label}</li>")
        else:
            items.append(f"<li>{label}</li>")
    if not items:
        return ""
    title = heading or ("✅ Выполненные задачи" if completed else "📋 Задачи")
    return f"<h2>{html.escape(title)}</h2><ul>{''.join(items)}</ul>"


def inject_tasks_checklists(summary_html: str, tasks: list[dict[str, Any]]) -> str:
    """Заменяет/добавляет разделы задач чеклистами Rich Message."""
    done = tasks_checklist_html(tasks, completed=True)
    todo = tasks_checklist_html(tasks, completed=False)
    section = "\n".join(x for x in (done, todo) if x)
    if not section:
        return (summary_html or "").strip()

    body = (summary_html or "").strip()
    match = _TASKS_HEADING_RE.search(body)
    if not match:
        sep = "\n\n" if body else ""
        return f"{body}{sep}{section}"

    start = match.start()
    after = match.end()
    rest = body[after:]
    next_match = _NEXT_SECTION_RE.search(rest)
    if next_match and next_match.start() > 0:
        tail = rest[next_match.start() :].lstrip()
        prefix = body[:start].rstrip()
        mid = "\n\n" if tail else ""
        return f"{prefix}\n\n{section}{mid}{tail}"
    prefix = body[:start].rstrip()
    return f"{prefix}\n\n{section}" if prefix else section


def enrich_summary_html(summary_html: str, *, tasks: list[dict[str, Any]] | None = None) -> str:
    s = promote_section_headings(summary_html or "")
    if tasks:
        s = inject_tasks_checklists(s, tasks)
    return s.strip()


def speakers_transcription_table(text: str) -> str:
    """Транскрипт с диаризацией → таблица Спикер | Текст."""
    s = (text or "").strip()
    if not s:
        return ""
    if _HAS_HTML_TAG_RE.search(s) and "<table" in s.lower():
        return s

    blocks = re.split(r"\n\s*\n", s)
    rows: list[str] = []
    for block in blocks:
        piece = block.strip()
        if not piece:
            continue
        m = _SPEAKER_BLOCK_RE.match(piece)
        if m:
            speaker = html.escape(m.group(1).strip())
            body = html.escape(m.group(2).strip()).replace("\n", "<br>")
            rows.append(f"<tr><td>{speaker}</td><td>{body}</td></tr>")
        else:
            body = html.escape(piece).replace("\n", "<br>")
            rows.append(f"<tr><td></td><td>{body}</td></tr>")
    if not rows:
        return html.escape(s).replace("\n", "<br>")
    return (
        "<table>"
        "<tr><th>Спикер</th><th>Текст</th></tr>"
        + "".join(rows)
        + "</table>"
    )


from assistant.lib.telegram_html import deep_unescape


def _event_row(ev: dict[str, Any]) -> tuple[str, str, str]:
    summary = deep_unescape(str(ev.get("summary") or "Встреча"))
    link = str(ev.get("link") or "").strip()
    start = str(ev.get("start") or "")
    if "T" in start:
        time_s = start[11:16]
    else:
        time_s = start[:16].replace("T", " ")
    return time_s, summary, link


def _event_time_range(ev: dict[str, Any]) -> str:
    start = str(ev.get("start") or "")
    end = str(ev.get("end") or "")
    if "T" in start:
        start_t = start[11:16]
    else:
        start_t = start[:16].replace("T", " ")
    if "T" in end:
        end_t = end[11:16]
        if end_t and end_t != start_t:
            return f"{start_t}-{end_t}"
    return start_t


def _md_table_cell(text: str) -> str:
    s = deep_unescape(str(text or "").strip())
    return (
        s.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\n", " ")
        .replace("\r", "")
    )


def _md_cell_link(summary: str, link: str) -> str:
    label = _md_table_cell(summary).replace("[", "\\[").replace("]", "\\]")
    url = str(link or "").strip()
    if url:
        return f"[{label}]({url})"
    return label


def format_events_day_markdown(events: list[dict[str, Any]], header: str) -> str:
    """Таблица встреч для sendRichMessage (Rich Markdown)."""
    title = _md_table_cell((header or "Встречи").rstrip(":"))
    if not events:
        return f"## {title}\n\nВстреч нет."
    lines = [
        f"## {title}",
        "",
        "| Время | Встреча |",
        "|:------|:--------|",
    ]
    for ev in events:
        time_r = _md_table_cell(_event_time_range(ev))
        _, summary, link = _event_row(ev)
        meeting = _md_cell_link(summary, link)
        lines.append(f"| {time_r} | {meeting} |")
    return "\n".join(lines)


def format_events_day_html(events: list[dict[str, Any]], header: str) -> str:
    """Таблица встреч (classic HTML): <pre> без &nbsp; — совместимо с Telegram."""
    title = deep_unescape((header or "Встречи").rstrip(":"))
    if not events:
        return f"<b>{html.escape(title)}</b><br><br>Встреч нет."
    rows = [_event_row(ev) for ev in events]
    time_w = max(len("Время"), *(len(t) for t, _, _ in rows), 5)
    pre_lines = [f"{'Время'.ljust(time_w)}  Встреча"]
    pre_lines.extend(f"{t.ljust(time_w)}  {s}" for t, s, _ in rows)
    pre_body = html.escape("\n".join(pre_lines))
    parts = [f"<b>{html.escape(title)}</b><br><br><pre>{pre_body}</pre>"]
    time_links: list[str] = []
    for time_s, _, link in rows:
        if not link:
            continue
        time_links.append(
            f'<a href="{html.escape(link, quote=True)}">{html.escape(time_s)}</a>'
        )
    if time_links:
        parts.append("<br>" + "  ".join(time_links))
    return "".join(parts)


def format_events_day_table(events: list[dict[str, Any]], header: str) -> str:
    title = html.escape((header or "Встречи").rstrip(":"))
    if not events:
        return f"<h2>{title}</h2><p>Встреч нет.</p>"
    rows: list[str] = []
    for ev in events:
        time_s, summary, link = _event_row(ev)
        summary_esc = html.escape(summary)
        if link:
            time_cell = (
                f'<a href="{html.escape(link, quote=True)}">'
                f"{html.escape(time_s)}</a>"
            )
        else:
            time_cell = html.escape(time_s)
        rows.append(f"<tr><td>{time_cell}</td><td>{summary_esc}</td></tr>")
    return (
        f"<h2>{title}</h2>"
        "<table>"
        "<tr><th>Время</th><th>Встреча</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def quote_block(text: str, *, credit: str = "") -> str:
    body = html.escape((text or "").strip()).replace("\n", "<br>")
    if not body:
        return ""
    if credit:
        return f"<blockquote>{body}<cite>{html.escape(credit)}</cite></blockquote>"
    return f"<blockquote>{body}</blockquote>"
