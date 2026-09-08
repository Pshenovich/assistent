"""Разбор заметки миниаппа советом PAEI → комментарий CHAIR."""

from __future__ import annotations

import threading
import time
from typing import Any

from assistant.board import store
from assistant.board.meeting import MeetingService
from assistant.board.orchestrator import max_rounds
from assistant.board.share_source import share_body_to_text
from assistant.stores import share_comments

CHAIR_AUTHOR_ID = 0
CHAIR_AUTHOR_NAME = "CHAIR"
CHAIR_AUTHOR_USERNAME = "PAIE"
NOTE_BODY_LIMIT = 12_000
COMMENT_LIMIT = 3900
JOB_STALE_SEC = 12 * 60

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


class NullPublisher:
    async def before_agent(self, meeting: dict[str, Any], agent: str) -> None:
        return None

    async def publish_text(
        self, meeting: dict[str, Any], text: str, *, markup: Any = None
    ) -> int | None:
        return None

    async def update_status(self, meeting: dict[str, Any], text: str) -> None:
        return None

    async def clear_status(self, meeting: dict[str, Any]) -> None:
        return None


def _public_job_error(exc: BaseException) -> str:
    text = str(exc or "").strip() or "Не удалось завершить PAIE"
    head = text[:400].lower()
    if (
        "<html" in head
        or "<!doctype" in head
        or "bad gateway" in head
        or "<head>" in head
    ):
        return "Сервер модели временно недоступен. Запустите PAIE ещё раз."
    return text[:400]


def job_key(user_id: int | str, kind: str, item_id: str | int) -> str:
    return f"{int(user_id)}:{kind}:{item_id}"


def get_job(user_id: int | str, kind: str, item_id: str | int) -> dict[str, Any] | None:
    with _jobs_lock:
        row = _jobs.get(job_key(user_id, kind, item_id))
        return dict(row) if row else None


def _progress_view(raw: dict[str, Any] | None) -> dict[str, Any]:
    p = dict(raw or {})
    phase = str(p.get("phase") or "")
    rnd = max(1, int(p.get("round") or 1))
    maxr = max(1, int(p.get("max_rounds") or max_rounds()))
    speaking = str(p.get("speaking") or "").strip().upper()
    extra = bool(p.get("extra"))
    followup = bool(p.get("followup"))
    label = "CHAIR отвечает…" if followup else "PAIE разбирает заметку…"
    pct = 8
    if phase == "ANALYZING":
        label, pct = (
            ("CHAIR читает уточнение…", 12) if followup else ("Анализ заметки…", 10)
        )
    elif phase == "CHALLENGE":
        label = f"Круг {rnd}/{maxr} · challenge"
        pct = min(80, 22 + int((rnd / maxr) * 50))
        if speaking:
            label += f" · говорит {speaking}"
    elif phase == "SYNTHESIS":
        label, pct = (
            ("CHAIR отвечает…", 92) if followup else ("CHAIR формирует решение…", 92)
        )
    elif phase in {"DISCUSSION", "CREATED"}:
        prefix = "Доп. круг" if extra else "Круг"
        label = f"{prefix} {rnd}/{maxr}"
        pct = min(86, 14 + int((rnd / maxr) * 62))
        if speaking:
            label += f" · говорит {speaking}"
    p["label"] = label
    p["pct"] = max(4, min(96, int(pct)))
    return p


def begin_job(
    user_id: int | str,
    kind: str,
    item_id: str | int,
    *,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    quote: str = "",
    reply_to_id: int | None = None,
) -> tuple[dict[str, Any], bool]:
    key = job_key(user_id, kind, item_id)
    now = time.time()
    reply_text = (reply or "").strip()
    with _jobs_lock:
        current = _jobs.get(key)
        if current and current.get("status") == "running":
            started = float(current.get("started_at") or 0)
            if not started or now - started < JOB_STALE_SEC:
                return dict(current), False
        row = {
            "status": "running",
            "kind": kind,
            "item_id": str(item_id),
            "error": None,
            "comment_id": None,
            "meeting_id": None,
            "started_at": now,
            "reply": reply_text,
            "parent_id": parent_id,
            "quote": (quote or "").strip(),
            "reply_to_id": reply_to_id,
            "use_knowledge": bool(use_knowledge),
            "progress": _progress_view(
                {
                    "phase": "ANALYZING",
                    "round": 1,
                    "max_rounds": max_rounds(),
                    "followup": bool(reply_text),
                }
            ),
        }
        _jobs[key] = row
        return dict(row), True


def _update_job(key: str, **fields: Any) -> None:
    with _jobs_lock:
        row = _jobs.get(key) or {}
        if "progress" in fields and isinstance(fields["progress"], dict):
            payload = dict(fields["progress"])
            if row.get("reply"):
                payload["followup"] = True
            fields["progress"] = _progress_view(payload)
        row.update(fields)
        _jobs[key] = row


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val:
        return [str(val).strip()]
    return []


