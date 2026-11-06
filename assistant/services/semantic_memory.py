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


DOC_PREFIX = "doc:"
# Ключи источников документа (заметка, документ базы знаний, встреча и их листы). Раньше они жили копией
# в личном разделе каждого, кому документ открыт, а документы организаций — в разделе "org:<id>".
_DOC_KEY = re.compile(r"^(note|kb|org|tr_sum|tr_txt|sheet):(\d+)(?::\d+)?$")
_MAIN_KINDS = ("note", "kb", "org")  # основной лист документа — под любым из этих ключей один и тот же текст


def doc_owner_key(note_id: int) -> str:
    """Раздел индекса документа: векторизуется один раз, права проверяет поиск."""
    return f"{DOC_PREFIX}{int(note_id)}"


def _doc_key_note(key: str) -> Optional[int]:
    m = _DOC_KEY.match(key or "")
    return int(m.group(2)) if m else None


def _doc_key_norm(key: str) -> str:
    """note:5 / kb:5 / org:5 — один и тот же основной лист (для склейки старых копий с новым разделом)."""
    m = _DOC_KEY.match(key or "")
    if m and m.group(1) in _MAIN_KINDS:
        return f"main:{m.group(2)}"
    return key


def doc_sources(note: dict[str, Any], *, org_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Тексты одного документа для его раздела: key, kind, title, date, text, ref. Права — при поиске, не здесь.
    org_id — документ лежит в базе знаний организации (тогда он «документ базы знаний», где бы ни был)."""
    from assistant.services import transcription_notes as tnotes
    from assistant.stores import notes as notes_store

    nid = int(note["id"])
    out: list[dict[str, Any]] = []
    if note.get("is_transcription") and org_id is None:
        summary = _plain(str(note.get("body") or "")) if tnotes.has_summary(note) else ""
        transcript = _plain(tnotes.transcript_html(note))
        date = str(note.get("created_at") or "")[:10]
        ref = {"note_id": nid, "kind": "transcription"}
        if summary:
            out.append({"key": f"tr_sum:{nid}", "kind": "meeting_summary", "title": note.get("title") or "Встреча",
                        "date": date, "text": summary, "ref": ref})
        if transcript:
            out.append({"key": f"tr_txt:{nid}", "kind": "transcript", "title": note.get("title") or "Встреча",
                        "date": date, "text": transcript, "ref": ref})
        # Лист «Транскрипция» уже выше (tr_txt) — остальные листы встречи отдельно.
        skip = (notes_store.TRANSCRIPT_SHEET_TITLE,) if tnotes.has_summary(note) else ()
        return out + _sheet_sources(note, "transcription", skip_titles=skip)
    knowledge = bool(note.get("is_knowledge")) or org_id is not None
    kind = "knowledge" if knowledge else "note"
    text = _plain(str(note.get("body") or ""))
    ref: dict[str, Any] = {"note_id": nid, "kind": kind}
    if org_id is not None:
        ref["org_id"] = int(org_id)
    if len(text) >= 40:
        out.append({"key": f"{'kb' if knowledge else 'note'}:{nid}", "kind": kind,
                    "title": note.get("title") or ("Документ" if knowledge else "Заметка"),
                    "date": str(note.get("updated_at") or note.get("created_at") or "")[:10], "text": text, "ref": ref})
    for sh in _sheet_sources(note, kind):
        if org_id is not None:
            sh["ref"]["org_id"] = int(org_id)
        out.append(sh)
    return out


def _org_of_note(note_id: int) -> Optional[int]:
    try:
        from assistant.stores import org_kb

        node = org_kb.node_for_note(int(note_id))
        return int(node["org_id"]) if node and node.get("kind") == "doc" else None
    except Exception as e:
        print(f"[semantic] org of note={note_id} err={e!r}")
        return None


def readable_doc_ids(uid: int) -> tuple[set[int], list[int]]:
    """Документы, открытые человеку сейчас (свои, расшаренные, базы знаний организаций), и его организации."""
    from assistant.stores import note_members
    from assistant.stores import notes as notes_store

    ids = set(notes_store.own_note_ids(uid))
    ids |= {nid for _owner, nid in note_members.list_note_ids_for_member(uid)}
    org_ids: list[int] = []
    try:
        from assistant.stores import org_kb

        org_ids = org_kb.org_ids_of(uid)
        for oid in org_ids:
            ids |= org_kb.readable_note_ids(uid, oid)
    except Exception as e:
        print(f"[semantic] org docs uid={uid} err={e!r}")
    return ids, org_ids


def sources_for_user(uid: int) -> list[dict[str, Any]]:
    """Личные тексты для раздела пользователя: дайджесты чатов, задачи, почта. Документы — в своих разделах."""
    out: list[dict[str, Any]] = []
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
    """Переиндексировать изменившиеся личные источники (не больше budget_chunks кусков за проход).
    Старые копии документов в личном разделе не трогаем: их убирает index_docs, перенеся векторы."""
    return _index_sources(uid, sources_for_user(uid), embed=embed, budget_chunks=budget_chunks,
                          keep=lambda key: _doc_key_note(key) is not None)


def index_doc(note_id: int, *, embed: Optional[Embed] = None, budget_chunks: int = 2000,
              note: Optional[dict[str, Any]] = None, org_id: Any = ...) -> dict[str, int]:
    """Раздел одного документа — один раз для всех, кому он открыт. Удалён — раздел убираем."""
    from assistant.stores import notes as notes_store

    nid = int(note_id)
    note = note if note is not None else notes_store.get_note_by_id(nid)
    if not note:
        idx.delete_owner(doc_owner_key(nid))
        return {"indexed": 0, "copied": 0, "skipped": 0, "removed": 0, "chunks": 0, "pending": 0}
    oid = _org_of_note(nid) if org_id is ... else org_id
    return _index_sources(doc_owner_key(nid), doc_sources(note, org_id=oid), embed=embed,
                          budget_chunks=budget_chunks, reuse=True)


def index_docs(*, embed: Optional[Embed] = None, budget_chunks: int = 3000) -> dict[str, int]:
    """Полный проход по документам: досчитать изменившиеся (общий бюджет кусков на проход), убрать разделы
    удалённых и старые копии в личных разделах / разделах организаций — у документов, уже готовых здесь."""
    from assistant.stores import notes as notes_store

    try:
        from assistant.stores import org_kb

        org_of = {}
        for oid in _all_org_ids():
            for nid in org_kb.org_doc_note_ids(oid):
                org_of[nid] = oid
    except Exception as e:
        print(f"[semantic] org docs map err={e!r}")
        org_of = {}
    total = {"docs": 0, "indexed": 0, "copied": 0, "skipped": 0, "removed": 0, "chunks": 0, "pending": 0, "purged": 0}
    ready: set[int] = set()
    existing = notes_store.all_note_ids()
    for nid in existing:
        try:
            res = index_doc(nid, embed=embed, budget_chunks=budget_chunks - total["chunks"], org_id=org_of.get(nid))
        except Exception as e:
            print(f"[semantic] doc={nid} err={e!r}")
            continue
        total["docs"] += 1
        for k in ("indexed", "copied", "skipped", "removed", "chunks", "pending"):
            total[k] += res[k]
        if not res["pending"]:
            ready.add(nid)
    alive = set(existing)
    for owner in idx.owners_with_prefix(DOC_PREFIX):
        try:
            gone = int(owner[len(DOC_PREFIX):]) not in alive
        except ValueError:
            gone = True
        if gone:
            idx.delete_owner(owner)
            total["removed"] += 1
    # Старые копии: документ уже целиком в своём разделе (или удалён) — копия больше не нужна.
    for owner, key in idx.sources_outside(DOC_PREFIX):
        nid = _doc_key_note(key)
        if nid is not None and (nid in ready or nid not in alive):
            idx.delete_source(owner, key)
            total["purged"] += 1
    return total


def _all_org_ids() -> list[int]:
    from assistant.stores import orgs_store

    return orgs_store.all_org_ids()


def index_org(org_id: int, *, embed: Optional[Embed] = None, budget_chunks: int = 2000) -> dict[str, int]:
    """Документы базы знаний организации — каждый в своём разделе (как любой документ)."""
    from assistant.stores import org_kb

    total = {"indexed": 0, "copied": 0, "skipped": 0, "removed": 0, "chunks": 0, "pending": 0}
    for nid in org_kb.org_doc_note_ids(org_id):
        res = index_doc(nid, embed=embed, budget_chunks=budget_chunks - total["chunks"], org_id=int(org_id))
        for k in total:
            total[k] += res[k]
    return total


def _index_sources(owner: Any, sources: list[dict[str, Any]], *, embed: Optional[Embed] = None,
                   budget_chunks: int = 400, reuse: bool = False,
                   keep: Optional[Callable[[str], bool]] = None) -> dict[str, int]:
    """reuse — сначала искать готовые векторы того же текста в старых разделах (перенос без пересчёта);
    keep(key) — не удалять такие ключи, даже если источника больше нет."""
    uid = owner
    embed = embed or embed_client.embed
    meta = idx.source_meta(uid)
    have = {k: v[0] for k, v in meta.items()}
    seen: set[str] = set()
    done = copied = skipped = pending = chunks_n = 0
    # сначала короткое и ценное (саммари, заметки, База знаний, дайджесты), длинные транскрипты — последними
    order = {"meeting_summary": 0, "note": 1, "knowledge": 1, "task": 2, "chat_digest": 2, "mail_thread": 3, "transcript": 4}
    for src in sorted(sources, key=lambda x: order.get(x["kind"], 9)):
        seen.add(src["key"])
        h = _hash(src["text"])
        title = str(src["title"])[:200]
        ref_json = json.dumps(src["ref"], ensure_ascii=False)
        if have.get(src["key"]) == h:
            # Текст тот же, а название / ссылка поменялись (переименовали заметку) — обновляем подпись.
            _h, old_title, old_ref = meta[src["key"]]
            if old_title != title or old_ref != ref_json:
                idx.update_meta(uid, src["key"], title=title, date=src["date"], ref_json=ref_json)
            skipped += 1
            continue
        if reuse:
            nid = _doc_key_note(src["key"])
            cands = [f"{kind}:{nid}" for kind in _MAIN_KINDS] if _doc_key_norm(src["key"]) != src["key"] else [src["key"]]
            found = idx.find_copy(cands, h, exclude_prefix=DOC_PREFIX)
            if found and idx.copy_source(found[0], found[1], uid, src["key"], content_hash=h, kind=src["kind"],
                                         title=title, date=src["date"], ref_json=ref_json):
                copied += 1
                continue
        chunks = chunk_text(src["text"])
        if not chunks:
            continue
        if chunks_n + len(chunks) > budget_chunks and (chunks_n or budget_chunks <= 0):
            pending += 1
            continue  # доиндексируем в следующий проход
        # заголовок в каждом куске — поиск «по встрече X» находит её куски
        vecs = embed([f"{src['title']}. {c}" for c in chunks])
        idx.upsert_source(uid, src["key"], content_hash=h, kind=src["kind"], title=title,
                          date=src["date"], ref_json=ref_json, chunks=chunks, vecs=vecs)
        chunks_n += len(chunks)
        done += 1
    removed = 0
    for key in set(have) - seen:
        if keep and keep(key):
            continue
        idx.delete_source(uid, key)
        removed += 1
    return {"indexed": done, "copied": copied, "skipped": skipped, "removed": removed, "chunks": chunks_n,
            "pending": pending}


def _search_owners(uid: int) -> tuple[list[str], set[int]]:
    """Разделы для поиска человека: личный, документы, открытые ему сейчас, и (пока не убраны проходом
    index_docs) старые разделы организаций. Плюс сами id открытых документов — для старых копий."""
    readable, org_ids = readable_doc_ids(uid)
    owners = [str(uid)] + [doc_owner_key(n) for n in sorted(readable)] + [f"org:{int(o)}" for o in org_ids]
    return owners, readable


def _visible(hits: list[dict[str, Any]], uid: int, readable: set[int]) -> list[dict[str, Any]]:
    """Права и склейка копий: раздел документа открыт по построению; старая копия документа (личный раздел,
    раздел организации) — только если документ всё ещё открыт и его нет в новом разделе."""
    def legacy(h: dict[str, Any]) -> bool:
        return not str(h.get("owner") or h.get("owner_user_id") or "").startswith(DOC_PREFIX)


    out: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for h in sorted(hits, key=legacy):  # сначала разделы документов — копии склеятся с ними
        nid = _doc_key_note(h["source_key"])
        if nid is not None and legacy(h) and nid not in readable:
            continue
        h["source_key"] = _doc_key_norm(h["source_key"])
        ident = (h["source_key"], h.get("chunk_no", 0))
        if ident in seen:
            continue
        seen.add(ident)
        out.append(h)
    return out


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
    # Личный раздел + разделы документов, открытых человеку прямо сейчас (права — при каждом поиске).
    owners, readable = _search_owners(uid)
    hits = _visible(idx.search_many(owners, qvec, k=60), uid, readable)
    qs = _stems(q)
    for h in hits:
        overlap = len(qs & _stems(h["text"] + " " + h["title"])) / max(1, len(qs))
        h["rank"] = h["score"] + KEYWORD_WEIGHT * overlap
    # Название заметки / листа целиком есть в вопросе («дай ссылку на заметку Вебапп», «для чего нужен
    # продукт Обучат Деск») — этот источник наверх, даже если по смыслу текст далёк от короткого вопроса.
    by_key = {}
    for h in hits:
        by_key.setdefault(h["source_key"], h)
    for fc in _visible(idx.first_chunks_many(owners), uid, readable):
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
_TIMERS: dict[Any, threading.Timer] = {}  # ключ: uid, "doc:<id>", "org:<id>"
_RUNNING: set[Any] = set()
_T_LOCK = threading.Lock()
_STATUS_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_STATUS_TTL = 20.0


def _run_once(key: Any, fn: Callable[[], Any], label: str) -> None:
    """Один прогон на ключ: если такой уже идёт — этот пропускаем (идущий увидит свежий текст или следующий таймер)."""
    with _T_LOCK:
        if key in _RUNNING:
            return
        _RUNNING.add(key)
    try:
        res = fn()
        if res and (res.get("indexed") or res.get("removed") or res.get("copied")):
            print(f"[semantic] on-change {label} {res}")
    except Exception as e:
        print(f"[semantic] on-change {label} err={e!r}")
    finally:
        with _T_LOCK:
            _RUNNING.discard(key)


def _schedule(key: Any, delay: float, fn: Callable[[], None]) -> None:
    """Через delay секунд; повтор с тем же ключом откладывает прежний таймер."""
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    with _T_LOCK:
        old = _TIMERS.get(key)
        if old is not None:
            old.cancel()
        _TIMERS[key] = timer
    timer.start()


def _index_now(uid: int) -> None:
    def run() -> dict[str, int]:
        res = index_user(uid, budget_chunks=2000)
        _STATUS_CACHE.pop(uid, None)
        return res

    _run_once(uid, run, f"uid={uid}")


def _index_doc_now(note_id: int) -> None:
    def run() -> dict[str, int]:
        res = index_doc(note_id)
        _STATUS_CACHE.clear()  # документ общий — счётчики «в памяти» у всех, кому он открыт
        return res

    _run_once(doc_owner_key(note_id), run, f"doc={note_id}")


def doc_changed(note_id: Any, *, delay: float = NOTE_DEBOUNCE_SEC) -> None:
    """Документ (текст, лист, встреча) изменился — переиндексировать его раздел один раз для всех."""
    if not embed_client.enabled():
        return
    try:
        nid = int(note_id)
    except (TypeError, ValueError):
        return
    _schedule(doc_owner_key(nid), delay, lambda: _index_doc_now(nid))


def org_changed(org_id: Any, *, delay: float = NOTE_DEBOUNCE_SEC) -> None:
    """Документ добавлен в базу знаний организации или убран — у её документов меняется вид («документ базы»)."""
    if not embed_client.enabled():
        return
    try:
        from assistant.stores import org_kb

        nids = org_kb.org_doc_note_ids(int(org_id))
    except Exception as e:
        print(f"[semantic] on-change org={org_id} err={e!r}")
        return
    for nid in nids:
        doc_changed(nid, delay=delay)


def note_changed(uid: Any, *, delay: float = NOTE_DEBOUNCE_SEC) -> None:
    """Личный материал (почта, задачи) изменился — переиндексировать раздел пользователя через delay секунд."""
    if not embed_client.enabled():
        return
    try:
        u = int(uid)
    except (TypeError, ValueError):
        return
    _schedule(u, delay, lambda: _index_now(u))


def _doc_status_sources(uid: int) -> list[tuple[str, dict[str, Any]]]:
    """(раздел, источник) документов, открытых человеку."""
    from assistant.stores import notes as notes_store

    try:
        readable, _orgs = readable_doc_ids(uid)
    except Exception as e:
        print(f"[semantic] status docs uid={uid} err={e!r}")
        return []
    out = []
    for nid in sorted(readable, reverse=True):
        note = notes_store.get_note_by_id(nid)
        if note:
            out += [(doc_owner_key(nid), src) for src in doc_sources(note, org_id=_org_of_note(nid))]
    return out


def status(uid: int) -> dict[str, Any]:
    """Сколько материалов уже в памяти: total, indexed, pending (названия первых в очереди)."""
    if not embed_client.enabled():
        return {"enabled": False}
    now = time.time()
    hit = _STATUS_CACHE.get(int(uid))
    if hit and now - hit[0] < _STATUS_TTL:
        return hit[1]
    pairs = [(str(uid), src) for src in sources_for_user(uid)] + _doc_status_sources(uid)
    have = idx.source_hashes_many(list(dict.fromkeys(o for o, _s in pairs)))
    total = indexed = 0
    pending: list[str] = []
    for owner, src in pairs:
        total += 1
        if have.get((owner, src["key"])) == _hash(src["text"]):
            indexed += 1
        elif len(pending) < 5:
            title = str(src["title"] or "")
            if title not in pending:
                pending.append(title)
    out = {"enabled": True, "total": total, "indexed": indexed, "pending": pending}
    _STATUS_CACHE[int(uid)] = (now, out)
    return out
