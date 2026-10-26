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


def _sheet_sources(note: dict[str, Any], kind: str, *, skip_titles: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    """Дополнительные текстовые листы заметки — каждый своим источником (часто важное именно там).
    Канвасы (презентации, страницы) не индексируем: в них вёрстка, а текст — из листов заметки."""
    from assistant.stores import note_sheets

    try:
        sheets = note_sheets.list_extra_sheets(int(note["id"]))
    except Exception as e:
        print(f"[semantic] sheets note={note.get('id')} err={e!r}")
        return []
    out = []
    base = str(note.get("title") or {"transcription": "Встреча", "knowledge": "Документ"}.get(kind, "Заметка"))
    for sh in sheets:
        sheet_kind = str(sh.get("kind") or "doc")
        if sheet_kind == "web":
            continue
        title = str(sh.get("title") or "").strip()
        if title in skip_titles:
            continue
        raw_body = str(sh.get("body") or sh.get("description") or "")
        if sheet_kind == "board":
            from assistant.lib.board_doc import board_plain_text

            text = " ".join(board_plain_text(raw_body).split())
        else:
            text = _plain(raw_body)
        if len(text) < 40:
            continue
        out.append({"key": f"sheet:{note['id']}:{sh['id']}", "kind": "knowledge" if kind == "knowledge" else "note", "title": f"{base} · {title or 'Лист'}",
                    "date": str(sh.get("updated_at") or note.get("updated_at") or note.get("created_at") or "")[:10],
                    "text": text, "ref": {"note_id": int(note["id"]), "sheet_id": int(sh["id"]), "sheet_title": title, "kind": kind}})
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
        # Лист «Транскрипция» уже выше (tr_txt) — остальные листы встречи отдельно.
        skip = (notes_store.TRANSCRIPT_SHEET_TITLE,) if tnotes.has_summary(full) else ()
        out += _sheet_sources(full, "transcription", skip_titles=skip)
    for n in notes_store.list_notes(uid, limit=500):
        text = _plain(str(n.get("body") or ""))
        if len(text) >= 40:
            out.append({"key": f"note:{n['id']}", "kind": "note", "title": n.get("title") or "Заметка",
                        "date": str(n.get("updated_at") or n.get("created_at") or "")[:10], "text": text,
                        "ref": {"note_id": int(n["id"]), "kind": "note"}})
        out += _sheet_sources(n, "note")
    # База знаний (свои документы и расшаренные командой) — тоже в памяти, со всеми листами.
    try:
        for n in notes_store.list_knowledge_notes(uid):
            text = _plain(str(n.get("body") or ""))
            if len(text) >= 40:
                out.append({"key": f"kb:{n['id']}", "kind": "knowledge", "title": n.get("title") or "Документ",
                            "date": str(n.get("updated_at") or n.get("created_at") or "")[:10], "text": text,
                            "ref": {"note_id": int(n["id"]), "kind": "knowledge"}})
            out += _sheet_sources(n, "knowledge")
    except Exception as e:
        print(f"[semantic] knowledge uid={uid} err={e!r}")
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
    try:
        out += task_sources(uid)
    except Exception as e:
        print(f"[semantic] tasks uid={uid} err={e!r}")
    try:
        from assistant.services import mail_memory

        out += mail_memory.semantic_sources(uid)  # переписка с людьми
    except Exception as e:
        print(f"[semantic] mail uid={uid} err={e!r}")
    return out


TASK_LIMIT = 1000


def _task_actor(task: dict[str, Any], ev: dict[str, Any]) -> str:
    name = str(ev.get("actor_name") or "").strip()
    if name:
        return name
    actor = str(ev.get("actor_user_id") or "")
    if actor and actor == str(task.get("assignee_user_id") or ""):
        return str(task.get("assignee_name") or "").strip() or "Исполнитель"
    return "Постановщик"


def task_sources(uid: int) -> list[dict[str, Any]]:
    """Задачи с содержанием (описание, результат, переписка с исполнителем) — у постановщика и исполнителя.
    Голые задачи из одного названия не индексируем: их и так находит tasks_search."""
    from assistant.lib.task_notify import task_webapp_url
    from assistant.stores import calendar_tasks

    tasks = calendar_tasks.list_involved_tasks(uid, limit=TASK_LIMIT)
    talk = calendar_tasks.discussion_by_task([int(t["id"]) for t in tasks if t.get("assignee_user_id")])
    out: list[dict[str, Any]] = []
    for t in tasks:
        tid = int(t["id"])
        body: list[str] = []
        desc = _plain(str(t.get("description") or "")).strip()
        if desc:
            body.append(f"Описание: {desc}")
        checklist = [c for c in t.get("checklist") or [] if isinstance(c, dict) and str(c.get("text") or "").strip()]
        if checklist:
            body.append("Чек-лист: " + "; ".join(("✓ " if c.get("done") else "") + str(c["text"]).strip() for c in checklist))
        msgs = talk.get(tid) or []
        if msgs:
            body.append("Переписка с исполнителем:")
            for ev in msgs:
                prefix = "вернул(а) на доработку: " if ev["kind"] == "returned" else ""
                body.append(f"{_task_actor(t, ev)} ({ev['at'][:10]}): {prefix}{ev['text'].strip()}")
        result = _plain(str(t.get("result") or "")).strip()
        if result:
            body.append(f"Итог / результат: {result}")
        if len(" ".join(body)) < 40:
            continue
        head = [f"Задача «{t.get('title') or 'Задача'}»."]
        if t.get("assignee_name"):
            head.append(f"Исполнитель: {t['assignee_name']}.")
        head.append("Статус: выполнена." if t.get("done") else "Статус: не выполнена.")
        out.append({"key": f"task:{tid}", "kind": "task", "title": f"Задача «{t.get('title') or 'Задача'}»",
                    "date": str(t.get("updated_at") or t.get("created_at") or "")[:10],
                    "text": " ".join(head) + "\n" + "\n".join(body),
                    "ref": {"task_id": tid, "kind": "task", "url": task_webapp_url(tid)}})
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
    meta = idx.source_meta(uid)
    have = {k: v[0] for k, v in meta.items()}
    seen: set[str] = set()
    done = skipped = chunks_n = 0
    # сначала короткое и ценное (саммари, заметки, База знаний, дайджесты), длинные транскрипты — последними
    order = {"meeting_summary": 0, "note": 1, "knowledge": 1, "task": 2, "chat_digest": 2, "mail_thread": 3, "transcript": 4}
    for src in sorted(sources_for_user(uid), key=lambda x: order.get(x["kind"], 9)):
        seen.add(src["key"])
        h = _hash(src["text"])
        if have.get(src["key"]) == h:
            # Текст тот же, а название / ссылка поменялись (переименовали заметку) — обновляем подпись.
            title = str(src["title"])[:200]
            ref_json = json.dumps(src["ref"], ensure_ascii=False)
            _h, old_title, old_ref = meta[src["key"]]
            if old_title != title or old_ref != ref_json:
                idx.update_meta(uid, src["key"], title=title, date=src["date"], ref_json=ref_json)
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


TITLE_MATCH_RANK = 0.8
# Слова, по которым название не считается «упомянутым» (иначе «Задачи», «Заметка» всплывали бы всегда).
_GENERIC_TITLE_STEMS = {"замет", "задач", "лист", "докум", "встре", "запис", "чат", "без", "назва", "новая", "новый",
                        "транс", "самма", "итоги", "недел", "план", "день", "финал", "описа", "копия"}


def _title_stems(title: str) -> set[str]:
    """Значимые слова названия (без «лист», «заметка»…); пусто — название слишком общее для совпадения."""
    return _stems(re.sub(r"[«»\"]", " ", str(title or ""))) - _GENERIC_TITLE_STEMS


def _note_url(note_id: Any) -> str:
    try:
        from assistant.stores.note_members import note_webapp_url

        return note_webapp_url(int(note_id))
    except Exception:
        return ""


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
    # Название заметки / листа целиком есть в вопросе («дай ссылку на заметку Вебапп», «для чего нужен
    # продукт Обучат Деск») — этот источник наверх, даже если по смыслу текст далёк от короткого вопроса.
    by_key = {}
    for h in hits:
        by_key.setdefault(h["source_key"], h)
    for fc in idx.first_chunks(uid):
        ts = _title_stems(fc["title"])
        if not ts or not ts <= qs:
            continue
        # Название целиком в вопросе — выше, чем лист этой заметки («Вебапп» выше «Вебапп · Лист 2»).
        exact = _stems(re.sub(r"[«»\"]", " ", str(fc["title"] or ""))) <= qs
        boost = TITLE_MATCH_RANK + 0.02 * len(ts) + (0.05 if exact else 0.0)
        cur = by_key.get(fc["source_key"])
        if cur is not None:
            cur["rank"] = max(cur["rank"], boost)
        else:
            fc = dict(fc, score=0.0, rank=boost)
            hits.append(fc)
            by_key[fc["source_key"]] = fc
    hits.sort(key=lambda h: -h["rank"])
    out: list[dict[str, Any]] = []
    per_source: dict[str, int] = {}
    for h in hits:
        if per_source.get(h["source_key"], 0) >= 2:
            continue
        per_source[h["source_key"]] = per_source.get(h["source_key"], 0) + 1
        ref = json.loads(h["ref_json"] or "{}")
        item = {
            "title": h["title"], "date": h["date"], "kind": h["kind"], "snippet": h["text"][:700],
            "score": round(h["rank"], 3), "ref": ref,
        }
        if ref.get("note_id"):
            item["url"] = _note_url(ref["note_id"])  # «дай ссылку на заметку» — ссылка готова
        elif ref.get("url"):
            item["url"] = ref["url"]
        out.append(item)
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------- по событию и статус

import threading
import time

NOTE_DEBOUNCE_SEC = 60.0
_TIMERS: dict[int, threading.Timer] = {}
_RUNNING: set[int] = set()
_T_LOCK = threading.Lock()
_STATUS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_STATUS_TTL = 20.0


def _index_now(uid: int) -> None:
    with _T_LOCK:
        if uid in _RUNNING:
            return
        _RUNNING.add(uid)
    try:
        res = index_user(uid, budget_chunks=2000)
        _STATUS_CACHE.pop(uid, None)
        if res["indexed"] or res["removed"]:
            print(f"[semantic] on-change uid={uid} {res}")
    except Exception as e:
        print(f"[semantic] on-change uid={uid} err={e!r}")
    finally:
        with _T_LOCK:
            _RUNNING.discard(uid)


def note_changed(uid: Any, *, delay: float = NOTE_DEBOUNCE_SEC) -> None:
    """Материал изменился — переиндексировать пользователя через delay секунд (повторы откладывают)."""
    if not embed_client.enabled():
        return
    try:
        u = int(uid)
    except (TypeError, ValueError):
        return
    timer = threading.Timer(delay, _index_now, args=(u,))
    timer.daemon = True
    with _T_LOCK:
        old = _TIMERS.get(u)
        if old is not None:
            old.cancel()
        _TIMERS[u] = timer
    timer.start()


def status(uid: int) -> dict[str, Any]:
    """Сколько материалов уже в памяти: total, indexed, pending (названия первых в очереди)."""
    if not embed_client.enabled():
        return {"enabled": False}
    now = time.time()
    hit = _STATUS_CACHE.get(int(uid))
    if hit and now - hit[0] < _STATUS_TTL:
        return hit[1]
    have = idx.source_hashes(uid)
    total = indexed = 0
    pending: list[str] = []
    for src in sources_for_user(uid):
        total += 1
        if have.get(src["key"]) == _hash(src["text"]):
            indexed += 1
        elif len(pending) < 5:
            title = str(src["title"] or "")
            if title not in pending:
                pending.append(title)
    out = {"enabled": True, "total": total, "indexed": indexed, "pending": pending}
    _STATUS_CACHE[int(uid)] = (now, out)
    return out
