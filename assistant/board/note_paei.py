"""Разбор заметки миниаппа советом PAEI → комментарий CHAIR."""

from __future__ import annotations

import threading
from typing import Any

from assistant.board import store
from assistant.board.meeting import MeetingService
from assistant.board.share_source import share_body_to_text
from assistant.stores import share_comments

CHAIR_AUTHOR_ID = 0
CHAIR_AUTHOR_NAME = "CHAIR"
CHAIR_AUTHOR_USERNAME = "PAIE"
NOTE_BODY_LIMIT = 12_000
COMMENT_LIMIT = 3900

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


def job_key(user_id: int | str, kind: str, item_id: str | int) -> str:
    return f"{int(user_id)}:{kind}:{item_id}"


def get_job(user_id: int | str, kind: str, item_id: str | int) -> dict[str, Any] | None:
    with _jobs_lock:
        row = _jobs.get(job_key(user_id, kind, item_id))
        return dict(row) if row else None


def begin_job(user_id: int | str, kind: str, item_id: str | int) -> tuple[dict[str, Any], bool]:
    key = job_key(user_id, kind, item_id)
    with _jobs_lock:
        current = _jobs.get(key)
        if current and current.get("status") == "running":
            return dict(current), False
        row = {
            "status": "running",
            "kind": kind,
            "item_id": str(item_id),
            "error": None,
            "comment_id": None,
            "meeting_id": None,
        }
        _jobs[key] = row
        return dict(row), True


def _update_job(key: str, **fields: Any) -> None:
    with _jobs_lock:
        row = _jobs.get(key) or {}
        row.update(fields)
        _jobs[key] = row


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val:
        return [str(val).strip()]
    return []


def format_chair_comment(payload: dict[str, Any]) -> str:
    lines = ["PAIE · решение CHAIR", ""]
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


async def run_note_paei(
    *,
    user_id: int,
    kind: str,
    item_id: str | int,
    provider: Any | None = None,
) -> dict[str, Any]:
    store.init_db()
    title, body = load_note_for_paei(user_id, kind, item_id)
    excerpt = (body or "").strip() or "(пустая заметка)"
    if len(excerpt) > NOTE_BODY_LIMIT:
        excerpt = excerpt[: NOTE_BODY_LIMIT - 1] + "…"
    question = (
        f"Разбери заметку «{title}» как управленческий совет PAEI (P • A • E • I). "
        "Сформулируй исполнимое решение CHAIR.\n\n"
        f"{excerpt}"
    )
    svc = MeetingService(publisher=NullPublisher(), provider=provider)
    svc.followups = False
    meeting = svc.start_meeting(
        user_id=int(user_id),
        chat_id=int(user_id),
        question=question,
        title=title[:80],
        extra_instruction="Источник: заметка миниаппа Leo. Опирайся на текст заметки.",
    )
    saved = await svc.run(str(meeting["id"]))
    if not saved:
        row = store.get_meeting(str(meeting["id"])) or {}
        raise RuntimeError(str(row.get("error_message") or "Совещание не дало решения"))
    text = format_chair_comment(saved)
    comment = share_comments.add_comment(
        user_id,
        kind,
        item_id,
        author_user_id=CHAIR_AUTHOR_ID,
        author_name=CHAIR_AUTHOR_NAME,
        author_username=CHAIR_AUTHOR_USERNAME,
        body=text,
        quote=title[:500],
    )
    return {
        "meeting_id": str(meeting["id"]),
        "decision_id": saved.get("id"),
        "comment": comment,
    }


async def run_note_paei_job(user_id: int, kind: str, item_id: str | int) -> None:
    key = job_key(user_id, kind, item_id)
    try:
        result = await run_note_paei(user_id=user_id, kind=kind, item_id=item_id)
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
        _update_job(key, status="error", error=str(e)[:400])
