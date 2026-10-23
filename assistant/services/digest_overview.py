"""«Дайджест» → «Чаты»: список по чатам (а не по дням) и отчёт чата лентой по дням.

Дела не дублируем: «Ждут вашего ответа» и метки чата — те же пункты, что в «Фокусе» (ключи focus_items),
закрыли здесь — закрыты и там.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

DAYS = 14

LABELS = {
    "chat_promise": ("promise", "вы обещали"),
    "chat_escalation": ("risk", "эскалация"),
    "chat_blocker": ("risk", "блокер"),
    "chat_deadline": ("deadline", "срок"),
}


def _selected_reports(uid: int, *, chat_id: Optional[int] = None) -> list[dict[str, Any]]:
    from assistant.stores import chat_digest as digest_store
    from assistant.stores import user_prefs

    if not user_prefs.digest_enabled(uid):
        return []
    chats = [int(c) for c in user_prefs.digest_chat_ids(uid)]
    if chat_id is not None:
        if int(chat_id) not in chats:
            return []
        chats = [int(chat_id)]
    since = (date.today() - timedelta(days=DAYS)).isoformat()
    return [r for r in digest_store.list_reports_for_chats(chats, limit=400)
            if str(r.get("report_date") or "") >= since and not r.get("is_demo")]


def _open_cards(uid: int, chat_id: Optional[int] = None) -> list[dict[str, Any]]:
    """Карточки «Фокуса» из чатов (обещания, эскалации, сроки), ещё не разобранные."""
    from assistant.services import focus_items, proactive, proactive_ai

    hidden = proactive.dismissed_keys(uid) | focus_items.hidden_keys(uid, datetime.now(timezone.utc))
    out = []
    for c in proactive_ai.chat_cards(uid):
        if c.get("type") == "chat_request" or c["key"] in hidden:
            continue
        if chat_id is not None and int(c["chat"]["id"]) != int(chat_id):
            continue
        out.append(c)
    return out


def _awaiting(uid: int, chat_id: Optional[int] = None) -> list[dict[str, Any]]:
    from assistant.services import awaiting_reply

    try:
        reqs = awaiting_reply.pending_requests(uid)
    except Exception as e:
        print(f"[digest] awaiting uid={uid} err={e!r}")
        return []
    return [q for q in reqs if chat_id is None or int(q["chat_id"]) == int(chat_id)]


def _brief(rep: dict[str, Any]) -> str:
    s = rep.get("summary") or {}
    return str(rep.get("preview") or s.get("brief") or "").strip()


def chat_url(chat_id: int) -> str:
    s = str(int(chat_id))
    return f"https://t.me/c/{s[4:]}" if s.startswith("-100") else ""


def overview(uid: int) -> dict[str, Any]:
    reports = _selected_reports(uid)
    by: dict[int, list[dict[str, Any]]] = {}
    for r in reports:
        by.setdefault(int(r["chat_id"]), []).append(r)
    awaiting: dict[int, int] = {}
    for q in _awaiting(uid):
        awaiting[int(q["chat_id"])] = awaiting.get(int(q["chat_id"]), 0) + 1
    labels: dict[int, list[dict[str, str]]] = {}
    for c in _open_cards(uid):
        kind, text = LABELS.get(str(c.get("type")), ("", ""))
        if kind:
            lst = labels.setdefault(int(c["chat"]["id"]), [])
            if not any(lab["text"] == text for lab in lst):
                lst.append({"kind": kind, "text": text})
    chats = []
    for cid, reps in by.items():
        reps.sort(key=lambda r: str(r.get("report_date") or ""), reverse=True)
        last = reps[0]
        brief = next((_brief(r) for r in reps if _brief(r)), "")
        n_wait = awaiting.get(cid, 0)
        labs = ([{"kind": "wait", "text": f"{n_wait} ждут ответа" if n_wait > 1 else "ждёт ответа"}] if n_wait else []) + labels.get(cid, [])
        chats.append({
            "chat_id": cid, "title": str(last.get("title") or "Чат"), "last_date": str(last.get("report_date") or ""),
            "brief": brief or "Ничего важного", "quiet": not labs and not brief, "labels": labs, "awaiting": n_wait,
            "days": len(reps),
        })
    chats.sort(key=lambda c: c["last_date"], reverse=True)
    chats.sort(key=lambda c: 0 if c["labels"] else 1)
    return {"chats": chats, "with_labels": sum(1 for c in chats if c["labels"])}


def step_key(chat_id: int, day: str, task: str) -> str:
    """Ключ пункта «Следующие шаги» отчёта чата (для «В задачу» → чип): чат + день + текст шага."""
    norm = re.sub(r"\s+", " ", str(task or "")).strip().casefold()
    return f"digest_step:{int(chat_id)}:{day}:{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]}"


def _step_task(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Поставленная по шагу задача — для чипа; удалённую задачу не показываем (снова «В задачу»)."""
    from assistant.stores import calendar_tasks as tasks_store

    if not row or str(row.get("status") or "") not in ("converted", "linked"):
        return None
    link = row.get("link") or {}
    if link.get("task_id"):
        try:
            t = tasks_store._get_task_row(int(link["task_id"]))
        except Exception:
            t = None
        if not t:
            return None
        return {"id": int(t["id"]), "title": str(t.get("title") or link.get("title") or ""), "done": bool(t.get("done"))}
    if link.get("url"):
        return {"url": str(link["url"]), "title": str(link.get("title") or "")}
    return None