def format_chair_comment(payload: dict[str, Any], *, heading: str | None = None) -> str:
    lines = [heading or "PAIE · решение CHAIR", ""]
    decision = str(payload.get("decision") or "").strip()
    if decision:
        lines.append(decision)
    why = _as_list(payload.get("why"))
    if why:
        lines.append("")
        lines.append("Почему")
        lines.extend(f"• {x}" for x in why[:5])
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
    act_lines = []
    for i, act in enumerate(actions[:6], 1):
        if isinstance(act, dict):
            text = str(act.get("action") or "").strip()
        else:
            text = str(act).strip()
        if text:
            act_lines.append(f"{i}. {text}")
    if act_lines:
        lines.append("")
        lines.append("Что делаем")
        lines.extend(act_lines)
    kpis = _as_list(payload.get("kpis"))
    if kpis:
        lines.append("")
        lines.append("KPI")
        lines.extend(f"• {x}" for x in kpis[:4])
    risks = _as_list(payload.get("risks"))
    if risks:
        lines.append("")
        lines.append("Риск")
        lines.extend(f"• {x}" for x in risks[:3])
    dont = _as_list(payload.get("do_not_do"))
    if dont:
        lines.append("")
        lines.append("Не делать")
        lines.extend(f"• {x}" for x in dont[:4])
    paei = payload.get("paei") if isinstance(payload.get("paei"), dict) else {}
    takes = []
    for letter in ("P", "A", "E", "I"):
        take = str(paei.get(letter) or "").strip()
        if take:
            takes.append(f"{letter}: {take}")
    if takes:
        lines.append("")
        lines.append("PAEI")
        lines.extend(takes)
    conf = payload.get("confidence")
    if conf not in (None, ""):
        try:
            pct = int(round(float(conf) * 100))
            lines.append("")
            lines.append(f"Confidence: {pct}%")
        except (TypeError, ValueError):
            pass
    text = "\n".join(lines).strip()
    if len(text) > COMMENT_LIMIT:
        text = text[: COMMENT_LIMIT - 1] + "…"
    return text


def load_note_for_paei(user_id: int | str, kind: str, item_id: str | int) -> tuple[str, str]:
    uid = str(int(user_id))
    kind = (kind or "").strip()
    if kind == "local":
        from assistant.stores import notes as notes_store

        note = notes_store.get_note(uid, int(item_id))
        if not note:
            raise ValueError("Заметка не найдена")
        title = str(note.get("title") or "").strip() or "Заметка"
        body = share_body_to_text(str(note.get("body") or ""))
        return title, body
    if kind == "journal":
        from assistant.lib.usage_store import get_user_usage_event, journal_text_from_raw

        row = get_user_usage_event(uid, int(item_id))
        if not row:
            raise ValueError("Запись не найдена")
        title = (
            str(row.get("main_topic") or "").strip()
            or str(row.get("meeting_topic") or "").strip()
            or "Запись журнала"
        )
        raw = row.get("raw_usage_json")
        body = journal_text_from_raw(raw if isinstance(raw, str) else None)
        return title, share_body_to_text(body)
    raise ValueError("Некорректный тип записи")


