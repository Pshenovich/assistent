"""B3: бриф перед встречей — история с участниками, прошлая встреча серии, открытые задачи.

Собирает факты из уже существующих источников (person_history, материалы встреч,
предыдущая встреча с тем же названием) и сжимает их моделью в короткий Telegram HTML.
"""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

_MAX_PEOPLE = 6


def briefs_enabled_for(user_id: int) -> bool:
    raw = (os.getenv("MEETING_BRIEFS_ENABLED", "0") or "0").strip().lower()
    if raw in {"0", "false", "no", "off", ""}:
        return False
    allow = [x.strip() for x in (os.getenv("MEETING_BRIEFS_USERS", "") or "").split(",") if x.strip()]
    return not allow or str(int(user_id)) in allow


def brief_minutes_before() -> int:
    try:
        return max(5, int(os.getenv("MEETING_BRIEF_MINUTES", "30") or "30"))
    except ValueError:
        return 30


def _attendees(raw: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for a in raw.get("attendees") or []:
        if not isinstance(a, dict) or a.get("resource") or a.get("self"):
            continue
        email = str(a.get("email") or "").strip().lower()
        name = str(a.get("displayName") or "").strip()
        if email or name:
            out.append({"email": email, "name": name})
    return out


def _start_dt(ev: dict[str, Any]) -> Optional[datetime]:
    st = ev.get("start")
    if isinstance(st, datetime):
        return st
    try:
        return datetime.fromisoformat(str(ev.get("start_iso") or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def _trim_history(h: dict[str, Any]) -> dict[str, Any]:
    """Только «когда виделись»: последние 3 встречи (название и дата).

    Материалы и поручения из чужих встреч сюда не берём — иначе в бриф груминга
    попадает то, что обсуждали с участником один на один. Повестка и обязательства
    берутся только из прошлой встречи этой серии (series).
    """
    meetings = [
        {k: m.get(k) for k in ("title", "start") if m.get(k)}
        for m in (h.get("meetings") or [])[:3]
    ]
    out: dict[str, Any] = {"person": h.get("person")}
    meetings = [m for m in meetings if m]
    if meetings:
        out["meetings"] = meetings
    return out


_CONF_RE = re.compile(
    r"https?://\S*(?:zoom\.us|telemost\.yandex|meet\.google|teams\.microsoft|teams\.live|webex\.com|"
    r"ktalk\.ru|sferum\.ru|jazz\.sber|mts-link|salutejazz|whereby\.com)\S*",
    re.I,
)
_CONF_LINE_RE = re.compile(
    r"(идентификатор конференции|код доступа|meeting id|passcode|password|пароль|join zoom|подключиться к|"
    r"присоединиться к|zoom meeting|видеовстреч|телемост|one tap mobile|dial by your location|"
    r"invited you to|приглашает вас|^\s*[-–—_=*]{3,}\s*$)",
    re.I,
)


def split_description(text: str) -> tuple[str, str]:
    """(описание без служебного текста созвона, первая ссылка для подключения)."""
    link = ""
    m = _CONF_RE.search(text or "")
    if m:
        link = m.group(0).rstrip(").,;»\"'")
    lines = []
    for line in (text or "").splitlines():
        if _CONF_RE.search(line) or _CONF_LINE_RE.search(line):
            continue
        line = re.sub(r"https?://\S+", "", line).strip(" :-–—\t")
        if line:
            lines.append(line)
    return "\n".join(lines).strip(), link


_SERIES_LIMIT = 3


def _speaker_label(who: str) -> bool:
    return bool(re.fullmatch(r"(спикер|speaker)\s*\d*", (who or "").strip(), flags=re.I))


def _note_digest(uid: int, m: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Итоги одной прошлой встречи из её заметки (первой, где они есть)."""
    from assistant.stores import notes as notes_store

    for nid in m["note_ids"]:
        try:
            note = notes_store.get_note(str(int(uid)), int(nid))
        except Exception:
            note = None
        meta = (note or {}).get("meta") if isinstance((note or {}).get("meta"), dict) else {}
        if not meta or not (meta.get("main_topic") or meta.get("tasks") or meta.get("decisions")):
            continue
        live: dict[str, Any] = {}
        if meta.get("tasks"):
            try:  # статус пункта — как в таблице задач встречи (взяли ≠ сделали)
                from assistant.services import meeting_tasks

                live = {x["id"]: x for x in meeting_tasks.rows(note)["items"] if x.get("id")}
            except Exception as e:
                print(f"[meeting_brief] meeting tasks failed note={nid} err={e!r}")
        tasks = []
        for t in meta.get("tasks") or []:
            if not isinstance(t, dict) or not str(t.get("task") or "").strip():
                continue
            who = str(t.get("assignee") or "").strip()
            x = live.get(str(t.get("id") or ""))
            row = {
                "task": str(t["task"]).strip(), "assignee": "" if _speaker_label(who) else who,
                "deadline": str(t.get("deadline") or "").strip(), "completed": bool(t.get("completed")),
            }
            if x:
                row["completed"] = x["code"] in ("done", "not_task", "duplicate", "doubtful")
                row["status"] = x["label"]
                if x.get("who") and x["code"] not in ("open",):
                    row["assignee"] = x["who"]
                if x.get("due") and x["code"] not in ("open",):
                    row["deadline"] = x["due"]
            tasks.append(row)
        decisions = [str(d.get("text") if isinstance(d, dict) else d).strip() for d in meta.get("decisions") or []]
        questions = [str(q.get("text") if isinstance(q, dict) else q).strip() for q in meta.get("open_questions") or []]
        return {
            "date": m["start"].date().isoformat(),
            "title": m["title"],
            "note_id": int(nid),
            "topic": str(meta.get("main_topic") or "").strip(),
            "decisions": [d for d in decisions if d][:6],
            "open_questions": [q for q in questions if q][:5],
            "tasks": tasks,
        }
    return None


def _series(uid: int, meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in meetings:
        d = _note_digest(uid, m)
        if d:
            out.append(d)
        if len(out) >= _SERIES_LIMIT * 2:
            break
    return out  # новые первыми


def stuck_items(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Задачи и вопросы, что всплывают на 2+ встречах темы и так и не закрыты."""
    from assistant.lib.summary_normalize import tasks_duplicate

    clusters: list[dict[str, Any]] = []
    for meet in reversed(series):  # от старых к новым
        items = [dict(t, kind="task") for t in meet["tasks"]]
        items += [{"task": q, "assignee": "", "completed": False, "kind": "question"} for q in meet["open_questions"]]
        for it in items:
            cur = next((c for c in clusters if tasks_duplicate(c["last"], it)), None)
            if cur is None:
                clusters.append({"first": meet["date"], "dates": [meet["date"]], "last": it})
                continue
            if meet["date"] not in cur["dates"]:
                cur["dates"].append(meet["date"])
            cur["last"] = it
    out = []
    for c in clusters:
        if len(c["dates"]) < 2 or c["last"].get("completed"):
            continue
        out.append({
            "text": c["last"]["task"], "who": c["last"].get("assignee") or "", "kind": c["last"]["kind"],
            "first_seen": c["first"], "times": len(c["dates"]),
        })
    return out[:6]


_CHAT_DAYS = 14


def _chat_context(chats: list[dict[str, Any]], since: datetime, before: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Что было в связанных чатах с прошлой встречи (не дальше 14 дней) и до неё: дайджесты, иначе хвост сообщений."""
    from assistant.stores import chat_digest
    from assistant.services.semantic_memory import _digest_text

    since_day = since.date().isoformat()
    out = []
    for c in chats:
        cid = int(c["chat_id"])
        # дайджест дня встречи покрывает и то, что было после неё — берём только прошлые дни
        until_day = before.date().isoformat() if before else "9999"
        reps = [r for r in chat_digest.list_reports_for_chats([cid], limit=20)
                if since_day <= r["report_date"] < until_day]
        text = ""
        if reps:
            parts = [f"{r['report_date']}: {_digest_text(r.get('summary') or {})}" for r in sorted(reps, key=lambda r: r["report_date"])]
            text = "\n".join(parts)
        else:
            until = before.astimezone(timezone.utc).isoformat() if before else "9999"
            msgs = [x for x in chat_digest.messages_after(cid, since.astimezone(timezone.utc).isoformat(), limit=400)
                    if str(x.get("text") or "").strip() and not x.get("is_auto") and str(x.get("ts_utc")) < until][-40:]
            text = "\n".join(f"{x.get('display_name') or x.get('username') or '?'}: {str(x['text']).strip()[:300]}" for x in msgs)
        if text.strip():
            out.append({"chat": c.get("title") or "", "since": since_day, "text": text[-2000:]})
    return out


_RELATED_MIN = 0.35
_INVITE_RE = re.compile(r"(приглашение|invitation|updated invitation|отмененное мероприятие|accepted:|принято:)", re.I)


def _notes_from(uid: int, before: datetime) -> set[int]:
    """Заметки этой и более поздних встреч — в бриф к ней не берём (у поиска дата только днём)."""
    from assistant.stores import meeting_links

    out = set()
    try:
        rows = meeting_links._conn().execute(
            "SELECT note_id, start_iso FROM meeting_links WHERE owner_user_id = ? AND start_iso >= ?",
            (str(int(uid)), (before - timedelta(days=1)).date().isoformat()),
        ).fetchall()
    except Exception:
        return out
    for r in rows:
        try:
            st = datetime.fromisoformat(str(r["start_iso"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if st.tzinfo is None:
            st = st.replace(tzinfo=before.tzinfo)
        if st >= before:
            out.add(int(r["note_id"]))
    return out


def _person_words(who: str) -> str:
    """Имя участника для сверки: «Артём Данилин»; из почты — только имя ящика (artem.danilin)."""
    who = (who or "").strip()
    if "@" in who:
        who = re.sub(r"[._\d]+", " ", who.split("@", 1)[0])
    return who
_RELATED_MAX = 4


def related_items(uid: int, meeting: dict[str, Any], series: list[dict[str, Any]], chats: list[dict[str, Any]],
                  before: datetime, *, debug: Optional[list] = None) -> list[dict[str, Any]]:
    """Смысловой поиск по памяти + проверка: код отсеивает чужое, модель подтверждает, код сверяет id."""
    from assistant.nlu import llm
    from assistant.services import semantic_memory
    from assistant.stores import meeting_topics as topics

    title = meeting["title"]
    query = " ".join([title] + [s["topic"] for s in series[:3] if s["topic"]] + meeting["attendees"][:4])
    try:
        hits = semantic_memory.search(int(uid), query, k=12)
    except Exception as e:
        print(f"[meeting_brief] related uid={uid} err={e!r}")
        return []
    have_notes = {s["note_id"] for s in series} | _notes_from(uid, before)
    chat_titles = {f"Чат «{c['chat']}»" for c in chats}
    tkeys = topics.keys(title) | {k for s in series for k in topics.keys(s["title"])}
    people = {w for a in meeting["attendees"] for w in topics.keys(_person_words(a))}
    day = before.date().isoformat()
    cands = []
    for h in hits:
        ref = h.get("ref") or {}
        why = ""
        if ref.get("note_id") in have_notes or h["title"] in chat_titles:
            why = "уже в брифе или эта/более поздняя встреча"
        elif str(h.get("date") or "") > day or (
                str(h.get("date") or "") == day and topics.norm_title(h["title"]) == topics.norm_title(title)):
            why = "позже встречи (или она сама)"
        elif _INVITE_RE.search(h["title"]):
            why = "приглашение в календарь"
        elif h["score"] < _RELATED_MIN:
            why = f"score {h['score']}"
        else:
            words = topics.keys(h["title"] + " " + h["snippet"])
            if not (words & tkeys or words & people):
                why = "нет общего слова темы и участника"
        if debug is not None:
            debug.append({"title": h["title"], "date": h.get("date"), "score": h["score"], "drop": why})
        if not why:
            cands.append({"id": len(cands) + 1, "title": h["title"], "date": h.get("date"), "kind": h.get("kind"),
                          "snippet": h["snippet"][:500]})
    if not cands:
        return []
    from assistant.nlu.prompts import MEETING_BRIEF_RELATED_SYSTEM

    try:
        raw = llm._chat(
            MEETING_BRIEF_RELATED_SYSTEM,
            json.dumps({"meeting": meeting, "series_topics": [s["topic"] for s in series[:3]], "candidates": cands},
                       ensure_ascii=False),
            operation="meeting_brief_related", model=llm._model_chat_digest(), timeout=60, temperature=0,
            max_tokens=600,
        )
        picked = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip()) or "[]")
    except Exception as e:
        print(f"[meeting_brief] related verify uid={uid} err={e!r}")
        return []
    by_id = {c["id"]: c for c in cands}
    out = []
    for p in picked if isinstance(picked, list) else []:
        c = by_id.get(p.get("id") if isinstance(p, dict) else None)
        fact = str((p or {}).get("fact") or "").strip()
        if c and fact and c not in out:
            out.append({"source": c["title"], "date": c["date"], "fact": fact[:300]})
        if len(out) >= _RELATED_MAX:
            break
    if debug is not None:
        debug.append({"llm_kept": [o["source"] for o in out], "llm_candidates": len(cands)})
    return out


def collect_facts(user_id: int, ev: dict[str, Any], *, assume_topics: bool = False,
                  debug: Optional[dict] = None) -> dict[str, Any]:
    from assistant.services import meeting_topics
    from assistant.services.gpt_calendar_tools import GptCalendarContext, _tz, tool_person_history

    raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
    title = str(ev.get("summary") or raw.get("summary") or "").strip()
    start = _start_dt(ev)
    before = start or datetime.now().astimezone()
    if before.tzinfo is None:
        before = before.replace(tzinfo=_tz(int(user_id)))
    local_before = before.astimezone(_tz(int(user_id))).replace(tzinfo=None)  # person_history — местное время
    ctx = GptCalendarContext(uid=int(user_id))
    people: list[dict[str, Any]] = []
    attendees = _attendees(raw)
    for a in attendees[:_MAX_PEOPLE]:
        try:
            res = json.loads(tool_person_history(ctx, {
                "person": a["email"] or a["name"],
                "start_date": (before - timedelta(days=90)).date().isoformat(),
                "end_date": (before - timedelta(minutes=1)).date().isoformat(),
            }))
        except Exception as e:
            print(f"[meeting_brief] history uid={user_id} who={a['email'] or a['name']} err={e!r}")
            continue
        if res.get("error"):
            continue
        # Окно истории — до конца дня: эту встречу и более поздние сегодня не считаем «виделись».
        res["meetings"] = [m for m in res.get("meetings") or [] if not _not_before(m.get("start"), local_before)]
        h = _trim_history(res)
        if len(h) > 1:
            people.append(h)
    try:
        from assistant.lib.telegram_html import html_to_plain

        plain = html_to_plain(str(raw.get("description") or ""))
    except Exception:
        plain = str(raw.get("description") or "")
    description, join_url = split_description(plain)
    topic = meeting_topics.resolve(int(user_id), title, before=before, assume=assume_topics)
    series = _series(int(user_id), topic["meetings"])
    stuck = stuck_items(series)
    last = datetime.fromisoformat(series[0]["date"]).replace(tzinfo=before.tzinfo) if series else None
    since = max(last or before - timedelta(days=_CHAT_DAYS), before - timedelta(days=_CHAT_DAYS))
    chats = _chat_context(topic["chats"], since, before)
    meeting = {
        "title": title,
        "start": start.isoformat() if start else "",
        "description": description[:1500],
        "attendees": [a["name"] or a["email"] for a in attendees],
    }
    rel_debug: list = []
    related = related_items(int(user_id), meeting, series, chats, before, debug=rel_debug) if (
        series or chats or len(description) >= _DESC_MIN) else []
    if debug is not None:
        debug.update({"topic": topic, "related": rel_debug})
    return {
        "meeting": meeting,
        "join_url": join_url,
        "people": people,
        "series": [
            {"date": s["date"], "title": s["title"], "topic": s["topic"], "decisions": s["decisions"],
             "open_tasks": [{k: t[k] for k in ("assignee", "task", "deadline", "status") if t.get(k)} for t in s["tasks"]
                            if not t["completed"]][:10],
             "open_questions": s["open_questions"]}
            for s in series[:_SERIES_LIMIT]
        ],
        "stuck": stuck,
        "chats": chats,
        "related": related,
    }


def _not_before(when: Any, local_before: datetime) -> bool:
    try:
        d = datetime.fromisoformat(str(when or ""))
    except ValueError:
        return False
    return d.replace(tzinfo=None) >= local_before


_DESC_MIN = 40


def has_substance(facts: dict[str, Any]) -> bool:
    """Только «кто и когда виделись» — не бриф. Нужны итоги темы, чат, найденное или повестка."""
    return bool(
        facts.get("series") or facts.get("chats") or facts.get("related")
        or len((facts.get("meeting") or {}).get("description") or "") >= _DESC_MIN
    )


def compose_brief(facts: dict[str, Any]) -> str:
    from assistant.nlu import llm
    from assistant.nlu.prompts import MEETING_BRIEF_SYSTEM

    payload = {k: v for k, v in facts.items() if k != "join_url"}  # ссылку на созвон добавляет код
    payload.setdefault("now", datetime.now().isoformat(timespec="minutes"))
    text = llm._chat(
        MEETING_BRIEF_SYSTEM,
        json.dumps(payload, ensure_ascii=False, default=str),
        operation="meeting_brief",
        model=llm._model_chat_digest(),
        timeout=120,
        temperature=0.2,
        max_tokens=1500,
    )
    return sanitize_brief_html((text or "").strip())


_ALLOWED_TAGS = ("b", "i", "a")


def sanitize_brief_html(text: str) -> str:
    """Telegram HTML строгий: оставляем b/i/a, остальное экранируем."""
    import re

    t = re.sub(r"^```(?:html)?\s*|\s*```$", "", text.strip())
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"</?(?:ul|ol|p|div|h\d)[^>]*>", "\n", t, flags=re.I)
    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.I).replace("</li>", "\n")
    parts = re.split(r"(</?(?:b|i|a)(?:\s+href=\"[^\"]*\")?>)", t, flags=re.I)
    out = []
    for p in parts:
        if re.fullmatch(r"</?(?:b|i|a)(?:\s+href=\"[^\"]*\")?>", p or "", flags=re.I):
            out.append(p)
        else:
            out.append(html.escape(html.unescape(p), quote=False))
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


def build_brief_message(user_id: int, ev: dict[str, Any], *, facts: Optional[dict[str, Any]] = None) -> Optional[str]:
    """Готовый текст брифа или None, если по встрече нечего сказать."""
    facts = facts if facts is not None else collect_facts(int(user_id), ev)
    if not has_substance(facts):
        return None
    body = compose_brief(facts)
    if not body:
        return None
    title = html.escape(str(facts["meeting"]["title"] or "Встреча"))
    head = f"📋 <b>Бриф к встрече «{title}»</b>"
    if facts.get("join_url"):
        head += f'\n<a href="{html.escape(facts["join_url"], quote=True)}">🔗 Подключиться</a>'
    return f"{head}\n\n{body}"


def events_due_for_brief(user_id: int, *, now: Optional[datetime] = None) -> list[dict[str, Any]]:
    from assistant.lib.calendar_event_utils import calendar_event_is_task
    from assistant.services import meeting_reminders as mr
    from assistant.services.calendar import _tz_for
    from assistant.stores import meeting_reminders_sent as sent_store

    tz = _tz_for(user_id)
    now = now or datetime.now(tz)
    mins = brief_minutes_before()
    half = mr.reminder_window_sec() / 2.0
    due = []
    for ev in mr.fetch_upcoming_timed_events(user_id, horizon_min=mins + 12):
        cal_id = str(ev.get("calendar_id") or "")
        ev_id = str(ev.get("event_id") or "")
        start_iso = str(ev.get("start_iso") or "")
        raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
        start = ev.get("start")
        if not ev_id or not cal_id or not isinstance(start, datetime) or calendar_event_is_task(raw):
            continue
        if not _attendees(raw):
            continue  # бриф нужен только встречам с другими людьми
        if sent_store.was_sent(user_id, cal_id, brief_sent_id(ev_id), start_iso):
            continue
        if abs((start - now).total_seconds() - mins * 60) <= half:
            due.append(ev)
    return due


def brief_sent_id(event_id: str) -> str:
    # отдельный ключ в meeting_reminders_sent, чтобы бриф и напоминание не мешали друг другу
    return f"brief:{event_id}"
