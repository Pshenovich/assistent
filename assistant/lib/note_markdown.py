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