def _clip_prompt(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def _thread_prompt(comments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in share_comments.paie_thread(comments):
        who = "CHAIR" if share_comments.is_paie_comment(row) else (
            str(row.get("author_name") or "").strip() or "Автор"
        )
        body = _clip_prompt(str(row.get("body") or ""), 1800)
        if body:
            parts.append(f"{who}:\n{body}")
    return _clip_prompt("\n\n".join(parts), 8000)


def _comment_by_id(
    comments: list[dict[str, Any]], comment_id: int | None
) -> dict[str, Any] | None:
    if comment_id in (None, "", 0, "0"):
        return None
    try:
        cid = int(comment_id)
    except (TypeError, ValueError):
        return None
    if cid <= 0:
        return None
    for row in comments:
        try:
            if int(row.get("id") or 0) == cid:
                return row
        except (TypeError, ValueError):
            continue
    return None


def _reply_target_prompt(
    comments: list[dict[str, Any]],
    *,
    reply_to_id: int | None = None,
    quote: str = "",
) -> str:
    target = _comment_by_id(comments, reply_to_id)
    if target:
        who = "CHAIR" if share_comments.is_paie_comment(target) else (
            str(target.get("author_name") or "").strip() or "Автор"
        )
        body = _clip_prompt(str(target.get("body") or ""), 1800)
        if body:
            return f"Автор отвечает именно на это сообщение ({who}):\n{body}\n\n"
    quote_text = _clip_prompt((quote or "").strip(), 500)
    if quote_text:
        return f"Автор отвечает на этот фрагмент:\n«{quote_text}»\n\n"
    return ""


async def run_note_paei(
    *,
    user_id: int,
    kind: str,
    item_id: str | int,
    provider: Any | None = None,
    on_progress: Any | None = None,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    quote: str = "",
    reply_to_id: int | None = None,
) -> dict[str, Any]:
    store.init_db()
    title, body = load_note_for_paei(user_id, kind, item_id)
    excerpt = _clip_prompt((body or "").strip() or "(пустая заметка)", NOTE_BODY_LIMIT)
    reply_text = (reply or "").strip()
    comments = share_comments.list_comments(user_id, kind, item_id)
    root_id = parent_id or share_comments.paie_thread_root_id(comments)
    if reply_text:
        history = _thread_prompt(comments)
        question = (
            f"Автор заметки «{title}» уточняет предыдущее решение CHAIR.\n\n"
            f"Текст заметки:\n{excerpt}\n\n"
        )
        if history:
            question += f"Переписка по заметке:\n{history}\n\n"
        question += _reply_target_prompt(
            comments, reply_to_id=reply_to_id, quote=quote
        )
        question += f"Новое уточнение автора:\n{reply_text}\n\n"
        question += (
            "Ответь на уточнение как CHAIR. Не пересказывай весь документ, "
            "если достаточно поправки к решению."
        )
        extra = (
            "Источник: заметка миниаппа Leo. Это follow-up к комментарию CHAIR. "
            "Опирайся на текст заметки, прошлый ответ и уточнение автора."
        )
        if use_knowledge:
            extra += " Используй COMPANY CONTEXT (вкладка «База знаний»)."
        else:
            extra += " Не подмешивай вкладку «База знаний»."
        heading = "PAIE · ответ CHAIR"
    else:
        question = (
            f"Разбери заметку «{title}» как управленческий совет PAEI (P • A • E • I). "
            "Сформулируй исполнимое решение CHAIR.\n\n"
            f"{excerpt}"
        )
        extra = "Источник: заметка миниаппа Leo. Опирайся на текст заметки."
        if use_knowledge:
            extra += " COMPANY CONTEXT — вкладка «База знаний», иначе живой каталог компании."
        else:
            extra += " Не подмешивай вкладку «База знаний»."
        heading = "PAIE · решение CHAIR"
        root_id = None
    if not use_knowledge:
        extra = "[knowledge:off]\n" + extra
    svc = MeetingService(
        publisher=NullPublisher(), provider=provider, on_progress=on_progress
    )
    svc.followups = False
    meeting = svc.start_meeting(
        user_id=int(user_id),
        chat_id=int(user_id),
        question=question,
        title=title[:80],
        extra_instruction=extra,
    )
    if callable(on_progress):
        on_progress(
            {
                "phase": "ANALYZING",
                "round": 1,
                "max_rounds": max_rounds(),
                "meeting_id": meeting["id"],
                "followup": bool(reply_text),
            }
        )
    saved = await svc.run(str(meeting["id"]))
    if not saved:
        row = store.get_meeting(str(meeting["id"])) or {}
        raise RuntimeError(str(row.get("error_message") or "Совещание не дало решения"))
    text = format_chair_comment(saved, heading=heading)
    comment = share_comments.add_comment(
        user_id,
        kind,
        item_id,
        author_user_id=CHAIR_AUTHOR_ID,
        author_name=CHAIR_AUTHOR_NAME,
        author_username=CHAIR_AUTHOR_USERNAME,
        body=text,
        quote="",
        parent_id=root_id,
    )
    return {
        "meeting_id": str(meeting["id"]),
        "decision_id": saved.get("id"),
        "comment": comment,
    }


async def run_note_paei_job(
    user_id: int,
    kind: str,
    item_id: str | int,
    *,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    quote: str = "",
    reply_to_id: int | None = None,
) -> None:
    key = job_key(user_id, kind, item_id)
    job = get_job(user_id, kind, item_id) or {}
    reply_text = (reply or job.get("reply") or "").strip()
    thread_parent = parent_id if parent_id is not None else job.get("parent_id")
    quote_text = (quote or job.get("quote") or "").strip()
    target_id = reply_to_id if reply_to_id is not None else job.get("reply_to_id")
    if "use_knowledge" in job:
        use_knowledge = bool(job.get("use_knowledge"))
    try:
        thread_parent = (
            int(thread_parent) if thread_parent not in (None, "", 0, "0") else None
        )
    except (TypeError, ValueError):
        thread_parent = None
    try:
        target_id = int(target_id) if target_id not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        target_id = None

    def on_progress(payload: dict[str, Any]) -> None:
        fields: dict[str, Any] = {"progress": payload}
        mid = payload.get("meeting_id")
        if mid:
            fields["meeting_id"] = mid
        _update_job(key, **fields)

    try:
        result = await run_note_paei(
            user_id=user_id,
            kind=kind,
            item_id=item_id,
            on_progress=on_progress,
            reply=reply_text,
            parent_id=thread_parent,
            use_knowledge=use_knowledge,
            quote=quote_text,
            reply_to_id=target_id,
        )
        comment = result.get("comment") or {}
        _update_job(
            key,
            status="done",
            meeting_id=result.get("meeting_id"),
            comment_id=comment.get("id"),
            error=None,
        )
    except Exception as e:
        print(f"[board.paei] note_fail user={user_id} kind={kind} item={item_id} err={e!r}")
        _update_job(key, status="error", error=_public_job_error(e))
