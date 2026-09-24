"""Research по заметке: web-search → JSON-справка в обсуждении."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from assistant.lib.llm_json import strip_json_from_markdown
from assistant.nlu.ask_context import pack_knowledge_brief
from assistant.nlu import llm as nlu_llm
from assistant.stores import share_comments
from assistant.stores.share_comments import (
    MAX_GPT_BODY_LEN,
    RESEARCH_AUTHOR_NAME,
    RESEARCH_AUTHOR_USERNAME,
    RESEARCH_PREFIX,
)

from assistant.board.note_paei import load_note_excerpt_for_agents

RESEARCH_AUTHOR_ID = 0
NOTE_BODY_LIMIT = 8_000
HISTORY_LIMIT = 8_000
JOB_STALE_SEC = 12 * 60
COMMENT_LIMIT = MAX_GPT_BODY_LEN

_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, Any]] = {}
_RESTART_ERROR = "Research прервался из-за перезапуска сервера. Запустите ещё раз."
_PROMPTS = Path(__file__).resolve().parent / "prompts" / "RESEARCH.md"


def jobs_file() -> Path:
    raw = os.getenv("RESEARCH_JOBS_PATH", "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (Path.cwd() / p).resolve()
    from assistant.board import store

    return store.db_path().parent / "research_jobs.json"


def _write_jobs(jobs: dict[str, dict[str, Any]]) -> None:
    try:
        path = jobs_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        print(f"[board.research] persist_fail err={exc!r}")


def _persist_jobs() -> None:
    with _jobs_lock:
        snap = {k: dict(v) for k, v in _jobs.items()}
    _write_jobs(snap)


def recover_jobs_after_restart() -> None:
    path = jobs_file()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[board.research] recover_read_fail err={exc!r}")
        return
    if not isinstance(raw, dict):
        return
    now = time.time()
    loaded: dict[str, dict[str, Any]] = {}
    for key, val in raw.items():
        if not isinstance(val, dict):
            continue
        row = dict(val)
        if str(row.get("status") or "") == "running":
            row["status"] = "error"
            row["error"] = _RESTART_ERROR
        started = float(row.get("started_at") or 0)
        if started and now - started > 36 * 3600:
            continue
        loaded[str(key)] = row
    with _jobs_lock:
        _jobs.clear()
        _jobs.update(loaded)
    _persist_jobs()


def job_key(user_id: int | str, kind: str, item_id: str | int) -> str:
    return f"{int(user_id)}:{kind}:{item_id}"


def get_job(user_id: int | str, kind: str, item_id: str | int) -> dict[str, Any] | None:
    with _jobs_lock:
        row = _jobs.get(job_key(user_id, kind, item_id))
        return dict(row) if row else None


def _progress_view(raw: dict[str, Any] | None) -> dict[str, Any]:
    p = dict(raw or {})
    phase = str(p.get("phase") or "").upper()
    labels = {
        "SEARCH": "Ищу в вебе…",
        "READ": "Разбираю источники…",
        "WRITE": "Пишу справку…",
    }
    label = str(p.get("label") or "").strip() or labels.get(phase, "Research ищет…")
    pct = p.get("pct")
    try:
        pct_n = int(pct) if pct is not None else {"SEARCH": 18, "READ": 52, "WRITE": 78}.get(phase, 12)
    except (TypeError, ValueError):
        pct_n = 12
    return {"phase": phase or "SEARCH", "label": label, "pct": max(4, min(96, pct_n))}


def begin_job(
    user_id: int | str,
    kind: str,
    item_id: str | int,
    *,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    knowledge_note_ids: list[str] | None = None,
    quote: str = "",
    sheet_ids: list[str] | None = None,
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
            "started_at": now,
            "reply": reply_text,
            "parent_id": parent_id,
            "quote": (quote or "").strip(),
            "use_knowledge": bool(use_knowledge)
            if knowledge_note_ids is None
            else bool(knowledge_note_ids),
            "knowledge_note_ids": (
                [str(x).strip() for x in knowledge_note_ids if str(x).strip()]
                if knowledge_note_ids is not None
                else None
            ),
            "sheet_ids": (
                [str(x).strip() for x in sheet_ids if str(x).strip()]
                if sheet_ids is not None
                else None
            ),
            "progress": _progress_view({"phase": "SEARCH", "pct": 14}),
        }
        _jobs[key] = row
        out = dict(row)
    _persist_jobs()
    return out, True


def _update_job(key: str, **fields: Any) -> None:
    with _jobs_lock:
        row = _jobs.get(key) or {}
        if "progress" in fields and isinstance(fields["progress"], dict):
            fields["progress"] = _progress_view(fields["progress"])
        row.update(fields)
        _jobs[key] = row
    _persist_jobs()


def _clip(text: str, limit: int) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1] + "…"


def _as_list(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if val:
        return [str(val).strip()]
    return []


def _safe_url(raw: str) -> str:
    url = str(raw or "").strip()
    if url.startswith("https://") or url.startswith("http://"):
        return url
    return ""


def _clamp_conf(raw: Any) -> float:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, n))


def parse_research_payload(raw: str) -> dict[str, Any]:
    text = strip_json_from_markdown(raw)
    data: Any = None
    try:
        data = json.loads(text)
    except Exception:
        data = None
    if not isinstance(data, dict):
        fallback = _clip(str(raw or "").strip(), 2000)
        return {
            "question": "",
            "findings": [],
            "contradictions": [],
            "unknowns": ["Модель не вернула структурированный отчёт"],
            "so_what": fallback,
            "next_queries": [],
            "ready_for_paie": False,
        }
    findings: list[dict[str, Any]] = []
    raw_findings = data.get("findings")
    if isinstance(raw_findings, list):
        for item in raw_findings[:12]:
            if isinstance(item, str):
                claim = item.strip()
                if claim:
                    findings.append(
                        {
                            "claim": claim,
                            "source_title": "",
                            "source_url": "",
                            "confidence": 0.0,
                        }
                    )
                continue
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue
            findings.append(
                {
                    "claim": claim,
                    "source_title": str(item.get("source_title") or "").strip(),
                    "source_url": _safe_url(str(item.get("source_url") or "")),
                    "confidence": _clamp_conf(item.get("confidence")),
                }
            )
    ready = data.get("ready_for_paie")
    return {
        "question": str(data.get("question") or "").strip(),
        "findings": findings,
        "contradictions": _as_list(data.get("contradictions"))[:8],
        "unknowns": _as_list(data.get("unknowns"))[:8],
        "so_what": str(data.get("so_what") or "").strip(),
        "next_queries": _as_list(data.get("next_queries"))[:6],
        "ready_for_paie": bool(ready) if ready not in (None, "") else False,
    }


def format_research_comment(payload: dict[str, Any], *, heading: str | None = None) -> str:
    lines = [heading or "Research · справка", ""]
    question = str(payload.get("question") or "").strip()
    if question:
        lines.append(f"Запрос: {question}")
        lines.append("")
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    if findings:
        lines.append("Факты")
        for item in findings[:12]:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue
            url = _safe_url(str(item.get("source_url") or ""))
            title = str(item.get("source_title") or "").strip()
            if url:
                label = title or url
                lines.append(f"• {claim} — [{label}]({url})")
            else:
                lines.append(f"• слабый: {claim} (нет ссылки)")
        lines.append("")
    contradictions = _as_list(payload.get("contradictions"))
    if contradictions:
        lines.append("Противоречия")
        lines.extend(f"• {x}" for x in contradictions[:8])
        lines.append("")
    unknowns = _as_list(payload.get("unknowns"))
    if unknowns:
        lines.append("Не нашли")
        lines.extend(f"• {x}" for x in unknowns[:8])
        lines.append("")
    so_what = str(payload.get("so_what") or "").strip()
    if so_what:
        lines.append("Что это значит")
        lines.append(so_what)
        lines.append("")
    nxt = _as_list(payload.get("next_queries"))
    if nxt:
        lines.append("Дальше поискать")
        lines.extend(f"• {x}" for x in nxt[:6])
        lines.append("")
    if payload.get("ready_for_paie"):
        lines.append("Можно отдавать в PAIE")
    text = "\n".join(lines).strip()
    if len(text) > COMMENT_LIMIT:
        text = text[: COMMENT_LIMIT - 1] + "…"
    return text


def research_system_prompt() -> str:
    return _PROMPTS.read_text(encoding="utf-8").strip()


def _research_history(comments: list[dict[str, Any]]) -> str:
    rows = [c for c in comments if share_comments.is_research_turn(c)]
    rows = rows[-8:]
    parts: list[str] = []
    for row in rows:
        if share_comments.is_research_comment(row):
            who = "Research"
        else:
            who = str(row.get("author_name") or "").strip() or "Автор"
        body = _clip(str(row.get("body") or ""), 1800)
        if body:
            parts.append(f"{who}:\n{body}")
    return _clip("\n\n".join(parts), HISTORY_LIMIT)


def _build_user_prompt(
    *,
    title: str,
    excerpt: str,
    reply: str,
    quote: str,
    history: str,
    knowledge_brief: str,
) -> str:
    parts = [
        f"Заметка «{title}».",
        "Текст заметки (контекст, не замена веба):",
        excerpt or "(пусто)",
    ]
    if knowledge_brief:
        parts.append("База знаний компании (фон, не замена веба):")
        parts.append(knowledge_brief)
    if history:
        parts.append("Уже было в Research по этой заметке:")
        parts.append(history)
    q = (reply or "").strip() or "Исследуй тему этой заметки в открытом вебе."
    if quote:
        q = f"Фрагмент: «{_clip(quote, 2000)}»\n\n{q}"
    parts.append("Вопрос менеджера:")
    parts.append(q)
    return "\n\n".join(parts)


def _public_job_error(exc: BaseException) -> str:
    text = str(exc or "").strip() or "Не удалось завершить Research"
    head = text[:400].lower()
    if (
        "<html" in head
        or "<!doctype" in head
        or "bad gateway" in head
        or "<head>" in head
    ):
        return "Сервер модели временно недоступен. Запустите Research ещё раз."
    return text[:400]


def run_note_research(
    *,
    user_id: int,
    kind: str,
    item_id: str | int,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    knowledge_note_ids: list[str] | None = None,
    quote: str = "",
    sheet_ids: list[str] | None = None,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    if sheet_ids is not None:
        sheet_ids = [str(x).strip() for x in sheet_ids if str(x).strip()]
    title, excerpt = load_note_excerpt_for_agents(
        user_id, kind, item_id, sheet_ids=sheet_ids, budget=NOTE_BODY_LIMIT
    )
    reply_text = (reply or "").strip()
    if knowledge_note_ids is not None:
        knowledge_note_ids = [str(x).strip() for x in knowledge_note_ids if str(x).strip()]
        use_knowledge = bool(knowledge_note_ids)
    comments = share_comments.list_comments(user_id, kind, item_id)
    kb = ""
    if use_knowledge:
        query = " ".join(p for p in (title, excerpt[:800], reply_text) if p)
        kb, _ver = pack_knowledge_brief(
            user_id,
            query,
            note_ids=knowledge_note_ids if knowledge_note_ids is not None else None,
        )
    if callable(on_progress):
        on_progress({"phase": "SEARCH", "pct": 20})
    user_prompt = _build_user_prompt(
        title=title,
        excerpt=excerpt,
        reply=reply_text,
        quote=quote,
        history=_research_history(comments),
        knowledge_brief=kb,
    )
    if callable(on_progress):
        on_progress({"phase": "READ", "pct": 48})
    raw = nlu_llm.research_with_web(research_system_prompt(), user_prompt)
    if callable(on_progress):
        on_progress({"phase": "WRITE", "pct": 82})
    payload = parse_research_payload(raw)
    if not payload.get("question"):
        payload["question"] = reply_text or title
    text = format_research_comment(payload)
    root = parent_id
    try:
        root = int(root) if root not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        root = None
    comment = share_comments.add_comment(
        user_id,
        kind,
        item_id,
        author_user_id=RESEARCH_AUTHOR_ID,
        author_name=RESEARCH_AUTHOR_NAME,
        author_username=RESEARCH_AUTHOR_USERNAME,
        body=text,
        quote="",
        prefix=RESEARCH_PREFIX,
        parent_id=root,
    )
    return {"comment": comment, "payload": payload}


async def run_note_research_job(
    user_id: int,
    kind: str,
    item_id: str | int,
    *,
    reply: str = "",
    parent_id: int | None = None,
    use_knowledge: bool = True,
    knowledge_note_ids: list[str] | None = None,
    quote: str = "",
    sheet_ids: list[str] | None = None,
) -> None:
    key = job_key(user_id, kind, item_id)
    job = get_job(user_id, kind, item_id) or {}
    reply_text = (reply or job.get("reply") or "").strip()
    thread_parent = parent_id if parent_id is not None else job.get("parent_id")
    quote_text = (quote or job.get("quote") or "").strip()
    if "sheet_ids" in job and job.get("sheet_ids") is not None:
        raw_sheets = job.get("sheet_ids")
        sheet_ids = (
            [str(x).strip() for x in raw_sheets if str(x).strip()]
            if isinstance(raw_sheets, list)
            else []
        )
    if "knowledge_note_ids" in job and job.get("knowledge_note_ids") is not None:
        raw_ids = job.get("knowledge_note_ids")
        knowledge_note_ids = (
            [str(x).strip() for x in raw_ids if str(x).strip()]
            if isinstance(raw_ids, list)
            else []
        )
        use_knowledge = bool(knowledge_note_ids)
    elif "use_knowledge" in job:
        use_knowledge = bool(job.get("use_knowledge"))

    def on_progress(payload: dict[str, Any]) -> None:
        _update_job(key, progress=payload)

    try:
        result = run_note_research(
            user_id=user_id,
            kind=kind,
            item_id=item_id,
            reply=reply_text,
            parent_id=thread_parent,
            use_knowledge=use_knowledge,
            knowledge_note_ids=knowledge_note_ids,
            quote=quote_text,
            sheet_ids=sheet_ids,
            on_progress=on_progress,
        )
        comment = result.get("comment") or {}
        _update_job(key, status="done", comment_id=comment.get("id"), error=None)
    except Exception as e:
        print(f"[board.research] note_fail user={user_id} kind={kind} item={item_id} err={e!r}")
        _update_job(key, status="error", error=_public_job_error(e))
