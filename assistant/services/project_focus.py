"""Карточки Фокуса по проектам (без модели, «LLM предлагает — код проверяет» здесь не нужен: всё решает код).

• «В проекте X буксует» — руководителю и владельцу, раз в неделю (ключ — понедельник), если по фактам
  проекта много или давно просроченных задач (project_facts.stuck).
• «Добавить в «X»?» — пачкой в конце дня (с 16:00): свои документы, саммари и задачи за сегодня, которые
  ни в одном проекте и в названии которых есть название проекта (или его аббревиатура). Кнопка
  «Добавить» кладёт всё сразу; ✕ — «не сюда»: эти элементы этому проекту больше не предлагаются.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

SUGGEST_FROM_HOUR = 16
MAX_SUGGEST_CARDS = 3
MAX_ITEMS_PER_CARD = 6


def _monday(now: datetime, tz: Any) -> str:
    local = now.astimezone(tz)
    return (local - timedelta(days=local.weekday())).date().isoformat()


def stuck_cards(uid: int, now: datetime, tz: Any) -> list[dict[str, Any]]:
    from assistant.services import project_facts
    from assistant.stores import projects as projects_store

    out = []
    for pid in projects_store.ids_for_user(uid):
        p = projects_store.get(pid)
        if not p or p.get("status") != "active":
            continue
        # только тем, кто за проект отвечает: владелец или руководитель
        if projects_store.role_of(uid, p) != "owner" and str(p.get("lead_user_id") or "") != str(int(uid)):
            continue
        f = project_facts.facts(uid, pid, now=now)
        if not f or not f["stuck"]:
            continue
        tops = []
        for t in f["overdue"][:2]:
            who = f" — {t['assignee']}" if t.get("assignee") else ""
            tops.append(f"«{t['title']}»{who}")
        out.append({
            "key": f"project_stuck:{pid}:{_monday(now, tz)}",
            "type": "project_stuck",
            "tag": "📁 проект",
            "urgency": 2,
            "title": f"В проекте «{f['title']}» буксует",
            "source": f"Просрочено задач: {f['overdue_total']}" + (". " + "; ".join(tops) if tops else ""),
            "project": {"id": int(pid), "title": f["title"]},
            "actions": [{"label": "Открыть проект", "act": "open_project", "primary": True,
                         "payload": {"project_id": int(pid)}}],
        })
    return out


_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")


def _keys(title: str) -> tuple[str, set[str]]:
    """(всё название в нижнем регистре, аббревиатуры вида «БГПТ»)."""
    t = " ".join(_WORD.findall(title or ""))
    abbr = {w.lower() for w in _WORD.findall(title or "") if len(w) >= 2 and w.isupper()}
    return t.lower(), abbr


def _matches(project_title: str, item_title: str) -> bool:
    full, abbr = _keys(project_title)
    hay_words = {w.lower() for w in _WORD.findall(item_title or "")}
    hay = " ".join(_WORD.findall(item_title or "")).lower()
    if len(full) >= 4 and f" {full} " in f" {hay} ":
        return True
    return bool(abbr & hay_words)


def _today_items(uid: int, now: datetime, tz: Any) -> list[tuple[str, str, str]]:
    """(вид, id, название) — свои документы/саммари и задачи, созданные сегодня."""
    from assistant.stores import calendar_tasks
    from assistant.stores import notes as notes_store

    start_local = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    start = start_local.astimezone(now.tzinfo).isoformat()
    out: list[tuple[str, str, str]] = []
    try:
        for n in notes_store.list_notes(uid, limit=80, include_transcription=True):
            if str(n.get("created_at") or "") >= start and str(n.get("owner_user_id") or n.get("user_id") or uid) == str(uid):
                out.append(("note", str(n["id"]), str(n.get("title") or "")))
    except Exception as e:
        print(f"[project_focus] notes uid={uid} err={e!r}")
    try:
        with calendar_tasks._LOCK:  # type: ignore[attr-defined]
            rows = calendar_tasks._conn().execute(  # type: ignore[attr-defined]
                "SELECT id, title FROM calendar_tasks WHERE owner_user_id = ? AND created_at >= ?",
                (str(int(uid)), start),
            ).fetchall()
        out += [("task", str(r[0]), str(r[1] or "")) for r in rows]
    except Exception as e:
        print(f"[project_focus] tasks uid={uid} err={e!r}")
    return out


def item_reject_key(project_id: Any, kind: str, item_id: str) -> str:
    return f"project_suggest_item:{int(project_id)}:{kind}:{item_id}"


def suggest_cards(uid: int, now: datetime, tz: Any, dismissed: set[str]) -> list[dict[str, Any]]:
    from assistant.stores import projects as projects_store

    if now.astimezone(tz).hour < SUGGEST_FROM_HOUR:
        return []
    projects = []
    for pid in projects_store.ids_for_user(uid):
        p = projects_store.get(pid)
        if p and p.get("status") == "active" and projects_store.at_least(projects_store.role_of(uid, p), "editor"):
            projects.append(p)
    if not projects:
        return []
    fresh = _today_items(uid, now, tz)
    if not fresh:
        return []
    in_some = projects_store.ids_with_items([(k, i) for k, i, _t in fresh])
    day = now.astimezone(tz).date().isoformat()
    out = []
    for p in projects:
        pid = int(p["id"])
        hits = []
        for kind, iid, title in fresh:
            if in_some.get((kind, iid)) or item_reject_key(pid, kind, iid) in dismissed:
                continue
            if _matches(str(p.get("title") or ""), title):
                hits.append({"kind": kind, "id": iid, "title": title})
        if not hits:
            continue
        hits = hits[:MAX_ITEMS_PER_CARD]
        out.append({
            "key": f"project_suggest:{pid}:{day}",
            "type": "project_suggest",
            "tag": "📁 проект",
            "urgency": 1,
            "title": f"Добавить в «{p['title']}»?",
            "source": "; ".join(f"«{h['title']}»" for h in hits[:3]) + (f" и ещё {len(hits) - 3}" if len(hits) > 3 else ""),
            "project": {"id": pid, "title": str(p.get("title") or "")},
            "actions": [
                {"label": "Добавить" + (f" {len(hits)}" if len(hits) > 1 else ""), "act": "project_add", "primary": True,
                 "payload": {"project_id": pid, "items": hits}},
                {"label": "Не сюда", "act": "project_reject", "payload": {"project_id": pid, "items": hits}},
            ],
        })
        if len(out) >= MAX_SUGGEST_CARDS:
            break
    return out


def reject(uid: int, project_id: Any, items: list[dict[str, Any]]) -> None:
    """«Не сюда»: эти элементы этому проекту больше не предлагать (навсегда)."""
    from assistant.services import proactive

    for it in items or []:
        kind = str(it.get("kind") or "")
        iid = str(it.get("id") or "")
        if kind in ("note", "task") and iid:
            proactive.dismiss(uid, item_reject_key(project_id, kind, iid))
