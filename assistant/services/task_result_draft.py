"""Серый черновик результата при сдаче задачи.

Лео читает чат задачи, тексты вложений и связанные документы и пишет 1–3 строки
«что получилось». Нет материала — пустая строка: ничего не выдумываем.
Черновик кэшируется на задачу до нового события в ленте.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

MATERIAL_BUDGET = 12000
_DRAFT_KINDS = ("message", "returned", "insisted", "done")

SYSTEM = (
    "Ты помогаешь исполнителю сдать задачу. По материалам ниже напиши 1–3 коротких строки "
    "«что получилось»: результат работы от первого лица исполнителя, без вступлений и оценок. "
    "Опирайся только на материалы: если из них не видно, что сделано, ответь пустой строкой. "
    "Ничего не выдумывай — ни цифр, ни фактов, ни ссылок. Без Markdown и кавычек."
)

_CACHE: dict[int, tuple[str, str]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 2000


def executor_id(task: dict[str, Any]) -> str:
    """Кто сдаёт задачу: исполнитель, а у своей задачи (или «не из команды») — постановщик."""
    return str(task.get("assignee_user_id") or "").strip() or str(task.get("owner_user_id") or "").strip()


def events_marker(events: list[dict[str, Any]]) -> str:
    """Отпечаток ленты: новое событие — новый черновик."""
    if not events:
        return "0"
    last = events[-1]
    return f"{len(events)}|{last.get('at')}|{last.get('kind')}"


def chat_text(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for e in events:
        if e.get("kind") not in _DRAFT_KINDS:
            continue
        text = str(e.get("text") or "").strip()
        meta = e.get("meta") or {}
        extra = [f"📎 {f.get('name')}" for f in (meta.get("files") or []) if isinstance(f, dict) and f.get("name")]
        extra += [f"→ «{r.get('title')}»" for r in (meta.get("refs") or []) if isinstance(r, dict) and r.get("title")]
        if not text and not extra:
            continue
        who = str(e.get("actor_name") or "").strip() or "участник"
        label = {"returned": " (вернул на доработку)", "insisted": " (настоял)", "done": " (сдал раньше)"}.get(
            str(e.get("kind")), ""
        )
        lines.append(f"{who}{label}: " + " ".join([text] + extra).strip())
    return "\n".join(lines)


def event_file_ids(events: list[dict[str, Any]]) -> list[int]:
    out: list[int] = []
    for e in events:
        for f in ((e.get("meta") or {}).get("files") or []):
            try:
                fid = int((f or {}).get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if fid not in out:
                out.append(fid)
    return out[:8]


def event_note_ids(events: list[dict[str, Any]]) -> list[str]:
    """Документы и транскрипции, на которые сослались в чате задачи."""
    out: list[str] = []
    for e in events:
        for r in ((e.get("meta") or {}).get("refs") or []):
            if not isinstance(r, dict):
                continue
            kind = str(r.get("kind") or "")
            rid = str(r.get("id") or "").strip()
            if not rid:
                continue
            if kind == "note":
                sid = rid
            elif kind == "transcription":
                sid = rid if rid.startswith("journal:") else (f"journal:{rid}" if rid.isdigit() else rid)
            else:
                continue
            if sid not in out:
                out.append(sid)
    return out


def bundle_note_ids(bundles: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for b in bundles or []:
        for it in b.get("items") or []:
            if str(it.get("item_kind") or "") == "note":
                sid = str(it.get("item_id") or "").strip()
                if sid and sid not in out:
                    out.append(sid)
    return out


def build_material(*, task: dict[str, Any], chat: str, files_text: str, docs_text: str) -> str:
    """Материал для черновика или "" — если, кроме названия задачи, ничего нет."""
    parts: list[str] = []
    if chat.strip():
        parts.append("ЧАТ ЗАДАЧИ:\n" + chat.strip())
    if files_text.strip():
        parts.append("ВЛОЖЕНИЯ:\n" + files_text.strip())
    if docs_text.strip():
        parts.append("СВЯЗАННЫЕ ДОКУМЕНТЫ:\n" + docs_text.strip())
    if not parts:
        return ""
    head = f"ЗАДАЧА: {str(task.get('title') or '').strip()}"
    desc = str(task.get("description") or "").strip()
    if desc:
        head += f"\nОПИСАНИЕ: {desc[:1500]}"
    return (head + "\n\n" + "\n\n".join(parts))[:MATERIAL_BUDGET]


def clean_draft(raw: str) -> str:
    lines = [ln.strip().strip("«»\"'").strip() for ln in str(raw or "").strip().splitlines()]
    lines = [ln.lstrip("-•* ").strip() for ln in lines if ln.strip()]
    return "\n".join(lines[:3])[:600].strip()


def cached(task_id: int, marker: str) -> Optional[str]:
    with _CACHE_LOCK:
        hit = _CACHE.get(int(task_id))
    if hit and hit[0] == marker:
        return hit[1]
    return None


def remember(task_id: int, marker: str, text: str) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[int(task_id)] = (marker, text)


def draft_from_material(material: str, *, llm: Optional[Callable[[str, str], str]] = None) -> str:
    if not material.strip():
        return ""
    if llm is None:
        from assistant.nlu.auto_model import cheap_model
        from assistant.nlu.llm import _chat

        def llm(system: str, user: str) -> str:  # noqa: E306
            return _chat(system, user, operation="task_result_draft", model=cheap_model(),
                         timeout=40, temperature=0.2, max_tokens=220)

    return clean_draft(llm(SYSTEM, material))
