"""Факты проекта для Лео (бриф, чат, Фокус, паспорт): всё считает код, без модели.

Только то, что видит человек (через bundle_access / project_access): чужая почта и чаты не попадают.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

RECENT_DAYS = 7
SOON_DAYS = 14
STUCK_OVERDUE = 3  # столько просроченных задач — «буксует»
STUCK_OLD_DAYS = 7  # или хоть одна просрочена дольше недели


def _dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _task_row(uid: str, tid: str) -> Optional[dict[str, Any]]:
    from assistant.services import project_access

    try:
        return project_access.viewable_task(uid, tid)
    except Exception:
        return None


def facts(user_id: Any, project_id: Any, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Сводка проекта глазами человека; None — проекта нет или он не участник."""
    from assistant.services import bundle_access

    uid = str(int(user_id))
    p = bundle_access.project_full(uid, project_id)
    if not p:
        return None
    now = now or datetime.now(timezone.utc)
    items = p["items"]
    tasks_open: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []
    done_week = 0
    for it in items:
        if it["kind"] != "task":
            continue
        t = _task_row(uid, it["id"])
        if not t:
            continue
        due = _dt(t.get("end_at") or t.get("start_at"))
        row = {"id": str(t["id"]), "title": str(t.get("title") or ""), "assignee": str(t.get("assignee_name") or ""),
               "due": due}
        if t.get("done"):
            if (_dt(t.get("done_at") or t.get("updated_at")) or now) >= now - timedelta(days=RECENT_DAYS):
                done_week += 1
            continue
        tasks_open.append(row)
        if due and due < now:
            overdue.append(row)
    overdue.sort(key=lambda r: r["due"])
    summaries = sorted(
        [it for it in items if it["kind"] == "note" and it.get("is_transcription")],
        key=lambda it: it.get("updated_at") or "", reverse=True,
    )
    docs = sorted(
        [it for it in items if it["kind"] == "note" and not it.get("is_transcription")],
        key=lambda it: it.get("updated_at") or "", reverse=True,
    )
    recent_meetings = [s for s in summaries if (_dt(s.get("updated_at")) or now - timedelta(days=99)) >= now - timedelta(days=SOON_DAYS)]
    oldest = overdue[0]["due"] if overdue else None
    stuck = len(overdue) >= STUCK_OVERDUE or bool(oldest and oldest < now - timedelta(days=STUCK_OLD_DAYS))
    return {
        "id": int(p["id"]),
        "title": p["title"],
        "status": p.get("status_label") or "",
        "description": p.get("description") or "",
        "lead": p.get("lead_label") or "",
        "members": len(p.get("members") or []),
        "counts": p.get("counts") or {},
        "tasks_open": len(tasks_open),
        "tasks_done_week": done_week,
        "overdue": overdue[:5],
        "overdue_total": len(overdue),
        "upcoming": upcoming_meetings(uid, items, now=now),
        "recent_meetings": [{"id": s["id"], "title": s["title"]} for s in recent_meetings[:5]],
        "docs": [{"id": d["id"], "title": d["title"]} for d in docs[:5]],
        "hidden": p.get("hidden") or 0,
        "stuck": stuck,
    }


