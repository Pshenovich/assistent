"""Семантическая память (B6): индекс заметок, саммари/транскриптов встреч и дайджестов чатов,
гибридный поиск (векторы + совпадение слов). Эмбеддинги — локальный сервис (embed_client).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Optional

from assistant.lib import embed_client
from assistant.stores import semantic_index as idx

CHUNK = 900
OVERLAP = 150
KEYWORD_WEIGHT = 0.15

Embed = Callable[[list[str]], list[list[float]]]


def _plain(html_text: str) -> str:
    try:
        from assistant.lib.telegram_html import html_to_plain

        return html_to_plain(html_text or "")
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_text or "")


def chunk_text(text: str, size: int = CHUNK, overlap: int = OVERLAP) -> list[str]:
    """Куски по абзацам/предложениям примерно по size символов с перекрытием."""
    t = re.sub(r"[ \t]+", " ", (text or "").strip())
    if not t:
        return []
    if len(t) <= size:
        return [t]
    parts = re.split(r"(?<=[.!?…])\s+|\n{2,}", t)
    out: list[str] = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        while len(p) > size:
            out.append((cur + " " + p[:size]).strip() if cur else p[:size])
            cur = ""
            p = p[size - overlap:]
        if cur and len(cur) + len(p) + 1 > size:
            out.append(cur)
            cur = cur[-overlap:].split(" ", 1)[-1] if overlap else ""
        cur = (cur + " " + p).strip()
    if cur:
        out.append(cur)
    return out


def sources_for_user(uid: int) -> list[dict[str, Any]]:
    """Тексты пользователя для индекса: key, kind, title, date, text, ref."""
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    out: list[dict[str, Any]] = []
    for n in notes_store.list_transcription_notes(uid, limit=500):
        if str(n.get("owner_user_id") or n.get("user_id") or uid) != str(uid):
            continue
        full = notes_store.get_note(uid, int(n["id"])) or n
        summary = _plain(str(full.get("body") or "")) if tnotes.has_summary(full) else ""
        transcript = _plain(tnotes.transcript_html(full))
        date = str(full.get("created_at") or "")[:10]
        ref = {"note_id": int(full["id"]), "kind": "transcription"}
        if summary:
            out.append({"key": f"tr_sum:{full['id']}", "kind": "meeting_summary", "title": full.get("title") or "Встреча",
                        "date": date, "text": summary, "ref": ref})
        if transcript:
            out.append({"key": f"tr_txt:{full['id']}", "kind": "transcript", "title": full.get("title") or "Встреча",
                        "date": date, "text": transcript, "ref": ref})
    for n in notes_store.list_notes(uid, limit=500):
        text = _plain(str(n.get("body") or ""))
        if len(text) < 40:
            continue
        out.append({"key": f"note:{n['id']}", "kind": "note", "title": n.get("title") or "Заметка",
                    "date": str(n.get("updated_at") or n.get("created_at") or "")[:10], "text": text,
                    "ref": {"note_id": int(n["id"]), "kind": "note"}})
    try:
        from assistant.stores import chat_digest

        chats = chat_digest.list_chats_for_user(uid)
        for rep in chat_digest.list_reports_for_chats([c["chat_id"] for c in chats], limit=200):
            s = rep.get("summary") or {}
            text = _digest_text(s)
            if len(text) < 40:
                continue
            out.append({"key": f"digest:{rep['chat_id']}:{rep['report_date']}", "kind": "chat_digest",
                        "title": f"Чат «{rep.get('title')}»", "date": rep["report_date"], "text": text,
                        "ref": {"chat_id": rep["chat_id"], "date": rep["report_date"]}})
    except Exception as e:
        print(f"[semantic] digests uid={uid} err={e!r}")
    return out


def _digest_text(s: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in s.items():
        if isinstance(v, str) and v.strip():
            lines.append(v.strip())
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    lines.append(" — ".join(str(x.get(f) or "") for f in ("assignee", "task", "text", "deadline") if x.get(f)))
                elif str(x or "").strip():
                    lines.append(str(x).strip())
    return "\n".join(l for l in lines if l)


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def index_user(uid: int, *, embed: Optional[Embed] = None, budget_chunks: int = 400) -> dict[str, int]:
    """Переиндексировать изменившиеся источники (не больше budget_chunks кусков за проход)."""
    embed = embed or embed_client.embed
    have = idx.source_hashes(uid)
    seen: set[str] = set()
    done = skipped = chunks_n = 0
    for src in sources_for_user(uid):
        seen.add(src["key"])
        h = _hash(src["text"])
        if have.get(src["key"]) == h:
            skipped += 1
            continue
        chunks = chunk_text(src["text"])
        if not chunks:
            continue
        if chunks_n + len(chunks) > budget_chunks and chunks_n:
            continue  # доиндексируем в следующий проход
        # заголовок в каждом куске — поиск «по встрече X» находит её куски
        vecs = embed([f"{src['title']}. {c}" for c in chunks])
        idx.upsert_source(uid, src["key"], content_hash=h, kind=src["kind"], title=str(src["title"])[:200],
                          date=src["date"], ref_json=json.dumps(src["ref"], ensure_ascii=False), chunks=chunks, vecs=vecs)
        chunks_n += len(chunks)
        done += 1
    removed = 0
    for key in set(have) - seen:
        idx.delete_source(uid, key)
        removed += 1
    return {"indexed": done, "skipped": skipped, "removed": removed, "chunks": chunks_n}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-яё0-9]{3,}", (text or "").lower().replace("ё", "е"))}


def search(uid: int, query: str, *, k: int = 8, embed: Optional[Embed] = None) -> list[dict[str, Any]]:
    """Лучшие куски: близость векторов + бонус за совпадение слов; не больше 2 кусков с источника."""
    q = (query or "").strip()
    if not q:
        return []
    embed = embed or embed_client.embed
    qvec = embed([q])[0]
    hits = idx.search(uid, qvec, k=60)
    qs = _stems(q)
    for h in hits:
        overlap = len(qs & _stems(h["text"] + " " + h["title"])) / max(1, len(qs))
        h["rank"] = h["score"] + KEYWORD_WEIGHT * overlap
    hits.sort(key=lambda h: -h["rank"])
    out: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for h in hits:
        if per_source.get(h["source_key"], 0) >= 2:
            continue
        per_source[h["source_key"]] = per_source.get(h["source_key"], 0) + 1
        out.append({
            "title": h["title"], "date": h["date"], "kind": h["kind"], "snippet": h["text"][:700],
            "score": round(h["rank"], 3), "ref": json.loads(h["ref_json"] or "{}"),
        })
        if len(out) >= k:
            break
    return out
