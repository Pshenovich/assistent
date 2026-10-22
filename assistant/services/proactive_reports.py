"""«Фокус», этап 3: «План на день» и «Итоги недели» — заметками в проекте «От ассистента».

Цифры (встречи, задачи, решения) считаются точно; ИИ пишет связный текст только по переданным
фактам. Одна заметка на день/неделю: повторная генерация обновляет её, а не плодит новые.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

PROJECT = "От ассистента"
_RU_MON = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
_ALLOWED = re.compile(r"</?(h3|h4|p|ul|ol|li|b|strong|i|em|br)\s*/?>", re.I)

WEEK_SYSTEM = """Ты — помощник руководителя. По фактам за неделю (встречи, задачи, решения из транскрипций, договорённости) напиши итоги недели.
Только по переданным фактам, ничего не выдумывай. Кратко, по-деловому, на русском.
Формат — только HTML-теги <h3>, <ul>, <li>, <b>, <p>. Разделы по порядку (пустые пропускай):
<h3>Главное</h3> 3–5 пунктов; <h3>Решения</h3>; <h3>Обязательства</h3> (что закрыто за неделю; что сорвано — отдельно «за мной» и «за другими» с именами; что буксует на встречах); <h3>Висит</h3> (кто, что, срок); <h3>Время</h3> (сколько ушло на встречи, если есть meeting_hours); <h3>По людям</h3> (кратко по ключевым людям); <h3>На следующую неделю</h3> (что сделать первым)."""

DAY_SYSTEM = """Ты — помощник руководителя. По фактам на сегодня (встречи с временем, задачи, просрочки, подсказки) составь план дня.
Только по переданным фактам, ничего не выдумывай. Кратко, на русском.
Формат — только HTML-теги <h3>, <ul>, <li>, <b>, <p>. Разделы: <h3>Сначала</h3> (1–3 главных дела), <h3>Расписание</h3> (встречи по времени и что сделать в окнах между ними), <h3>Не забыть</h3>."""


def _sanitize(raw: str) -> str:
    s = re.sub(r"```(?:html)?", "", str(raw or ""), flags=re.I).strip()
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=re.I | re.S)
    # Оставляем только разрешённые теги, остальное — текстом.
    parts = re.split(r"(<[^>]+>)", s)
    out = []
    for p in parts:
        if p.startswith("<"):
            m = re.match(r"<\s*(/?)\s*([a-zA-Z0-9]+)", p)
            tag = (m.group(2).lower() if m else "")
            if tag in ("h3", "h4", "p", "ul", "ol", "li", "b", "strong", "i", "em", "br"):
                out.append("<" + m.group(1) + tag + ">")  # без атрибутов
        else:
            out.append(p)
    return "".join(out).strip()


def assistant_project(uid: int) -> Optional[dict[str, Any]]:
    from assistant.stores import tags as tags_store

    for t in tags_store.list_tags(uid):
        if str(t.get("name") or "").strip().lower() == PROJECT.lower():
            return t
    try:
        return tags_store.create_tag(uid, PROJECT)
    except Exception as e:
        print(f"[reports] project failed uid={uid} err={e!r}")
        return None


def _upsert_note(uid: int, key: str, title: str, body: str) -> dict[str, Any]:
    """Одна заметка на ключ (report:day:YYYY-MM-DD / report:week:YYYY-MM-DD)."""
    from assistant.services import proactive_ai
    from assistant.stores import notes as notes_store
    from assistant.stores import tags as tags_store

    with proactive_ai._LOCK:
        row = proactive_ai._conn().execute(
            "SELECT items_json FROM proactive_insights WHERE owner_user_id = ? AND source_key = ?", (str(uid), key)
        ).fetchone()
    note = None
    if row:
        try:
            nid = int(json.loads(row["items_json"] or "{}").get("note_id") or 0)
        except (ValueError, AttributeError):
            nid = 0
        if nid and notes_store.get_note(uid, nid):
            note = notes_store.update_note(uid, nid, title=title, body=body, allow_empty_body=True, actor_user_id=uid)
    if not note:
        note = notes_store.create_note(uid, title, body)
        proj = assistant_project(uid)
        if proj:
            try:
                tags_store.set_item_tags(uid, "local", note["id"], [int(proj["id"])])
            except Exception as e:
                print(f"[reports] tag failed uid={uid} err={e!r}")
    with proactive_ai._LOCK:
        conn = proactive_ai._conn()
        conn.execute(
            """
            INSERT INTO proactive_insights (owner_user_id, source_key, content_hash, analyzed_at, title, items_json)
            VALUES (?, ?, '', ?, ?, ?)
            ON CONFLICT(owner_user_id, source_key) DO UPDATE SET analyzed_at = excluded.analyzed_at, title = excluded.title, items_json = excluded.items_json
            """,
            (str(uid), key, datetime.now(timezone.utc).isoformat(), title, json.dumps({"note_id": int(note["id"])})),
        )
        conn.commit()
    return note


def report_note_id(uid: int, key: str) -> int:
    from assistant.services import proactive_ai

    with proactive_ai._LOCK:
        row = proactive_ai._conn().execute(
            "SELECT items_json FROM proactive_insights WHERE owner_user_id = ? AND source_key = ?", (str(uid), key)
        ).fetchone()
    if not row:
        return 0
    try:
        return int(json.loads(row["items_json"] or "{}").get("note_id") or 0)
    except (ValueError, AttributeError):
        return 0


def _llm(system: str, facts: dict[str, Any], operation: str) -> str:
    from assistant.nlu import llm

    return llm._chat(system, json.dumps(facts, ensure_ascii=False), operation=operation, model=llm._model_chat_digest(), timeout=120, temperature=0.2, max_tokens=1800)


def _events(uid: int, t0: datetime, t1: datetime, events: Optional[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    if events is not None:
        return events
    from assistant.services import proactive
    from assistant.services.gpt_calendar_tools import has_google_calendar

    if not has_google_calendar(uid):
        return []
    try:
        return proactive.events_for_user(uid, t0, t1)
    except Exception as e:
        print(f"[reports] events failed uid={uid} err={e!r}")
        return []


def _meetings(evs: list[dict[str, Any]], tz: Any) -> list[dict[str, Any]]:
    from assistant.services import proactive

    out = []
    for e in evs:
        if proactive._is_all_day(e) or proactive._declined(e) or e.get("entry_type") == "task":
            continue
        st = proactive._parse(e.get("start"))
        out.append({
            "when": st.astimezone(tz).strftime("%a %d.%m %H:%M") if st else "",
            "title": e.get("summary") or "",
            "with": proactive._others(e)[:6],
        })
    return out


def _commitment_facts(uid: int, monday: datetime, now: datetime) -> dict[str, list[dict[str, str]]]:
    """B9: срез реестра обязательств за неделю (B1)."""
    from assistant.services import focus_items
    from assistant.services.risk_rules import STALLED_MEETINGS, _meeting_sources

    out: dict[str, list[dict[str, str]]] = {"closed_this_week": [], "overdue_mine": [], "overdue_theirs": [], "stalled": []}
    try:
        rows = focus_items.commitments(uid, status="", now=now.astimezone(timezone.utc), limit=1000)
    except Exception as e:
        print(f"[reports] commitments failed uid={uid} err={e!r}")
        return out
    since = monday.astimezone(timezone.utc).isoformat()
    for c in rows:
        row = {"text": c["text"], "who": c["who"] or ("я" if c["direction"] == "mine" else ""), "due": c["due_iso"] or c["deadline"]}
        if c["status"] == "closed" and c.get("updated_at", "") >= since and c.get("closed_as") in ("completed", "linked", "converted"):
            out["closed_this_week"].append(row)
        elif c["status"] == "overdue":
            out["overdue_mine" if c["direction"] == "mine" else "overdue_theirs"].append(row)
        if c["status"] in ("open", "overdue") and _meeting_sources(c) >= STALLED_MEETINGS:
            out["stalled"].append(row)
    return {k: v[:25] for k, v in out.items()}


def _meeting_hours(evs: list[dict[str, Any]]) -> float:
    from assistant.services import proactive

    total = 0.0
    for e in evs:
        if proactive._is_all_day(e) or proactive._declined(e) or e.get("entry_type") == "task":
            continue
        st, en = proactive._parse(e.get("start")), proactive._parse(e.get("end"))
        if st and en and en > st:
            total += min((en - st).total_seconds(), 12 * 3600) / 3600
    return round(total, 1)


def weekly(uid: int, *, now: Optional[datetime] = None, events: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.services import proactive_ai
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks
    from assistant.stores import notes as notes_store

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    tz = _tz_for(uid)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    end = monday + timedelta(days=7)
    meets = _meetings(_events(uid, monday, end, events), tz)
    tasks = calendar_tasks.list_tasks_in_window(uid, monday - timedelta(days=21), end)
    done = [t for t in tasks if t.get("done") and str(t.get("updated_at") or "") >= monday.astimezone(timezone.utc).isoformat()]
    hanging = [t for t in tasks if not t.get("done") and str(t.get("end_at") or t.get("start_at") or "") < now.astimezone(timezone.utc).isoformat()]
    decisions, mats = [], []
    for n in notes_store.list_transcription_notes(uid, limit=40):
        if str(n.get("created_at") or "") < monday.astimezone(timezone.utc).isoformat():
            continue
        meta = n.get("meta") if isinstance(n.get("meta"), dict) else {}
        for d in meta.get("decisions") or []:
            decisions.append({"decision": str(d)[:200], "from": n.get("title")})
        mats.append({"title": n.get("title"), "topic": meta.get("main_topic") or "", "tasks": meta.get("tasks") or []})
    agreements = []
    for c in proactive_ai.insight_cards(uid, now=now.astimezone(timezone.utc), task_titles=[]):
        if c.get("type") in ("agreement", "open_question"):
            agreements.append({"type": c["type"], "text": c["title"], "source": c["source"]})
    commitments = _commitment_facts(uid, monday, now)
    hours = _meeting_hours(_events(uid, monday, end, events))
    facts = {
        "week": f"{monday.date().isoformat()}..{(end - timedelta(days=1)).date().isoformat()}",
        "commitments": commitments,
        "meeting_hours": hours,
        "meetings": meets[:60],
        "tasks_done": [{"title": t.get("title"), "who": t.get("assignee_name") or "я"} for t in done][:40],
        "tasks_hanging": [{"title": t.get("title"), "who": t.get("assignee_name") or "я", "due": str(t.get("end_at") or "")[:10]} for t in hanging][:40],
        "decisions": decisions[:30],
        "meeting_materials": mats[:15],
        "agreements_and_open_questions": agreements[:20],
    }
    body = _sanitize(_llm(WEEK_SYSTEM, facts, "proactive_weekly"))
    late = len(commitments["overdue_mine"]) + len(commitments["overdue_theirs"])
    kpi = (
        f"<p><b>{len(meets)}</b> встреч ({hours:g} ч) · <b>{len(done)}</b> задач закрыто · <b>{len(hanging)}</b> висят · "
        f"<b>{len(decisions)}</b> решений · <b>{len(commitments['closed_this_week'])}</b> обещаний закрыто · "
        f"<b>{late}</b> просрочено</p>"
    )
    last = end - timedelta(days=1)
    title = f"Итоги недели · {monday.day}–{last.day} {_RU_MON[last.month - 1]}"
    note = _upsert_note(uid, f"report:week:{monday.date().isoformat()}", title, kpi + body)
    return note


def day_plan(uid: int, *, now: Optional[datetime] = None, events: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    from assistant.integrations.openrouter_client import set_openrouter_usage_telegram_user
    from assistant.services import proactive
    from assistant.services.calendar import _tz_for
    from assistant.stores import calendar_tasks

    set_openrouter_usage_telegram_user(telegram_user_id=uid)
    tz = _tz_for(uid)
    now_utc = now or datetime.now(timezone.utc)
    local = now_utc.astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    evs = _events(uid, start - timedelta(days=2), end, events)
    today_evs = [e for e in evs if (lambda s: s is not None and start <= s.astimezone(tz) < end)(proactive._parse(e.get("start")))]
    tasks = calendar_tasks.list_tasks_in_window(uid, start - timedelta(days=14), end)
    focus = proactive.build_feed(uid, evs, now=now_utc)
    facts = {
        "now": local.strftime("%Y-%m-%d %H:%M"),
        "meetings_today": sorted(_meetings(today_evs, tz), key=lambda m: m["when"]),
        "tasks_today": [{"title": t.get("title"), "when": str(t.get("start_at") or "")[11:16], "who": t.get("assignee_name") or "я"}
                        for t in tasks if not t.get("done") and str(t.get("start_at") or "") >= start.astimezone(timezone.utc).isoformat()][:20],
        "overdue": [t.get("title") for t in tasks if not t.get("done") and str(t.get("end_at") or "") < now_utc.isoformat()][:15],
        "focus": [{"title": f.get("title"), "source": f.get("source")} for f in focus][:7],
    }
    body = _sanitize(_llm(DAY_SYSTEM, facts, "proactive_day_plan"))
    title = f"План на день · {local.day} {_RU_MON[local.month - 1]}"
    return _upsert_note(uid, f"report:day:{local.date().isoformat()}", title, body)


def weekly_card(uid: int, *, now: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Карточка «📊 Итоги недели готовы» — 3 дня после генерации."""
    from assistant.services.calendar import _tz_for
    from assistant.stores import notes as notes_store

    tz = _tz_for(uid)
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    monday = (local - timedelta(days=local.weekday())).date()
    for m in (monday, monday - timedelta(days=7)):
        nid = report_note_id(uid, f"report:week:{m.isoformat()}")
        if not nid:
            continue
        if local.date() > m + timedelta(days=7) + timedelta(days=2):
            continue
        note = notes_store.get_note(uid, nid)
        if not note:
            continue
        return {
            "key": f"weekly:{m.isoformat()}",
            "type": "weekly",
            "tag": "📊 итоги недели",
            "urgency": 1,
            "title": f"{note.get('title')} — готово",
            "source": "заметка в проекте «От ассистента»",
            "ask": f"Итоги недели: {note.get('title')}",
            "note": {"id": nid, "kind": "note", "title": note.get("title")},
            "actions": [{"label": "Открыть", "act": "open_note", "primary": True}],
        }
    return None
