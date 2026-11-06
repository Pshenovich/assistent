"""Markdown саммари → HTML заметки, один в один как NoteHtml.markdownToHtml в webapp/note-html.js.

Саммари встреч сохраняем сразу в HTML: на строке задачи держится её ID (data-leo-item),
а приложение показывает тело так же, как раньше показывало Markdown. Меняете здесь —
поменяйте и в note-html.js (сверка: tests/test_note_markdown.py, нужен node).
"""

from __future__ import annotations

import re

_HEADERS_RAW = [
    "📌 Краткое описание",
    "📝 Краткое содержание",
    "👤 Информация о кандидате",
    "⭐ Самые перспективные идеи",
    "🔧 Практические рекомендации",
    "⚠️ Риски и блокеры",
    "❓ Вопросы без ответа",
    "✅ Принятые решения",
    "✅ Упомянутые действия",
    "🎯 Что обсуждали",
    "🎯 Цели и задачи",
    "❓ Открытые вопросы",
    "🔜 Следующие шаги",
    "📋 Следующие действия",
    "⚠️ Слабые стороны",
    "💪 Сильные стороны",
    "🛠 Навыки и опыт",
    "📝 Итоговая оценка",
    "🧠 Основные идеи",
    "📚 Ключевые тезисы",
    "💡 Предложенные идеи",
    "📌 Цель обсуждения",
    "📌 Важные факты",
    "💡 Основные мысли",
    "📌 О чем запись",
    "📌 Кратко",
    "📋 Задачи",
    "✅ Выполненные задачи",
    "📋 Чек-лист",
    "⚠️ Риски",
    "🎯 Главная идея",
]


def _js_len(s: str) -> int:
    # Длина строки в JS — в UTF-16 единицах (эмодзи вне BMP — две).
    return len(s.encode("utf-16-le")) // 2


# Как в JS: sort по убыванию длины, стабильный.
_MEETING_SECTION_HEADERS = sorted(_HEADERS_RAW, key=lambda t: -_js_len(t))

_EMOJI = r"(?:[☀-➿]|[\U0001F000-\U0010FFFF])"


def _esc(s: str) -> str:
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_INLINE_SPLIT = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|~~[^~]+~~)")


def _inline(s: str) -> str:
    out = []
    for part in _INLINE_SPLIT.split(str(s or "")):
        if re.fullmatch(r"\*\*[^*]+\*\*", part):
            out.append("<strong>" + _esc(part[2:-2]) + "</strong>")
        elif re.fullmatch(r"\*[^*]+\*", part):
            out.append("<em>" + _esc(part[1:-1]) + "</em>")
        elif re.fullmatch(r"~~[^~]+~~", part):
            out.append("<s>" + _esc(part[2:-2]) + "</s>")
        else:
            out.append(_esc(part))
    return "".join(out)


def _is_task_section_title(title: str) -> bool:
    t = str(title or "").strip()
    return bool(re.search(r"📋\s*(Задачи|Чек-лист|Следующие действия)", t) or re.search(r"✅\s*Выполненные задачи", t))


def _is_completed_task_section_title(title: str) -> bool:
    return bool(re.search(r"✅\s*Выполненные задачи", str(title or "").strip()))


def _split_inline_bullet_line(line: str) -> str:
    t = str(line or "").strip()
    if not re.match(r"-\s", t):
        return line
    if re.search(r"\s+-\s+\[[ xX]\]\s", t):
        return re.sub(r"\s+-\s+(\[[ xX]\]\s+)", r"\n- \1", t)
    if not re.search(r"\s+-\s+", t):
        return line
    t = re.sub(r"\.\s+-\s+", ".\n- ", t)
    return re.sub(r"\s+-\s+(?=[А-ЯA-ZЁ])", "\n- ", t)


_EMOJI_BODY = re.compile(
    r"^(" + _EMOJI + r"+\s*(?:[^\s#-]+(?:\s+[^\s#-]+){0,5}?))\s*((?:[А-ЯA-ZЁ]|[-•]\s).*)$", re.S
)
_PLAIN_BODY = re.compile(r"^((?:[^\s#-]+(?:\s+[^\s#-]+){0,4}?))\s+([А-ЯA-ZЁ].*)$", re.S)


def _split_heading_content(hashes: str, content: str) -> str:
    body = str(content or "").strip()
    if not body:
        return hashes + " " + body
    for title in _MEETING_SECTION_HEADERS:
        if body == title:
            return hashes + " " + title
        if body.startswith(title):
            rest = body[len(title):].strip()
            if not rest:
                return hashes + " " + title
            return hashes + " " + title + "\n" + _split_inline_bullet_line(rest)
    m = _EMOJI_BODY.match(body)
    if m and _js_len(m.group(1).strip()) <= 60 and m.group(2).strip():
        return hashes + " " + m.group(1).strip() + "\n" + _split_inline_bullet_line(m.group(2).strip())
    m = _PLAIN_BODY.match(body)
    if m and _js_len(m.group(1).strip()) <= 50 and _js_len(m.group(1).strip()) < _js_len(body) - 8:
        return hashes + " " + m.group(1).strip() + "\n" + _split_inline_bullet_line(m.group(2).strip())
    return hashes + " " + body


