"""Компактный контекст компании для PAEI: индекс + релевантные фрагменты.

Полный живой документ не кладётся в каждый ход. Один раз на совещание
собирается короткий бриф (каталог продуктов + 2–3 секции по задаче),
он кэшируется и переиспользуется. Без дополнительного LLM-вызова.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Any

_LOCK = threading.Lock()
_PARSED: dict[str, tuple[str, list[dict[str, str]]]] = {}

_HEADING_MD = re.compile(r"^(#{1,3})\s+(.+)$")
_HEADING_HTML = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.I | re.S)
_NESTED_HEADINGS = {
    "уже есть",
    "уже есть:",
    "роадмап",
    "роадмап:",
    "монетизация",
    "конкуренты",
    "принцип работы сервиса",
    "принцип работы сервиса:",
}
_TOKEN = re.compile(r"[a-zа-яё0-9]{3,}", re.I)
_STOP = {
    "and", "the", "for", "with", "that", "this", "from", "are", "was", "were",
    "есть", "уже", "как", "для", "или", "что", "это", "при", "без", "над",
    "под", "все", "всё", "они", "его", "ее", "её", "их", "мы", "вы", "он",
    "она", "том", "тем", "чем", "так", "также", "если", "чтобы", "можно",
    "нужно", "будет", "быть", "этот", "эта", "эти", "наш", "наша", "наши",
}

DEFAULT_BUDGET = 4000
DEFAULT_EXCERPTS = 3
INDEX_BUDGET = 1400
ONE_LINER = 110
SECTION_CAP = 1200


def ctx_char_budget() -> int:
    try:
        return max(1200, int(os.getenv("BOARD_COMPANY_CTX_CHARS", str(DEFAULT_BUDGET)) or DEFAULT_BUDGET))
    except ValueError:
        return DEFAULT_BUDGET


def max_excerpts() -> int:
    try:
        return max(1, min(6, int(os.getenv("BOARD_COMPANY_EXCERPTS", str(DEFAULT_EXCERPTS)) or DEFAULT_EXCERPTS)))
    except ValueError:
        return DEFAULT_EXCERPTS


def tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN.finditer(text or "") if m.group(0).lower() not in _STOP}


def _section(title: str, text: str, source: str, index_title: str = "") -> dict[str, str]:
    return {
        "title": title,
        "text": text,
        "source": source,
        "index_title": index_title or title,
    }


def split_html_sections(html: str, *, source: str = "") -> list[dict[str, str]]:
    from assistant.lib.telegram_html import html_to_plain
    from assistant.board.share_source import share_body_to_text

    matches = list(_HEADING_HTML.finditer(html or ""))
    if not matches:
        return split_sections(share_body_to_text(html or ""), source=source)
    sections: list[dict[str, str]] = []
    product = source or ""
    preamble = share_body_to_text(html[: matches[0].start()])
    if preamble.strip():
        sections.append(_section(product or "Обзор", preamble, source, product or "Обзор"))
    for i, match in enumerate(matches):
        raw_title = html_to_plain(match.group(2) or "").strip().rstrip(":")
        title = re.sub(r"\s+", " ", raw_title)
        level = int(match.group(1))
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(html)
        body = share_body_to_text(html[start:end]).strip()
        nested_key = title.lower()
        nest = bool(product) and (
            nested_key in _NESTED_HEADINGS
            or level >= 4
            or (level >= 3 and len(title) > 48)
        )
        if nest:
            label = f"{product} · {title}"
            index_title = product
        else:
            product = title or product
            label = title or product or "Обзор"
            index_title = label
        if not body:
            continue
        sections.append(_section(label, body, source, index_title))
    return sections or split_sections(share_body_to_text(html or ""), source=source)


def split_sections(text: str, *, source: str = "") -> list[dict[str, str]]:
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return []
    lines = raw.split("\n")
    sections: list[dict[str, str]] = []
    title = source or "Обзор"
    body: list[str] = []

    def flush() -> None:
        chunk = "\n".join(body).strip()
        if not chunk and title in {"Обзор", source}:
            return
        sections.append(_section(title.strip() or "Обзор", chunk, source))

    for i, line in enumerate(lines):
        stripped = line.strip()
        rest_has_body = any(x.strip() for x in lines[i + 1 :])
        md = _HEADING_MD.match(stripped)
        if md and (body or rest_has_body):
            flush()
            title = md.group(2).strip()
            body = []
            continue
        if _looks_like_heading(stripped) and rest_has_body:
            flush()
            title = stripped
            body = []
            continue
        if stripped:
            body.append(stripped)
    flush()
    if len(sections) <= 1 and len(raw) > SECTION_CAP * 2:
        return _chunk_paragraphs(raw, source=source)
    return sections


def _looks_like_heading(line: str) -> bool:
    if not line or line.startswith(("- ", "• ", "* ", "— ")):
        return False
    if line.endswith((".", ",", ";", ":", "»", ")")):
        return False
    if len(line) < 2 or len(line) > 64:
        return False
    words = line.split()
    if not (1 <= len(words) <= 7):
        return False
    letters = sum(ch.isalpha() for ch in line)
    return letters >= 2


def _chunk_paragraphs(text: str, *, source: str = "") -> list[dict[str, str]]:
    parts = re.split(r"\n{2,}", text)
    out: list[dict[str, str]] = []
    buf = ""
    n = 1
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        if buf and len(buf) + len(piece) > SECTION_CAP:
            out.append(_section(f"Фрагмент {n}", buf.strip(), source))
            n += 1
            buf = piece
        else:
            buf = f"{buf}\n\n{piece}".strip() if buf else piece
    if buf.strip():
        out.append(_section(f"Фрагмент {n}", buf.strip(), source))
    return out


def _one_liner(text: str) -> str:
    line = re.sub(r"\s+", " ", (text or "").strip())
    if len(line) <= ONE_LINER:
        return line
    return line[: ONE_LINER - 1] + "…"


def _parse_key(url: str, updated_at: str, text: str) -> str:
    return f"{url}|{updated_at}|{len(text)}"


def parse_document(
    text: str,
    *,
    title: str = "",
    url: str = "",
    updated_at: str = "",
    html: str = "",
) -> list[dict[str, str]]:
    key = _parse_key(url or title, updated_at, html or text)
    with _LOCK:
        hit = _PARSED.get(key)
        if hit and hit[0] == (html or text):
            return [dict(s) for s in hit[1]]
    sections = split_html_sections(html, source=title) if html else split_sections(text, source=title)
    with _LOCK:
        if len(_PARSED) > 32:
            _PARSED.pop(next(iter(_PARSED)))
        _PARSED[key] = (html or text, [dict(s) for s in sections])
    return sections


def pack_sections(pack: dict[str, Any] | None) -> list[dict[str, str]]:
    if not pack:
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    live = pack.get("_live_share") if isinstance(pack.get("_live_share"), dict) else {}
    docs = list(pack.get("_documents") or [])
    notes = (pack.get("raw_text") or "").strip()
    if notes:
        out.extend(parse_document(notes, title="Заметки /company"))
        seen.add(notes)
    for doc in docs:
        body = (doc.get("text") or "").strip()
        if not body or body in seen:
            continue
        seen.add(body)
        name = str(doc.get("filename") or "Документ")
        updated = ""
        html = ""
        if doc.get("kind") == "live" and live:
            updated = str(live.get("updated_at") or "")
            html = str(live.get("html") or "")
        out.extend(
            parse_document(
                body,
                title=name,
                url=str(pack.get("source_url") or ""),
                updated_at=updated,
                html=html,
            )
        )
    if out:
        return out
    parts = []
    for key in (
        "description",
        "business_model",
        "products",
        "customers",
        "kpis",
        "team",
        "goals",
        "constraints",
    ):
        val = (pack.get(key) or "").strip()
        if val:
            parts.append(f"{key}: {val}")
    if parts:
        return [_section("Профиль", "\n".join(parts), "профиль")]
    return []


def catalog_index(sections: list[dict[str, str]], *, budget: int = INDEX_BUDGET) -> str:
    lines: list[str] = []
    used = 0
    seen_titles: set[str] = set()
    for sec in sections:
        title = (sec.get("index_title") or sec.get("title") or "").strip()
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        blurb = _one_liner(sec.get("text") or "")
        line = f"• {title}" + (f" — {blurb}" if blurb else "")
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def score_section(section: dict[str, str], query_tokens: set[str]) -> int:
    if not query_tokens:
        return 0
    title_t = tokens(section.get("title") or "")
    body_t = tokens((section.get("text") or "")[:2400])
    return 5 * len(title_t & query_tokens) + len(body_t & query_tokens)


def select_sections(
    sections: list[dict[str, str]],
    query: str,
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    cap = limit if limit is not None else max_excerpts()
    if not sections:
        return []
    q = tokens(query)
    ranked = sorted(
        enumerate(sections),
        key=lambda item: (-score_section(item[1], q), item[0]),
    )
    picked: list[dict[str, str]] = []
    used: set[int] = set()
    for idx, sec in ranked:
        if score_section(sec, q) <= 0:
            break
        picked.append(sec)
        used.add(idx)
        if len(picked) >= cap:
            return picked
    for idx, sec in enumerate(sections):
        if idx in used:
            continue
        picked.append(sec)
        if len(picked) >= cap:
            break
    return picked


def format_company_context(
    pack: dict[str, Any] | None,
    *,
    query: str = "",
    index_only: bool = False,
) -> str:
    if not pack:
        return "(не задан — живой документ компании недоступен)"
    err = str(pack.get("_live_share_error") or "").strip()
    live = pack.get("_live_share") if isinstance(pack.get("_live_share"), dict) else {}
    title = str((live or {}).get("title") or "")
    updated = str((live or {}).get("updated_at") or "").strip()
    sections = pack_sections(pack)
    if not sections:
        if err:
            return f"(живой документ недоступен: {err})"
        return "(пусто)"

    budget = ctx_char_budget()
    header_bits = ["КОМПАНИЯ (живой документ)"]
    if title:
        header_bits.append(f"«{title}»")
    if updated:
        header_bits.append(f"обновлён {updated[:16]}")
    header = " ".join(header_bits)
    index = catalog_index(sections)
    parts = [header, "Каталог:", index]
    used = sum(len(p) for p in parts) + 8
    if index_only:
        text = "\n".join(parts)
        return text if len(text) <= budget else text[: budget - 1] + "…"

    remain = budget - used - 80
    if remain > 200:
        excerpts = select_sections(sections, query)
        body_chunks: list[str] = ["Фрагменты по текущей задаче (полный каталог не дублируется):"]
        for sec in excerpts:
            label = (sec.get("title") or "Фрагмент").strip()
            body = (sec.get("text") or "").strip()
            if len(body) > SECTION_CAP:
                body = body[: SECTION_CAP - 1] + "…"
            piece = f"## {label}\n{body}"
            room = remain - sum(len(x) for x in body_chunks) - 2
            if room <= 80:
                break
            if len(piece) > room:
                piece = piece[: room - 1] + "…"
            body_chunks.append(piece)
        parts.extend(body_chunks)
    text = "\n".join(parts).strip()
    if err:
        text += f"\n(часть документа: {err})"
    if len(text) > budget:
        return text[: budget - 1] + "…"
    return text


def prepare_meeting_brief(pack: dict[str, Any] | None, query: str) -> str:
    return format_company_context(pack, query=query, index_only=False)