def upcoming_meetings(uid: str, items: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """Ближайшие встречи проекта (отдельные и встречи серий) в следующие две недели."""
    keys = {(it["kind"], it["id"]) for it in items if it["kind"] in ("event", "series")}
    if not keys:
        return []
    from assistant.services import project_sources

    rows = []
    try:
        for r in project_sources._google_rows(int(uid), now, now + timedelta(days=SOON_DAYS)):  # type: ignore[attr-defined]
            rows.append((r["calendar_id"], r["event_id"], r.get("series_id") or "", r.get("title") or "", r["start"]))
        for r in project_sources._orbita_rows(int(uid), now, now + timedelta(days=SOON_DAYS)):  # type: ignore[attr-defined]
            rows.append((r["calendar_id"], r["event_id"], r["series_id"], r["title"], r["start"]))
    except Exception as e:
        print(f"[project_facts] upcoming uid={uid} err={e!r}")
        return []
    out = []
    for cal, eid, sid, title, start in rows:
        if ("event", f"{cal}:{eid}") in keys or (sid and ("series", f"{cal}:{sid}") in keys):
            out.append({"title": title or "Встреча", "start": start})
    out.sort(key=lambda r: r["start"])
    return out[:5]


def _when(d: Optional[datetime], tz: Any) -> str:
    if not d:
        return ""
    try:
        d = d.astimezone(tz)
    except Exception:
        pass
    return d.strftime("%d.%m")


def text(f: dict[str, Any], *, tz: Any = timezone.utc, with_title: bool = True) -> str:
    """Факты одним коротким блоком для промпта (цифры уже посчитаны)."""
    lines = []
    if with_title:
        head = f"Проект «{f['title']}»"
        if f.get("status"):
            head += f" — {f['status'].lower()}"
        if f.get("lead"):
            head += f", руководитель {f['lead']}"
        lines.append(head + ".")
    if f.get("description"):
        lines.append("Описание: " + f["description"][:300])
    lines.append(
        f"Задачи: открыто {f['tasks_open']}, просрочено {f['overdue_total']}, сдано за неделю {f['tasks_done_week']}."
    )
    for t in f.get("overdue") or []:
        who = f" ({t['assignee']})" if t.get("assignee") else ""
        lines.append(f"• просрочено с {_when(t['due'], tz)}: «{t['title']}»{who}")
    if f.get("upcoming"):
        lines.append("Ближайшие встречи: " + "; ".join(f"«{m['title']}» {_when(m['start'], tz)}" for m in f["upcoming"]))
    if f.get("recent_meetings"):
        lines.append("Свежие саммари: " + "; ".join(f"«{m['title']}»" for m in f["recent_meetings"]))
    if f.get("docs"):
        lines.append("Документы: " + "; ".join(f"«{d['title']}»" for d in f["docs"]))
    if f.get("stuck"):
        lines.append("Признак: проект буксует (много или давно просроченных задач).")
    return "\n".join(lines)


def projects_for_item(user_id: Any, kind: str, item_id: Any) -> list[dict[str, Any]]:
    """Проекты элемента, где человек участник: [{id, title}]."""
    from assistant.stores import projects as projects_store

    uid = str(int(user_id))
    out = []
    try:
        pids = projects_store.ids_with_item(kind, item_id)
    except ValueError:
        return []
    for pid in pids:
        p = projects_store.get(pid)
        if p and not p.get("archived_at") and projects_store.role_of(uid, p):
            out.append({"id": int(p["id"]), "title": str(p.get("title") or "")})
    return out


# ---------- паспорт проекта: 2–3 строки от дешёвой модели по готовым фактам ----------

PASSPORT_SYSTEM = (
    "Ты Лео, помощник руководителя. По фактам проекта напиши 2–3 коротких строки по-русски: "
    "что сейчас главное, что буксует и кто кому что должен. Только из фактов, ничего не выдумывай; "
    "цифры бери как есть. Без вступлений, без markdown-заголовков, без эмодзи."
)
_PASSPORT_CACHE: dict[tuple[str, int], tuple[str, str, float]] = {}
PASSPORT_TTL = 12 * 3600


def passport(user_id: Any, project_id: Any) -> Optional[dict[str, Any]]:
    """{text, facts, cached}: текст считается заново, только если факты изменились (или прошло 12 ч).
    Пустой проект — без вызова модели."""
    import hashlib
    import time

    f = facts(user_id, project_id)
    if f is None:
        return None
    tz = _user_tz(user_id)
    body = text(f, tz=tz)
    nums = {k: f[k] for k in ("tasks_open", "overdue_total", "tasks_done_week")}
    nums["stuck"] = f["stuck"]
    nums["upcoming"] = len(f["upcoming"])
    if not f["tasks_open"] and not f["recent_meetings"] and not f["upcoming"] and not f["docs"]:
        return {"text": "", "facts": nums, "cached": False}
    sig = hashlib.sha1(body.encode("utf-8")).hexdigest()
    key = (str(int(user_id)), int(f["id"]))
    hit = _PASSPORT_CACHE.get(key)
    if hit and hit[0] == sig and time.time() - hit[2] < PASSPORT_TTL:
        return {"text": hit[1], "facts": nums, "cached": True}
    try:
        from assistant.nlu.auto_model import cheap_model
        from assistant.nlu.llm import _chat

        out = _chat(PASSPORT_SYSTEM, body, operation="project_passport", model=cheap_model(),
                    timeout=40, temperature=0.2, max_tokens=700)
    except Exception as e:
        print(f"[project_facts] passport pid={project_id} err={e!r}")
        return {"text": "", "facts": nums, "cached": False}
    txt = _whole_sentences(" ".join(str(out or "").split())[:700]) if isinstance(out, str) else ""
    if txt:
        _PASSPORT_CACHE[key] = (sig, txt, time.time())
    return {"text": txt, "facts": nums, "cached": False}


def _whole_sentences(t: str) -> str:
    """Оборванный ответ (лимит токенов) — до последнего целого предложения."""
    t = t.strip()
    if not t or t[-1] in ".!?…»)":
        return t
    cut = max(t.rfind(". "), t.rfind("! "), t.rfind("? "))
    return t[: cut + 1] if cut > 0 else t


def _user_tz(user_id: Any) -> Any:
    try:
        from assistant.services.calendar import _tz_for

        return _tz_for(int(user_id))
    except Exception:
        return timezone.utc
