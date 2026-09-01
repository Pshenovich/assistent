"""Генерация PDF для записей журнала (транскрипции, саммари)."""

from __future__ import annotations

import html as html_lib
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from assistant.lib.usage_store import (
    get_user_usage_event,
    journal_json_from_raw,
    journal_meta_from_raw,
    journal_source_links_from_raw,
    journal_text_from_raw,
)

_FONT_NAME = "JournalBody"
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_FOOTER_RE = re.compile(r"<footer\b[^>]*>[\s\S]*?</footer>", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TASK_RE = re.compile(r"^-\s+\[([ xX])\]\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*•]\s+(.*)$")
_MD_HINT_RE = re.compile(
    r"(^|\n)#{1,6}\s|(^|\n)-\s+\[[ xX]\]\s|(^|\n)[-*•]\s",
    re.MULTILINE,
)
_BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")
_ITALIC_LINE_RE = re.compile(r"^\*([^*]+)\*$")
_TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
_LINK_INLINE_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

_MEETING_SECTION_HEADERS = sorted(
    [
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
        "📋 Чек-лист",
        "⚠️ Риски",
        "🎯 Главная идея",
    ],
    key=len,
    reverse=True,
)


def _assets_font_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _resolve_font_paths() -> tuple[Path, Path | None]:
    bundled = _assets_font_dir() / "DejaVuSans.ttf"
    bundled_bold = _assets_font_dir() / "DejaVuSans-Bold.ttf"
    candidates = [
        bundled,
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
    ]
    regular: Path | None = None
    for p in candidates:
        if p.is_file():
            regular = p
            break
    if regular is None:
        raise RuntimeError(
            "Не найден шрифт для PDF. Установите fonts-dejavu-core или добавьте DejaVuSans.ttf "
            "в assistant/assets/fonts/."
        )
    bold_candidates = [
        bundled_bold,
        regular.parent / "DejaVuSans-Bold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    bold = next((p for p in bold_candidates if p.is_file()), None)
    return regular, bold


def _html_to_plain(text: str) -> str:
    s = (text or "").replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    s = s.replace("</p>", "\n").replace("</div>", "\n").replace("</li>", "\n")
    s = s.replace("</h1>", "\n").replace("</h2>", "\n").replace("</h3>", "\n")
    s = _HTML_TAG_RE.sub("", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _pdf_safe(text: str) -> str:
    return (text or "").replace("\r", "").strip()


def _write_block(
    pdf: FPDF,
    text: str,
    *,
    style: str = "",
    size: float = 11,
    h: float = 6,
    color: tuple[int, int, int] | None = None,
) -> None:
    pdf.set_font(_FONT_NAME, style, size)
    if color is not None:
        pdf.set_text_color(*color)
    pdf.multi_cell(0, h, _pdf_safe(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if color is not None:
        pdf.set_text_color(0, 0, 0)


def _operation_label(op: str) -> str:
    if op == "summarize":
        return "Саммари"
    if op == "obuchat_transcribe":
        return "Транскрипция"
    return op or "Запись"


def _filename_for_row(row: dict[str, Any], *, title: str) -> str:
    op = str(row.get("operation") or "")
    base = "samari" if op == "summarize" else "transkript"
    eid = row.get("id")
    slug = re.sub(r"[^\w\-]+", "_", title.lower(), flags=re.UNICODE)[:40].strip("_")
    parts = [base]
    if slug:
        parts.append(slug)
    if eid:
        parts.append(str(eid))
    return "-".join(parts) + ".pdf"


def _is_task_section_title(title: str) -> bool:
    t = (title or "").strip()
    return bool(
        re.search(r"📋\s*(Задачи|Чек-лист|Следующие действия)", t)
        or re.search(r"✅\s*Выполненные задачи", t)
    )


def _is_completed_task_section_title(title: str) -> bool:
    return bool(re.search(r"✅\s*Выполненные задачи", (title or "").strip()))


def _split_inline_bullet_line(line: str) -> str:
    t = (line or "").strip()
    if not t.startswith("- "):
        return line
    if re.search(r"\s+-\s+\[[ xX]\]\s", t):
        return re.sub(r"\s+-\s+(\[[ xX]\]\s+)", r"\n- \1", t)
    if not re.search(r"\s+-\s+", t):
        return line
    t = re.sub(r"\.\s+-\s+", ".\n- ", t)
    t = re.sub(r"\s+-\s+(?=[А-ЯA-ZЁ])", r"\n- ", t)
    return t


def _split_heading_content(hashes: str, content: str) -> str:
    body = (content or "").strip()
    if not body:
        return f"{hashes} {body}"

    for title in _MEETING_SECTION_HEADERS:
        if body == title:
            return f"{hashes} {title}"
        if body.startswith(title):
            rest = body[len(title) :].strip()
            if not rest:
                return f"{hashes} {title}"
            return f"{hashes} {title}\n{_split_inline_bullet_line(rest)}"

    emoji_body = re.match(
        r"^((?:[\U00002600-\U000027BF]|[\U0001F000-\U0001FAFF])+\s*"
        r"(?:[^\s#-]+(?:\s+[^\s#-]+){0,5}?))\s*((?:[А-ЯA-ZЁ]|[-•]\s).*)$",
        body,
    )
    if emoji_body and len(emoji_body.group(1).strip()) <= 60 and emoji_body.group(2).strip():
        return (
            f"{hashes} {emoji_body.group(1).strip()}\n"
            f"{_split_inline_bullet_line(emoji_body.group(2).strip())}"
        )

    plain_body = re.match(r"^((?:[^\s#-]+(?:\s+[^\s#-]+){0,4}?))\s+([А-ЯA-ZЁ].*)$", body)
    if (
        plain_body
        and len(plain_body.group(1).strip()) <= 50
        and len(plain_body.group(1).strip()) < len(body) - 8
    ):
        return (
            f"{hashes} {plain_body.group(1).strip()}\n"
            f"{_split_inline_bullet_line(plain_body.group(2).strip())}"
        )

    return f"{hashes} {body}"


def normalize_collapsed_markdown(md: str) -> str:
    """Та же нормализация склеенного markdown, что в webapp/note-html.js."""
    s = (md or "").strip()
    if not s:
        return s
    s = re.sub(r"\s+(#{1,6}\s+)", r"\n\n\1", s)
    s = re.sub(r"(#{1,6}\s+[^\n#]+?)\s+(-\s+\[[ xX]\]\s+)", r"\1\n\2", s)
    s = re.sub(r"(#{1,6}\s+[^\n#]+?)\s+(-\s+(?!\[[ xX]\]))", r"\1\n\2", s)
    out_lines: list[str] = []
    for line in s.split("\n"):
        trimmed = line.strip()
        if not _HEADING_RE.match(trimmed):
            out_lines.append(_split_inline_bullet_line(line))
            continue
        hm = _HEADING_RE.match(trimmed)
        if not hm:
            out_lines.append(line)
            continue
        out_lines.append(_split_heading_content(hm.group(1), hm.group(2)))
    return "\n".join(out_lines)


def _strip_inline_markdown(text: str) -> str:
    s = str(text or "")
    s = _LINK_INLINE_RE.sub(lambda m: f"{m.group(1)} ({m.group(2)})", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"\*([^*]+)\*", r"\1", s)
    s = re.sub(r"~~([^~]+)~~", r"\1", s)
    return s


def _looks_like_markdown(text: str) -> bool:
    return bool(_MD_HINT_RE.search(text or ""))


def _prepare_body_text(raw_body: str, op: str) -> str:
    body = (raw_body or "").strip()
    body = _FOOTER_RE.sub("", body).strip()
    if op == "summarize" and _HTML_TAG_RE.search(body) and not _looks_like_markdown(body):
        from assistant.lib.telegram_markdown import html_to_rich_markdown

        body = html_to_rich_markdown(body)
    if op == "summarize" and _looks_like_markdown(body):
        body = normalize_collapsed_markdown(body)
    return body


def _render_markdown_body(pdf: FPDF, body: str, font: str) -> None:
    del font
    in_task_section = False
    task_section_completed = False
    for line in body.split("\n"):
        trimmed = line.strip()
        if not trimmed:
            pdf.ln(2)
            continue

        if _TABLE_SEP_RE.match(trimmed):
            continue

        tm_row = _TABLE_ROW_RE.match(trimmed)
        if tm_row:
            cells = [
                _strip_inline_markdown(c.strip())
                for c in tm_row.group(1).split("|")
                if c.strip()
            ]
            if cells:
                _write_block(pdf, "  " + "  ·  ".join(cells), size=10, h=5.5)
            continue

        bq = _BLOCKQUOTE_RE.match(trimmed)
        if bq:
            _write_block(
                pdf,
                "  " + _strip_inline_markdown(bq.group(1).strip()),
                size=10,
                h=5.5,
                color=(95, 95, 95),
            )
            continue

        im = _ITALIC_LINE_RE.match(trimmed)
        if im:
            _write_block(
                pdf,
                im.group(1).strip(),
                size=10,
                h=5.5,
                color=(90, 90, 90),
            )
            continue

        hm = _HEADING_RE.match(trimmed)
        if hm:
            title = _strip_inline_markdown(hm.group(2).strip())
            _write_block(pdf, title, style="B", size=13, h=7)
            pdf.ln(2)
            in_task_section = _is_task_section_title(title)
            task_section_completed = _is_completed_task_section_title(title)
            continue

        tm = _TASK_RE.match(trimmed)
        if tm:
            checked = tm.group(1).lower() == "x"
            label = _strip_inline_markdown(tm.group(2).strip())
            prefix = "[x] " if checked else "[ ] "
            _write_block(pdf, f"  {prefix}{label}")
            continue

        bm = _BULLET_RE.match(trimmed)
        if bm:
            label = _strip_inline_markdown(bm.group(1).strip())
            if in_task_section:
                prefix = "[x] " if task_section_completed else "[ ] "
                _write_block(pdf, f"  {prefix}{label}")
            else:
                _write_block(pdf, f"  • {label}")
            continue

        in_task_section = False
        task_section_completed = False
        _write_block(pdf, _strip_inline_markdown(trimmed))


def _render_plain_body(pdf: FPDF, body: str, font: str) -> None:
    del font
    chunks = re.split(r"\n{2,}", body.strip())
    if len(chunks) <= 1 and "\n" in body:
        chunks = [ln.strip() for ln in body.split("\n") if ln.strip()]
    for chunk in chunks:
        if not chunk.strip():
            continue
        _write_block(pdf, chunk.strip())
        pdf.ln(1)


def _render_journal_body(pdf: FPDF, body: str, op: str, font: str) -> None:
    prepared = _prepare_body_text(body, op)
    if not prepared:
        return
    if _looks_like_markdown(prepared):
        _render_markdown_body(pdf, prepared, font)
        return
    plain = _html_to_plain(prepared) if _HTML_TAG_RE.search(prepared) else prepared
    _render_plain_body(pdf, plain, font)


def build_journal_pdf(row: dict[str, Any]) -> tuple[bytes, str, str]:
    """Возвращает (pdf_bytes, filename, caption)."""
    op = str(row.get("operation") or "")
    if op not in ("obuchat_transcribe", "summarize"):
        raise ValueError("PDF доступен только для транскрипций и саммари.")
    raw = row.get("raw_usage_json")
    raw_s = raw if isinstance(raw, str) else None
    meta = journal_meta_from_raw(raw_s)
    links = journal_source_links_from_raw(raw_s)
    body = journal_text_from_raw(raw_s)
    body = _pdf_safe(body)
    if not body:
        raise ValueError("Пустой текст для PDF.")

    title = str(meta.get("main_topic") or "").strip()
    if not title:
        first = _prepare_body_text(body, op).split("\n", 1)[0].strip()
        first = re.sub(r"^#{1,6}\s+", "", first)
        title = first[:120] or _operation_label(op)
    label = _operation_label(op)

    ts = str(row.get("ts_utc") or "").strip()
    ts_human = ts[:16].replace("T", " ") if ts else ""

    regular, bold = _resolve_font_paths()
    bold_path = bold or regular
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.add_font(_FONT_NAME, "", str(regular))
    pdf.add_font(_FONT_NAME, "B", str(bold_path))

    _write_block(pdf, label, style="B", size=16, h=9)
    pdf.ln(2)
    _write_block(pdf, title, style="B", size=13, h=8)
    pdf.ln(2)
    if ts_human:
        _write_block(pdf, f"Дата: {ts_human}", size=10, h=6, color=(90, 90, 90))
        pdf.ln(2)

    extras: list[str] = []
    if links.get("source_url"):
        extras.append(f"Исходный файл: {links['source_url']}")
    if links.get("telegram_link"):
        extras.append(f"Сообщение в Telegram: {links['telegram_link']}")
    if extras:
        _write_block(pdf, "\n".join(extras), size=9, h=5)
        pdf.ln(3)

    _render_journal_body(pdf, body, op, _FONT_NAME)

    out = pdf.output()
    data = out if isinstance(out, (bytes, bytearray)) else out.encode("latin-1")
    fname = _filename_for_row(row, title=title)
    caption = f"{label}: {title}"[:900]
    return bytes(data), fname, caption


def journal_pdf_for_user(telegram_user_id: str, event_id: int) -> tuple[bytes, str, str]:
    row = get_user_usage_event(telegram_user_id, event_id)
    if not row:
        raise ValueError("Запись не найдена.")
    op = str(row.get("operation") or "")
    if op not in ("obuchat_transcribe", "summarize"):
        raise ValueError("PDF недоступен для этой записи.")
    return build_journal_pdf(row)
