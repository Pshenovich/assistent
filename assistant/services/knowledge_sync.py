"""Синхронизация и chunking корпоративных баз знаний."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from assistant.integrations import (
    knowledge_bitrix24,
    knowledge_google_docs,
    knowledge_notion,
    knowledge_yandex_disk,
)
from assistant.stores import knowledge_base_store as kb_store

_CHUNK_TARGET = 1000
_CHUNK_MIN = 400


def chunk_text(text: str, *, doc_title: str = "") -> list[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return []
    sections: list[tuple[str, str]] = []
    current_heading = doc_title or ""
    buf: list[str] = []
    for line in raw.splitlines():
        if re.match(r"^#{1,3}\s+", line.strip()):
            if buf:
                sections.append((current_heading, "\n".join(buf).strip()))
                buf = []
            current_heading = re.sub(r"^#{1,3}\s+", "", line.strip())
            continue
        if not line.strip() and buf:
            sections.append((current_heading, "\n".join(buf).strip()))
            buf = []
            current_heading = current_heading or doc_title
            continue
        buf.append(line)
    if buf:
        sections.append((current_heading, "\n".join(buf).strip()))
    if not sections:
        sections = [(doc_title or "", raw)]

    chunks: list[dict[str, str]] = []
    for heading, body in sections:
        body = body.strip()
        if not body:
            continue
        if len(body) <= _CHUNK_TARGET:
            chunks.append({"heading": heading, "text": body})
            continue
        paras = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        acc = ""
        for para in paras:
            candidate = f"{acc}\n\n{para}".strip() if acc else para
            if len(candidate) <= _CHUNK_TARGET:
                acc = candidate
                continue
            if acc:
                chunks.append({"heading": heading, "text": acc})
            if len(para) > _CHUNK_TARGET:
                for i in range(0, len(para), _CHUNK_TARGET):
                    part = para[i : i + _CHUNK_TARGET].strip()
                    if len(part) >= _CHUNK_MIN or not chunks:
                        chunks.append({"heading": heading, "text": part})
                acc = ""
            else:
                acc = para
        if acc:
            chunks.append({"heading": heading, "text": acc})
    return chunks


def _aggregate_hash(documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for doc in documents:
        h.update(str(doc.get("doc_id") or "").encode())
        h.update(str(doc.get("content_hash") or "").encode())
    for ch in chunks:
        h.update(str(ch.get("doc_id") or "").encode())
        h.update(str(ch.get("text") or "").encode())
    return h.hexdigest()


def _fetch_documents(kb: dict[str, Any]) -> list[Any]:
    source_type = str(kb.get("source_type") or "")
    url = str(kb.get("source_url") or "")
    meta = kb.get("source_meta") if isinstance(kb.get("source_meta"), dict) else {}
    owner_id = int(kb.get("owner_telegram_user_id") or 0)
    if source_type == "google_docs":
        return knowledge_google_docs.fetch_documents(url)
    if source_type == "notion":
        token = str(meta.get("notion_token") or "")
        return knowledge_notion.fetch_documents(url, notion_token=token)
    if source_type == "yandex_disk":
        return knowledge_yandex_disk.fetch_documents(
            url, owner_telegram_user_id=owner_id
        )
    if source_type == "bitrix24_knowledge":
        return knowledge_bitrix24.fetch_documents(
            url,
            owner_telegram_user_id=owner_id,
            source_meta=meta,
        )
    raise ValueError(f"Неизвестный тип источника: {source_type}")


def sync_knowledge_base(kb_id: str) -> dict[str, Any]:
    kb = kb_store.get_knowledge_base(kb_id)
    if not kb:
        raise ValueError("База знаний не найдена")
    kb_store.update_sync_status(kb_id, sync_status="syncing", sync_error="")
    try:
        fetched = _fetch_documents(kb)
        documents: list[dict[str, Any]] = []
        chunks: list[dict[str, Any]] = []
        for doc in fetched:
            text = (doc.text or "").strip()
            if not text:
                continue
            doc_hash = kb_store.content_hash(text)
            documents.append(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "content_hash": doc_hash,
                    "modified_at": doc.modified_at,
                    "url": str(getattr(doc, "url", "") or ""),
                }
            )
            for idx, ch in enumerate(chunk_text(text, doc_title=doc.title)):
                chunks.append(
                    {
                        "doc_id": doc.doc_id,
                        "heading": ch.get("heading") or doc.title,
                        "text": ch.get("text") or "",
                        "chunk_index": idx,
                    }
                )
        if not chunks:
            raise RuntimeError("Не удалось извлечь текст для индексации")
        agg = _aggregate_hash(documents, chunks)
        if agg == str(kb.get("content_hash") or ""):
            kb_store.update_sync_status(kb_id, sync_status="ok", chunk_count=len(chunks))
            return kb_store.get_knowledge_base(kb_id) or kb
        kb_store.replace_kb_index(
            kb_id,
            documents,
            chunks,
            aggregate_hash=agg,
        )
        return kb_store.get_knowledge_base(kb_id) or kb
    except Exception as e:
        kb_store.update_sync_status(kb_id, sync_status="error", sync_error=str(e))
        raise


def sync_all_knowledge_bases() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for kb in kb_store.list_all_active_kbs():
        kb_id = str(kb.get("id") or "")
        if not kb_id:
            continue
        try:
            results.append(sync_knowledge_base(kb_id))
        except Exception as e:
            print(f"[knowledge_sync] kb={kb_id} err={e!r}")
            results.append({"id": kb_id, "sync_status": "error", "sync_error": str(e)})
    return results
