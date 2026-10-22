"""B4: черновик фоллоу-апа участникам по саммари встречи. Ничего не отправляет сам."""

from __future__ import annotations

import json
from typing import Any, Optional

CALLBACK_PREFIX = "fu:"


def followup_button(note_id: int) -> dict[str, str]:
    return {"text": "✉️ Фоллоу-ап участникам", "callback_data": f"{CALLBACK_PREFIX}{int(note_id)}"}


def _facts(note: dict[str, Any]) -> Optional[dict[str, Any]]:
    meta = note.get("meta") if isinstance(note.get("meta"), dict) else {}
    tasks = []
    for t in meta.get("tasks") or []:
        if not isinstance(t, dict) or not str(t.get("task") or "").strip() or t.get("completed"):
            continue
        who = str(t.get("assignee_name") if "assignee_name" in t else t.get("assignee") or "").strip()
        if who.lower().startswith("спикер") or who.upper().startswith("SPEAKER"):
            who = ""
        tasks.append({"assignee": who, "task": str(t["task"]).strip(), "deadline": str(t.get("deadline") or "").strip()})
    decisions = [str(d.get("text") if isinstance(d, dict) else d).strip() for d in meta.get("decisions") or []]
    decisions = [d for d in decisions if d]
    questions = [str(q).strip() for q in meta.get("open_questions") or [] if str(q).strip()]
    if not (tasks or decisions):
        return None
    return {
        "title": str(note.get("title") or "").strip(),
        "date": str(meta.get("meeting_at") or note.get("created_at") or "")[:10],
        "main_topic": str(meta.get("main_topic") or "").strip(),
        "decisions": decisions[:15],
        "tasks": tasks[:30],
        "open_questions": questions[:10],
    }


def plain_followup(f: dict[str, Any]) -> str:
    """Запасной вариант без модели."""
    topic = f.get("main_topic") or f.get("title") or "встрече"
    lines = [f"Коллеги, спасибо за встречу! Фиксирую договорённости по «{topic}»:"]
    if f["decisions"]:
        lines += ["", "Решили:"] + [f"— {d}" for d in f["decisions"]]
    named: dict[str, list[str]] = {}
    nobody: list[str] = []
    for t in f["tasks"]:
        item = t["task"] + (f" — {t['deadline']}" if t["deadline"] else "")
        (named.setdefault(t["assignee"], []) if t["assignee"] else nobody).append(item)
    if named:
        lines += ["", "Задачи:"]
        for who, items in named.items():
            lines += [f"{who}: {x}" for x in items]
    if nobody:
        lines += ["", "Нужно назначить ответственного:"] + [f"— {x}" for x in nobody]
    if f["open_questions"]:
        lines += ["", "Открытые вопросы:"] + [f"— {q}" for q in f["open_questions"]]
    lines += ["", "Если что-то упустил — поправьте, пожалуйста."]
    return "\n".join(lines)


def build_followup(user_id: int, note_id: int) -> Optional[str]:
    from assistant.nlu import llm
    from assistant.nlu.prompts import MEETING_FOLLOWUP_SYSTEM
    from assistant.stores import notes as notes_store

    note = notes_store.get_accessible_note(str(int(user_id)), int(note_id), with_sharing=False)
    if not note:
        return None
    f = _facts(note)
    if not f:
        return None
    try:
        text = llm._chat(
            MEETING_FOLLOWUP_SYSTEM,
            json.dumps(f, ensure_ascii=False),
            operation="meeting_followup",
            model=llm._model_chat_digest(),
            timeout=90,
            temperature=0.2,
            max_tokens=1500,
        ).strip()
    except Exception as e:
        print(f"[meeting_followup] llm failed note={note_id} err={e!r}")
        text = ""
    return text or plain_followup(f)
