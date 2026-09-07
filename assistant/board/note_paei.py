"""Разбор заметки миниаппа советом PAEI → комментарий CHAIR."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from assistant.board import llm as board_llm
from assistant.board.context import load_prompt
from assistant.board.share_source import resolve_company_share, share_body_to_text
from assistant.stores import share_comments

CHAIR_AUTHOR_ID = 0
CHAIR_AUTHOR_NAME = "CHAIR"
CHAIR_AUTHOR_USERNAME = "PAIE"
NOTE_BODY_LIMIT = 12_000
COMMENT_LIMIT = 3900
JOB_STALE_SEC = 8 * 60
COMPANY_CTX_LIMIT = 3_500

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}


def job_key(user_id: int | str, kind: str, item_id: str | int) -> str:
    return f"{int(user_id)}:{kind}:{item_id}"


def get_job(user_id: int | str, kind: str, item_id: str | int) -> dict[str, Any] | None:
    with _jobs_lock:
        row = _jobs.get(job_key(user_id, kind, item_id))
        return dict(row) if row else None


def begin_job(user_id: int | str, kind: str, item_id: str | int) -> tuple[dict[str, Any], bool]:
    key = job_key(user_id, kind, item_id)
    now = time.time()
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


def _company_excerpt() -> str:
    try:
        _url, doc, _err = resolve_company_share()
    except Exception:
        return ""
    if not doc:
        return ""
    text = str(doc.get("text") or "").strip()
    if not text:
        return ""
    if len(text) > COMPANY_CTX_LIMIT:
        text = text[: COMPANY_CTX_LIMIT - 1] + "…"
    return text


def _build_user_prompt(title: str, body: str) -> str:
    excerpt = (body or "").strip() or "(пустая заметка)"
    if len(excerpt) > NOTE_BODY_LIMIT:
        excerpt = excerpt[: NOTE_BODY_LIMIT - 1] + "…"
    parts = [f"Заметка «{title}»", "", excerpt]
    company = _company_excerpt()
    if company:
        parts.extend(["", "Контекст компании:", company])
    return "\n".join(parts)


async def run_note_paei(
    *,
    user_id: int,
    kind: str,
    item_id: str | int,
    provider: Any | None = None,
) -> dict[str, Any]:
    title, body = load_note_for_paei(user_id, kind, item_id)
    user_prompt = _build_user_prompt(title, body)
    payload = await asyncio.to_thread(
        board_llm.generate_json,
        system=load_prompt("NOTE_PAEI"),
        user=user_prompt,
        operation="board_note_paei",
        temperature=0.2,
        provider=provider,
    )
    if not isinstance(payload, dict) or not str(payload.get("decision") or "").strip():
        raise RuntimeError("PAIE не вернул решение")
    text = format_chair_comment(payload)
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
    return {"comment": comment, "payload": payload}


async def run_note_paei_job(user_id: int, kind: str, item_id: str | int) -> None:
    key = job_key(user_id, kind, item_id)
    try:
        result = await run_note_paei(user_id=user_id, kind=kind, item_id=item_id)
        comment = result.get("comment") or {}
        _update_job(
            key,
            status="done",
            comment_id=comment.get("id"),
            error=None,
        )
    except Exception as e:
        print(f"[board.paei] note_fail user={user_id} kind={kind} item={item_id} err={e!r}")
        _update_job(key, status="error", error=str(e)[:400])