def normalize_collapsed_markdown(md: str) -> str:
    s = str(md or "").strip()
    if not s:
        return s
    s = re.sub(r"\s+(#{1,6}\s+)", r"\n\n\1", s)
    s = re.sub(r"(#{1,6}\s+[^\n#]+?)\s+(-\s+\[[ xX]\]\s+)", r"\1\n\2", s)
    s = re.sub(r"(#{1,6}\s+[^\n#]+?)\s+(-\s+(?!\[[ xX]\]))", r"\1\n\2", s)
    out = []
    for line in s.split("\n"):
        trimmed = line.strip()
        if not re.match(r"#{1,6}\s+", trimmed):
            out.append(_split_inline_bullet_line(line))
            continue
        hm = re.match(r"^(#{1,6})\s+(.*)$", trimmed, re.S)
        out.append(_split_heading_content(hm.group(1), hm.group(2)) if hm else line)
    return "\n".join(out)


def _table_row(line: str):
    t = str(line or "").strip()
    if not re.fullmatch(r"\|.+\|", t, re.S):
        return None
    if re.fullmatch(r"\|[\s\-:|]+\|", t):
        return "sep"
    return [c.strip() for c in t[1:-1].split("|")]


def _table_html(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    out = ['<table class="note-editor-table"><tbody>']
    for i, cells in enumerate(rows):
        out.append("<tr>")
        tag = "th" if i == 0 else "td"
        for c in cells:
            out.append(f"<{tag}>{_inline(c)}</{tag}>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _lift_tables(md: str) -> str:
    lines = str(md or "").split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        row = _table_row(lines[i].strip())
        if not row:
            out.append(lines[i])
            i += 1
            continue
        rows = []
        while i < len(lines):
            cand = _table_row(lines[i].strip())
            if not cand:
                break
            if cand != "sep":
                rows.append(cand)
            i += 1
        if rows:
            out.append(_table_html(rows))
    return "\n".join(out)


def markdown_to_html(md: str) -> str:
    lines = _lift_tables(normalize_collapsed_markdown(md)).split("\n")
    html: list[str] = []
    st = {"list": False, "task": False, "task_section": False, "task_done": False}

    def close_list() -> None:
        if st["list"]:
            html.append("</ul>")
            st["list"] = False

    def close_task() -> None:
        if st["task"]:
            html.append("</ul>")
            st["task"] = False

    def close_all() -> None:
        close_list()
        close_task()

    def task_item(checked: bool, text: str) -> None:
        close_list()
        if not st["task"]:
            html.append('<ul class="note-task-list">')
            st["task"] = True
        html.append(("<li checked><p>" if checked else "<li><p>") + _inline(text) + "</p></li>")

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            close_all()
            continue
        if re.match(r"<table[\s>]", trimmed, re.I):
            close_all()
            html.append(trimmed)
            st["task_section"] = st["task_done"] = False
            continue
        hm = re.match(r"^(#{1,6})\s+(.*)$", trimmed, re.S)
        if hm:
            close_all()
            tag = "h" + str(min(len(hm.group(1)), 6))
            html.append(f"<{tag}>{_inline(hm.group(2))}</{tag}>")
            st["task_section"] = _is_task_section_title(hm.group(2))
            st["task_done"] = _is_completed_task_section_title(hm.group(2))
            continue
        tm = re.match(r"^-\s+\[([ xX])\]\s+(.*)$", trimmed, re.S)
        if tm:
            task_item(tm.group(1).lower() == "x", tm.group(2))
            continue
        bm = re.match(r"^[-*•]\s+(.*)$", trimmed, re.S)
        if bm:
            if st["task_section"]:
                task_item(st["task_done"], bm.group(1))
                continue
            close_task()
            if not st["list"]:
                html.append("<ul>")
                st["list"] = True
            html.append("<li><p>" + _inline(bm.group(1)) + "</p></li>")
            continue
        close_all()
        st["task_section"] = st["task_done"] = False
        html.append("<p>" + _inline(trimmed) + "</p>")
    close_all()
    return "".join(html)


# ---------- Обычный Markdown документа (агенты по MCP, импорт) ----------
# markdown_to_html выше — для саммари встреч: он «расклеивает» заголовок и текст, слитые моделью
# в одну строку, и поэтому режет обычные заголовки («# Орбита и ИИ-ассистент» → «Орбита и» + абзац).
# Для готового Markdown — этот разбор: заголовки как есть, нумерованные и вложенные списки,
# цитаты, код, разделитель, ссылки.

_DOC_INLINE = re.compile(
    r"(`[^`]+`|\*\*[^*]+\*\*|~~[^~]+~~|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*)"
)
_SAFE_URL = re.compile(r"^(https?://|mailto:|tel:|/|#)", re.I)


def _doc_inline(s: str) -> str:
    out = []
    for part in _DOC_INLINE.split(str(s or "")):
        if not part:
            continue
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            out.append("<code>" + _esc(part[1:-1]) + "</code>")
        elif part.startswith("**") and part.endswith("**") and len(part) > 4:
            out.append("<strong>" + _doc_inline(part[2:-2]) + "</strong>")
        elif part.startswith("~~") and part.endswith("~~") and len(part) > 4:
            out.append("<s>" + _doc_inline(part[2:-2]) + "</s>")
        elif part.startswith("[") and part.endswith(")"):
            m = re.fullmatch(r"\[([^\]]+)\]\(([^)\s]+)\)", part)
            if m and _SAFE_URL.match(m.group(2)):
                out.append(f'<a href="{_esc(m.group(2))}">{_doc_inline(m.group(1))}</a>')
            else:
                out.append(_esc(part))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            out.append("<em>" + _doc_inline(part[1:-1]) + "</em>")
        else:
            out.append(_esc(part))
    return "".join(out)


_LIST_ITEM = re.compile(r"^(\s*)([-*+•]|\d{1,3}[.)])\s+(.*)$")
_DOC_TABLE_SEP = re.compile(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?$")


def _doc_table_cells(line: str) -> list[str]:
    t = line.strip()
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|"):
        t = t[:-1]
    return [c.strip() for c in t.split("|")]


def document_markdown_to_html(md: str) -> str:
    """Готовый Markdown → HTML документа Орбиты (без эвристик саммари)."""
    lines = str(md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    html: list[str] = []
    para: list[str] = []
    i = 0

    def flush_para() -> None:
        if para:
            html.append("<p>" + "<br>".join(_doc_inline(p) for p in para) + "</p>")
            para.clear()

    def render_list(start: int) -> int:
        """Список (с вложенностью по отступу) с позиции start; возвращает следующую строку."""
        stack: list[tuple[int, str]] = []  # (отступ, ul|ol)
        j = start
        while j < len(lines):
            m = _LIST_ITEM.match(lines[j])
            if not m:
                # продолжение пункта — строка с отступом под ним
                if lines[j].strip() and stack and len(lines[j]) - len(lines[j].lstrip()) > stack[-1][0]:
                    html[-1] = html[-1][: -len("</p>")] + "<br>" + _doc_inline(lines[j].strip()) + "</p>"
                    j += 1
                    continue
                break
            indent = len(m.group(1).expandtabs(4))
            kind = "ol" if m.group(2)[0].isdigit() else "ul"
            text = m.group(3)
            task = re.match(r"^\[([ xX])\]\s+(.*)$", text) if kind == "ul" else None
            while stack and indent < stack[-1][0]:
                html.append("</li></" + stack.pop()[1] + ">")
            if stack and indent == stack[-1][0] and kind != stack[-1][1]:
                html.append("</li></" + stack.pop()[1] + ">")
            if not stack or indent > stack[-1][0]:
                if task:
                    html.append('<ul class="note-task-list">')
                elif kind == "ol" and m.group(2)[:-1] != "1":
                    html.append(f'<ol start="{int(m.group(2)[:-1])}">')
                else:
                    html.append("<" + kind + ">")
                stack.append((indent, kind))
            else:
                html.append("</li>")
            if task:
                html.append(("<li checked>" if task.group(1).lower() == "x" else "<li>") + "<p>" + _doc_inline(task.group(2)) + "</p>")
            else:
                html.append("<li><p>" + _doc_inline(text) + "</p>")
            j += 1
        while stack:
            html.append("</li></" + stack.pop()[1] + ">")
        return j

    while i < len(lines):
        raw = lines[i]
        t = raw.strip()
        if not t:
            flush_para()
            i += 1
            continue
        if t.startswith("```"):
            flush_para()
            code: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            html.append("<pre><code>" + _esc("\n".join(code)) + "</code></pre>")
            continue
        hm = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", t)
        if hm:
            flush_para()
            n = len(hm.group(1))
            html.append(f"<h{n}>{_doc_inline(hm.group(2))}</h{n}>")
            i += 1
            continue
        if re.fullmatch(r"(\*\s*){3,}|(-\s*){3,}|(_\s*){3,}", t):
            flush_para()
            html.append("<hr>")
            i += 1
            continue
        if t.startswith("|") and i + 1 < len(lines) and _DOC_TABLE_SEP.match(lines[i + 1].strip()):
            flush_para()
            rows = [_doc_table_cells(t)]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_doc_table_cells(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            out = ['<table class="note-editor-table"><tbody>']
            for n, cells in enumerate(rows):
                tag = "th" if n == 0 else "td"
                cells = cells + [""] * (width - len(cells))
                out.append("<tr>" + "".join(f"<{tag}><p>{_doc_inline(c)}</p></{tag}>" for c in cells) + "</tr>")
            out.append("</tbody></table>")
            html.append("".join(out))
            continue
        if t.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^>\s?", "", lines[i].strip()))
                i += 1
            html.append("<blockquote>" + document_markdown_to_html("\n".join(quote)) + "</blockquote>")
            continue
        if _LIST_ITEM.match(raw) and not para:
            i = render_list(i)
            continue
        para.append(t)
        i += 1
    flush_para()
    return "".join(html)
