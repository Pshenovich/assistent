"""URL документов и строка «Источник» для ответов по базе знаний."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from assistant.lib.knowledge_retrieval import KbFragment

_MD_LINK_LABEL_RE = re.compile(r"([\[\]\\])")
_SOURCE_LINE_RE = re.compile(
    r"(?:^|\n)\s*(?:\*\*)?Источник(?:\*\*)?:[^\n]*",
    re.IGNORECASE | re.MULTILINE,
)


def escape_md_link_label(text: str) -> str:
    return _MD_LINK_LABEL_RE.sub(r"\\\1", (text or "").strip() or "—")


def resolve_doc_url(
    *,
    source_type: str,
    kb_source_url: str,
    doc_id: str,
    stored_url: str = "",
) -> str:
    url = (stored_url or "").strip()
    if url:
        return url
    kb_url = (kb_source_url or "").strip()
    doc = (doc_id or "").strip()
    st = (source_type or "").strip().lower()
    if st == "bitrix24_knowledge" and kb_url and doc:
        from assistant.integrations.knowledge_bitrix24 import parse_bitrix_knowledge_url

        ref = parse_bitrix_knowledge_url(kb_url)
        if ref:
            return f"https://{ref.portal_host}/knowledge/{ref.kb_code}/{doc}/"
    if st == "google_docs":
        if kb_url:
            base = kb_url.split("?")[0].rstrip("/")
            return base if base.endswith("/edit") else f"{base}/edit"
        if doc:
            return f"https://docs.google.com/document/d/{doc}/edit"
    if st in {"notion", "yandex_disk"} and kb_url:
        return kb_url
    return ""


def strip_kb_source_line(text: str) -> str:
    return _SOURCE_LINE_RE.sub("", text or "").rstrip()


def _md_link(label: str, url: str) -> str:
    u = (url or "").strip()
    if not u:
        return escape_md_link_label(label)
    return f"[{escape_md_link_label(label)}]({u})"


def _html_link(label: str, url: str) -> str:
    u = (url or "").strip()
    title = html.escape((label or "").strip() or "—")
    if not u:
        return title
    return f'<a href="{html.escape(u, quote=True)}">{title}</a>'


def _resolve_fragment_doc_url(frag: KbFragment) -> str:
    doc_url = (frag.doc_url or "").strip()
    if doc_url:
        return doc_url
    return resolve_doc_url(
        source_type=frag.kb_source_type,
        kb_source_url=frag.kb_source_url,
        doc_id=frag.doc_id,
    )


def _unique_docs(fragments: list[KbFragment]) -> list[tuple[str, str, str]]:
    unique_docs: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for frag in sorted(fragments, key=lambda f: -f.score):
        did = (frag.doc_id or "").strip()
        if not did or did in seen:
            continue
        seen.add(did)
        unique_docs.append((did, frag.doc_title or did, _resolve_fragment_doc_url(frag)))
    return unique_docs


def primary_kb_fragments(fragments: list[KbFragment]) -> list[KbFragment]:
    """Фрагменты главного документа (для футера «Источник»)."""
    if not fragments:
        return []
    top = max(fragments, key=lambda f: f.score)
    return [
        f
        for f in fragments
        if f.kb_id == top.kb_id and f.doc_id == top.doc_id
    ]


def format_kb_sources_footer(fragments: list[KbFragment]) -> str:
    if not fragments:
        return ""
    source_frags = primary_kb_fragments(fragments)
    top = max(source_frags, key=lambda f: f.score)
    kb_title = top.kb_title or "База знаний"
    kb_url = (top.kb_source_url or "").strip()
    unique_docs = _unique_docs(source_frags)

    kb_part = _md_link(kb_title, kb_url)
    if len(unique_docs) == 1:
        _, doc_title, doc_url = unique_docs[0]
        doc_part = _md_link(doc_title, doc_url)
    else:
        doc_part = ", ".join(_md_link(title, url) for _, title, url in unique_docs)
    return f"Источник: {kb_part} | Документ: {doc_part}"


def format_kb_sources_footer_html(fragments: list[KbFragment]) -> str:
    """HTML-футер со ссылками (надёжнее в Rich Message)."""
    if not fragments:
        return ""
    source_frags = primary_kb_fragments(fragments)
    top = max(source_frags, key=lambda f: f.score)
    kb_title = top.kb_title or "База знаний"
    kb_url = (top.kb_source_url or "").strip()
    unique_docs = _unique_docs(source_frags)

    kb_part = _html_link(kb_title, kb_url)
    if len(unique_docs) == 1:
        _, doc_title, doc_url = unique_docs[0]
        doc_part = _html_link(doc_title, doc_url)
    else:
        doc_part = ", ".join(_html_link(title, url) for _, title, url in unique_docs)
    return f"<p>Источник: {kb_part} | Документ: {doc_part}</p>"
