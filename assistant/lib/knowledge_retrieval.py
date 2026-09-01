"""Поиск по корпоративным базам знаний."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from assistant.lib.kb_html_format import normalize_kb_answer_markdown
from assistant.lib.kb_search import (
    expand_kb_search_queries,
    extract_kb_search_query,
    resolve_kb_name_and_search_query,
)
from assistant.stores import knowledge_base_store as kb_store

_CONTEXT_MAX_CHARS = 12000
_FRAGMENT_TEXT_MAX = 3500

_KB_QA_MARKERS = re.compile(
    r"(?:баз[аы]\s+знан|в\s+базе|по\s+базе|корпоративн\w*\s+баз|"
    r"должностн\w*\s+регламент|регламент\w*|интеграц\w*)",
    re.IGNORECASE,
)
_KB_SEARCH_INTRO_RE = re.compile(
    r"^(?:найди|найти|ищи|покажи|поиск|расскажи|что\s+есть)\s+"
    r"(?:мне\s+)?(?:в\s+)?(?:базе\s+знан\w*|баз[ае]\w*)\s+"
    r"(?:(?:про|об|по)\s+)?(.+)$",
    re.IGNORECASE | re.UNICODE,
)
_QUESTION_HINT = re.compile(
    r"(?:[?]|^(?:что|как|какие|кто|где|когда|перечисли|найди|расскаж))",
    re.IGNORECASE,
)
_KB_LOOKUP_RE = re.compile(
    r"(?:найди|найти|покажи|ищи|где|какие?\s+доступ)",
    re.IGNORECASE,
)
_KB_ACCESS_RE = re.compile(
    r"(?:доступ|логин|парол|кабинет|учётн|учетн|credentials|testcabinet)",
    re.IGNORECASE,
)


@dataclass
class KbFragment:
    kb_id: str
    kb_title: str
    kb_source_url: str
    kb_source_type: str
    doc_id: str
    doc_title: str
    doc_url: str
    heading: str
    text: str
    chunk_index: int
    score: float


def is_knowledge_base_query(text: str) -> bool:
    s = (text or "").strip()
    if len(s) < 5:
        return False
    if _KB_SEARCH_INTRO_RE.search(s):
        return True
    if _KB_QA_MARKERS.search(s) and _QUESTION_HINT.search(s):
        return True
    if re.search(r"\bв\s+базе\b", s, re.IGNORECASE) and _QUESTION_HINT.search(s):
        return True
    return False


def knowledge_qa_likely_user_text(text: str) -> bool:
    return is_knowledge_base_query(text)


def should_quote_kb_context_directly(question: str) -> bool:
    s = (question or "").strip()
    if not s:
        return False
    return bool(_KB_LOOKUP_RE.search(s) and _KB_ACCESS_RE.search(s))


def _hit_to_fragment(hit: dict[str, Any]) -> KbFragment:
    return KbFragment(
        kb_id=str(hit.get("kb_id") or ""),
        kb_title=str(hit.get("kb_title") or ""),
        kb_source_url=str(hit.get("kb_source_url") or ""),
        kb_source_type=str(hit.get("kb_source_type") or ""),
        doc_id=str(hit.get("doc_id") or ""),
        doc_title=str(hit.get("doc_title") or ""),
        doc_url=str(hit.get("doc_url") or ""),
        heading=str(hit.get("heading") or ""),
        text=str(hit.get("text") or "")[:_FRAGMENT_TEXT_MAX],
        chunk_index=int(hit.get("chunk_index") or 0),
        score=float(hit.get("score") or 0),
    )


def _filter_hits_primary_document(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not hits:
        return hits
    top = hits[0]
    kb_id = str(top.get("kb_id") or "")
    doc_id = str(top.get("doc_id") or "")
    return [
        h
        for h in hits
        if str(h.get("kb_id") or "") == kb_id and str(h.get("doc_id") or "") == doc_id
    ]


def _expand_top_document_chunks(
    hits: list[dict[str, Any]],
    hits_by_id: dict[int, dict[str, Any]],
) -> None:
    if not hits:
        return
    top = hits[0]
    base_score = float(top.get("score") or 0)
    for row in kb_store.list_kb_chunks_for_doc(
        str(top.get("kb_id") or ""),
        str(top.get("doc_id") or ""),
    ):
        hid = int(row.get("id") or 0)
        if hid in hits_by_id:
            continue
        row["score"] = base_score
        hits_by_id[hid] = row


def format_kb_direct_answer(fragments: list[KbFragment]) -> str:
    if not fragments:
        return ""
    top = max(fragments, key=lambda f: f.score)
    doc_frags = [
        f
        for f in fragments
        if f.kb_id == top.kb_id and f.doc_id == top.doc_id
    ]
    doc_frags.sort(key=lambda f: f.chunk_index)
    lines: list[str] = []
    title_shown = False
    doc_title_l = (top.doc_title or "").strip().lower()
    for frag in doc_frags:
        block = normalize_kb_answer_markdown(frag.text.strip())
        if not block:
            continue
        heading_l = (frag.heading or "").strip().lower()
        if (
            heading_l
            and heading_l != doc_title_l
            and heading_l not in block[:120].lower()
        ):
            block = f"**{frag.heading.strip()}**\n{block}"
        elif not title_shown and doc_title_l and not block.lower().startswith("## "):
            lines.append(f"## {top.doc_title}")
            title_shown = True
        elif not title_shown and doc_title_l:
            title_shown = True
        if block in lines:
            continue
        lines.append(block)
    body = "\n\n".join(lines).strip()
    return normalize_kb_answer_markdown(body)


def retrieve_kb_context(
    telegram_user_id: int,
    parsed: dict[str, Any],
    *,
    original_question: str = "",
) -> tuple[list[KbFragment], str]:
    kbs = kb_store.list_knowledge_bases_for_user(int(telegram_user_id))
    if not kbs:
        return [], ""
    kb_ids = [str(kb["id"]) for kb in kbs if kb.get("id")]
    kb_title_by_id = {str(kb["id"]): str(kb.get("title") or "") for kb in kbs}
    kb_titles = [
        {"id": str(kb["id"]), "title": str(kb.get("title") or "")} for kb in kbs
    ]

    target_kb_id = str(parsed.get("kb_id") or "").strip()
    kb_name, search_query = resolve_kb_name_and_search_query(
        original_question,
        knowledge_bases=kb_titles,
        parsed_kb_name=str(parsed.get("kb_name") or ""),
        parsed_search_query=str(parsed.get("search_query") or ""),
    )
    if target_kb_id and target_kb_id in kb_ids:
        kb_ids = [target_kb_id]
    elif kb_name:
        matched = [
            kb_id
            for kb_id, title in kb_title_by_id.items()
            if kb_name in title.lower()
        ]
        if matched:
            kb_ids = matched

    queries: list[str] = []
    if search_query:
        for q in expand_kb_search_queries(search_query):
            if q not in queries:
                queries.append(q)
    local_query = extract_kb_search_query(original_question)
    if local_query:
        for q in expand_kb_search_queries(local_query):
            if q not in queries:
                queries.append(q)
    if original_question and original_question not in queries:
        queries.append(original_question)

    hits_by_id: dict[int, dict[str, Any]] = {}
    for q in queries:
        for hit in kb_store.search_kb_chunks(kb_ids, q, limit=12):
            hid = int(hit.get("id") or 0)
            prev = hits_by_id.get(hid)
            if prev is None or float(hit.get("score") or 0) > float(prev.get("score") or 0):
                hits_by_id[hid] = hit
    hits = sorted(
        hits_by_id.values(),
        key=lambda h: (-float(h.get("score") or 0), int(h.get("chunk_index") or 0)),
    )[:12]
    _expand_top_document_chunks(hits, hits_by_id)
    hits = sorted(
        hits_by_id.values(),
        key=lambda h: (-float(h.get("score") or 0), int(h.get("chunk_index") or 0)),
    )
    hits = _filter_hits_primary_document(hits)
    fragments: list[KbFragment] = []
    lines: list[str] = []
    total = 0
    for hit in hits:
        frag = _hit_to_fragment(hit)
        block = (
            f"[База: {frag.kb_title} | Документ: {frag.doc_title}"
            + (f" | {frag.heading}" if frag.heading else "")
            + f"]\n{frag.text}"
        )
        if total + len(block) > _CONTEXT_MAX_CHARS:
            break
        fragments.append(frag)
        lines.append(block)
        total += len(block) + 2
    return fragments, "\n\n".join(lines).strip()


def describe_kb_index_issues(kbs: list[dict[str, Any]]) -> str | None:
    """Сообщение, если индекс пуст или синхронизация упала (а не «ничего не нашёл»)."""
    if not kbs:
        return None
    empty = [kb for kb in kbs if int(kb.get("chunk_count") or 0) <= 0]
    if not empty:
        return None
    errors = [
        str(kb.get("sync_error") or "").strip()
        for kb in empty
        if str(kb.get("sync_status") or "").strip().lower() == "error"
        and str(kb.get("sync_error") or "").strip()
    ]
    if errors:
        err = errors[0]
        if len(err) > 160:
            err = err[:157] + "…"
        if "401" in err or "Unauthorized" in err:
            return (
                "База знаний ещё не проиндексирована: вебхук Битрикс24 на сервере "
                "недействителен или без прав landing. Обновите BITRIX24_WEBHOOK_URL "
                "и нажмите «Синхронизировать» в мини-приложении."
            )
        return (
            f"База знаний ещё не проиндексирована: {err} "
            "Обновите источник и нажмите «Синхронизировать» в мини-приложении."
        )
    return (
        "База знаний пока пуста — нажмите «Синхронизировать» в мини-приложении "
        "(Профиль → База знаний)."
    )