def chat(uid: int, chat_id: int) -> dict[str, Any]:
    reports = sorted(_selected_reports(uid, chat_id=chat_id), key=lambda r: str(r.get("report_date") or ""), reverse=True)
    if not reports:
        raise LookupError("Отчётов по этому чату нет")
    awaiting = [{"key": q["key"], "who": q["who"], "text": q["text"], "asked_at": q["asked_at"], "url": q["url"]}
                for q in _awaiting(uid, chat_id)]
    cards = [{"key": c["key"], "type": c["type"], "label": LABELS.get(str(c["type"]), ("", ""))[1], "text": c["title"],
              "due": (c.get("_item") or {}).get("due") or "", "day": c["chat"]["date"]} for c in _open_cards(uid, chat_id)]
    from assistant.services import focus_items

    links = {str(x["item_key"]): x for x in focus_items.rows(uid) if str(x.get("item_key") or "").startswith(f"digest_step:{int(chat_id)}:")}

    def _steps(day: str, raw: Any) -> list[dict[str, Any]]:
        out = []
        for x in raw or []:
            x = x or {}
            task = str(x.get("task") or "")
            key = step_key(chat_id, day, task)
            out.append({"who": str(x.get("assignee") or ""), "task": task, "deadline": str(x.get("deadline") or ""),
                        "key": key, "task_link": _step_task(links.get(key))})
        return out

    days = []
    for r in reports:
        s = r.get("summary") or {}
        days.append({
            "date": str(r.get("report_date") or ""), "message_count": r.get("message_count") or 0,
            "brief": str(s.get("brief") or r.get("preview") or ""),
            "decisions": [d if isinstance(d, str) else str(d.get("text") or d.get("decision") or "") for d in s.get("decisions") or []],
            "next_steps": _steps(str(r.get("report_date") or ""), s.get("next_steps")),
            "deadlines": [{"item": str((x or {}).get("item") or ""), "when": str((x or {}).get("when") or "")} for x in s.get("deadlines") or []],
            "open_questions": [q if isinstance(q, str) else str(q.get("text") or "") for q in s.get("open_questions") or []],
        })
    return {"chat_id": int(chat_id), "title": str(reports[0].get("title") or "Чат"), "url": chat_url(chat_id),
            "awaiting": awaiting, "cards": cards, "days": days}


def _step_snapshot(uid: int, key: str) -> Optional[dict[str, Any]]:
    m = re.fullmatch(r"digest_step:(-?\d+):([\d-]+):[0-9a-f]{12}", key)
    if not m:
        return None
    chat_id, day = int(m.group(1)), m.group(2)
    for r in _selected_reports(uid, chat_id=chat_id):
        if str(r.get("report_date") or "") != day:
            continue
        for x in (r.get("summary") or {}).get("next_steps") or []:
            x = x or {}
            if step_key(chat_id, day, str(x.get("task") or "")) == key:
                title = str(r.get("title") or "Чат")
                return {"key": key, "card_key": f"digest:{chat_id}:{day}", "kind": "chat_step", "text": str(x.get("task") or ""),
                        "who": str(x.get("assignee") or ""), "due": str(x.get("deadline") or ""), "type": "task",
                        "source": {"kind": "chat", "id": chat_id, "label": f"чат «{title}»", "url": chat_url(chat_id)}}
    return None


def step_converted(uid: int, key: str, *, task_id: Any = None, url: str = "", title: str = "", by: str = "") -> dict[str, Any]:
    """«В задачу» из «Следующих шагов»: задачу поставили в окне задачи — запоминаем связь (кнопка → чип).
    Тот же пункт «Фокуса»: похожий пункт там больше не поднимется."""
    from assistant.services import focus_items

    if not focus_items.get(uid, key):
        snap = _step_snapshot(uid, key)
        if snap is None:
            raise LookupError("Пункт не найден — обновите дайджест")
        focus_items.remember(uid, snap)
    if task_id:
        from assistant.stores import calendar_tasks as tasks_store

        t = tasks_store._get_task_row(int(task_id))
        if not t or str(t.get("owner_user_id") or "") != str(int(uid)):
            raise LookupError("Задача не найдена")
        row = focus_items.apply(uid, key, "converted", by=by, task_id=int(task_id))
    elif url:
        row = focus_items.apply(uid, key, "link", by=by, url=url, title=title)
    else:
        raise ValueError("Нужна задача или ссылка на неё")
    return {"key": key, "task_link": _step_task(row)}


def item_op(uid: int, key: str, op: str, *, by: str = "") -> dict[str, Any]:
    """«Ответил» / «Не мне» / «Сделано» для пункта из чата — тот же пункт «Фокуса» (запоминаем и применяем)."""
    from assistant.services import focus_items

    if not focus_items.get(uid, key):
        snap = None
        for q in _awaiting(uid):
            if q["key"] == key:
                snap = {"key": key, "card_key": f"awaiting:{date.today().isoformat()}", "kind": "chat_request",
                        "text": f"«{q['chat_title']}» · {q['who']}: {q['text']}", "who": "", "due": "", "type": "task",
                        "source": {"kind": "chat", "id": q["chat_id"], "label": f"чат «{q['chat_title']}»", "url": q["url"]}}
                break
        if snap is None:
            for c in _open_cards(uid):
                if c["key"] == key:
                    snap = focus_items.item_from_card(c)[0]
                    break
        if snap is None:
            raise LookupError("Пункт не найден — обновите дайджест")
        focus_items.remember(uid, snap)
    if op == "not_me":
        row = focus_items.apply(uid, key, "dismiss", by=by, reason="not_me")
    elif op == "done":
        row = focus_items.apply(uid, key, "done", by=by)
    else:
        raise ValueError("Неизвестная операция")
    return focus_items.public(row)
