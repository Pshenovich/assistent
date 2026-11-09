"""Показатели работы сотрудника по поручениям — для него самого и для его руководителя.

Мерим выполнение обещаний и качество, а не активность. Правила честности:

* перенос срока постановщиком — новый срок, исполнителю не в упрёк;
* перенос без известного автора (синхронизация, ИИ) — тоже в пользу исполнителя;
* пока висит вопрос исполнителя постановщику, часы срока стоят;
* срок дали меньше чем за рабочий день — в «в срок» не считаем;
* срок пришёлся на отпуск/больничный — не считаем;
* перенёс сам или задал вопрос до срока, но всё равно опоздал — «предупредил», а не «сорвал»;
* меньше ``MIN_SAMPLE`` поручений — процент не показываем, «мало данных».

Модуль — чистые функции над задачами и событиями из ``calendar_tasks``; сборка по человеку — ``member_metrics``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

MIN_SAMPLE = 5
SHORT_NOTICE_WORK_HOURS = 8.0  # меньше рабочего дня между постановкой и сроком
STALE_DAYS = 10
WORK_START_HOUR = 9
WORK_END_HOUR = 19
REACTION_KINDS = ("seen", "taken", "declined", "in_progress", "message", "snoozed", "done")


# ---------- время ----------


def _dt(raw: Any) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def work_hours_between(a: datetime, b: datetime, tz: ZoneInfo | timezone = timezone.utc) -> float:
    """Рабочие часы (пн–пт, 9–19 по часовому поясу человека) между двумя моментами."""
    if b <= a:
        return 0.0
    a, b = a.astimezone(tz), b.astimezone(tz)
    total = 0.0
    day = a.date()
    while day <= b.date():
        if day.weekday() < 5:
            ws = datetime(day.year, day.month, day.day, WORK_START_HOUR, tzinfo=tz)
            we = datetime(day.year, day.month, day.day, WORK_END_HOUR, tzinfo=tz)
            lo, hi = max(a, ws), min(b, we)
            if hi > lo:
                total += (hi - lo).total_seconds() / 3600
        day += timedelta(days=1)
    return total


def _absent(day: date, absences: Iterable[dict[str, str]]) -> bool:
    d = day.isoformat()
    return any(it.get("from", "") <= d <= it.get("to", "") for it in absences or [])


# ---------- одно поручение ----------


def _question_pauses(task: dict[str, Any], events: list[dict[str, Any]]) -> list[tuple[datetime, datetime]]:
    """Отрезки, когда исполнитель ждал ответа постановщика: его сообщение → следующее сообщение/возврат постановщика."""
    owner = str(task.get("owner_user_id") or "")
    assignee = str(task.get("assignee_user_id") or "")
    out: list[tuple[datetime, datetime]] = []
    opened: Optional[datetime] = None
    for e in events:
        at = _dt(e.get("at"))
        if at is None:
            continue
        who = str(e.get("actor_user_id") or "")
        if e.get("kind") == "message" and who == assignee and opened is None:
            opened = at
        elif opened is not None and who == owner and e.get("kind") in ("message", "returned", "insisted", "accepted"):
            out.append((opened, at))
            opened = None
    if opened is not None and task.get("assignee_status") == "question":
        out.append((opened, datetime.now(timezone.utc)))
    return out


def deadline_verdict(
    task: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    now: datetime,
    tz: ZoneInfo | timezone = timezone.utc,
    absences: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    """Вердикт по сроку поручения.

    ``status``: ``on_time`` | ``late`` | ``late_warned`` | ``pending`` (срок не наступил, не сдано)
    | ``excluded``; ``reason`` для исключённых и «спасённых» правилами: ``short_notice``, ``absence``,
    ``owner_moved``, ``question_wait``.
    """
    owner = str(task.get("owner_user_id") or "")
    assignee = str(task.get("assignee_user_id") or "")
    created = _dt(task.get("created_at"))
    log = list(task.get("schedule_log") or [])
    final_end = _dt(task.get("end_at"))
    if final_end is None or created is None:
        return {"status": "excluded", "reason": "no_deadline"}

    # Срок, который назначил постановщик: исходный, затем каждый его перенос. Перенос исполнителем срок не двигает.
    deadline = _dt(log[0].get("from_end")) if log else final_end
    deadline = deadline or final_end
    original = deadline
    owner_moved = False
    warned = False
    for it in log:
        at, to_end = _dt(it.get("at")), _dt(it.get("to_end"))
        by = str(it.get("by_user_id") or "")
        if to_end is None:
            continue
        if by == assignee and by != owner:
            if at is not None and at < deadline:
                warned = True
            continue
        deadline = to_end  # постановщик или неизвестный автор — в пользу исполнителя
        owner_moved = owner_moved or to_end > original
    if log and _dt(log[-1].get("to_end")) != final_end and final_end > deadline:
        deadline, owner_moved = final_end, True  # правка мимо журнала — автор неизвестен

    if work_hours_between(created, deadline, tz) < SHORT_NOTICE_WORK_HOURS and deadline > created:
        return {"status": "excluded", "reason": "short_notice", "deadline": deadline.isoformat()}
    if deadline <= created:
        return {"status": "excluded", "reason": "short_notice", "deadline": deadline.isoformat()}
    if _absent(deadline.astimezone(tz).date(), absences):
        return {"status": "excluded", "reason": "absence", "deadline": deadline.isoformat()}

    done_times = [_dt(e.get("at")) for e in events if e.get("kind") == "done"]
    done_times = [d for d in done_times if d is not None]
    finished = done_times[0] if done_times else (_dt(task.get("done_at")) if task.get("done") else None)

    pauses = _question_pauses(task, events)
    for a, b in pauses:
        if a < deadline:
            warned = True
    paused = sum(
        ((min(b, finished or now) - a).total_seconds() for a, b in pauses if a < deadline and min(b, finished or now) > a),
        0.0,
    )
    extended = deadline + timedelta(seconds=paused)

    if finished is None:
        if extended > now:
            return {"status": "pending", "deadline": deadline.isoformat()}
        finished_at = None
    else:
        finished_at = finished
    late_vs_owner = (finished_at or now) > deadline
    late_vs_original = (finished_at or now) > original
    if (finished_at or now) <= extended:
        reason = ""
        if late_vs_owner:
            reason = "question_wait"
        elif late_vs_original and owner_moved:
            reason = "owner_moved"
        out = {"status": "on_time", "deadline": deadline.isoformat()}
        if reason:
            out["reason"] = reason
        return out
    return {
        "status": "late_warned" if warned else "late",
        "deadline": deadline.isoformat(),
        "open": finished_at is None,
    }


# ---------- сводка по человеку ----------


def _in(at: Optional[datetime], start: datetime, end: datetime) -> bool:
    return at is not None and start <= at < end


def _pct(num: int, den: int) -> Optional[int]:
    return round(100 * num / den) if den >= MIN_SAMPLE else None


def _median_hours(values: list[float]) -> Optional[float]:
    return round(median(values), 1) if values else None


def assignee_metrics(
    user_id: str,
    tasks: list[dict[str, Any]],
    events: dict[int, list[dict[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    tz: ZoneInfo | timezone = timezone.utc,
    absences: Iterable[dict[str, str]] = (),
) -> dict[str, Any]:
    """Надёжность, качество и отзывчивость по поручениям, которые человеку поставили другие, за [start, end)."""
    uid = str(user_id)
    mine = [
        t for t in tasks
        if str(t.get("assignee_user_id") or "") == uid
        and str(t.get("owner_user_id") or "") not in ("", uid)
        and t.get("outcome") != "cancelled"
        and t.get("assignee_triage") != "declined"
    ]
    absences = list(absences or [])
    verdicts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for t in mine:
        v = deadline_verdict(t, events.get(int(t["id"]), []), now=now, tz=tz, absences=absences)
        dl = _dt(v.get("deadline"))
        if dl is not None and _in(dl, start, min(end, now)):
            verdicts.append((t, v))
    counted = [v for _, v in verdicts if v["status"] in ("on_time", "late", "late_warned")]
    on_time = sum(1 for v in counted if v["status"] == "on_time")
    late = [v for v in counted if v["status"] != "on_time"]
    excused: dict[str, int] = {}
    for _, v in verdicts:
        if v.get("reason") in ("short_notice", "absence", "owner_moved", "question_wait"):
            excused[v["reason"]] = excused.get(v["reason"], 0) + 1

    # Качество: что приняли за период — с первого ли раза.
    accepted = returned_before = 0
    returns = declines = 0
    ratings = {"good": 0, "ok": 0, "bad": 0}  # последняя оценка постановщика по принятому за период
    for t in tasks:
        if str(t.get("assignee_user_id") or "") != uid or str(t.get("owner_user_id") or "") == uid:
            continue
        evs = events.get(int(t["id"]), [])
        returns += sum(1 for e in evs if e.get("kind") == "returned" and _in(_dt(e.get("at")), start, end))
        declines += sum(1 for e in evs if e.get("kind") == "declined" and _in(_dt(e.get("at")), start, end))
        acc = _dt(t.get("accepted_at"))
        if t.get("outcome") == "accepted" and _in(acc, start, end):
            accepted += 1
            if any(e.get("kind") == "returned" for e in evs):
                returned_before += 1
            last = [e for e in evs if e.get("kind") == "rated" and e.get("rating") in ratings]
            if last:
                ratings[last[-1]["rating"]] += 1

    # Отзывчивость: от постановки до первой реакции исполнителя, в рабочих часах.
    reactions: list[float] = []
    for t in mine:
        created = _dt(t.get("created_at"))
        if not _in(created, start, end):
            continue
        first = None
        for e in events.get(int(t["id"]), []):
            if e.get("kind") in REACTION_KINDS and str(e.get("actor_user_id") or "") == uid:
                first = _dt(e.get("at"))
                break
        first = first or _dt(t.get("assignee_seen_at"))
        if first is not None and created is not None:
            reactions.append(work_hours_between(created, first, tz))

    stale_edge = now - timedelta(days=STALE_DAYS)
    stale = [
        t for t in mine
        if not t.get("done") and not t.get("owner_archived")
        and (_dt(t.get("created_at")) or now) < stale_edge
    ]
    owners = {str(t.get("owner_user_id")) for t in mine if _in(_dt(t.get("created_at")), start, end)}
    received = sum(1 for t in mine if _in(_dt(t.get("created_at")), start, end))
    return {
        "received": received,
        "from_people": len(owners),
        "deadlines": len(counted),
        "on_time": on_time,
        "on_time_pct": _pct(on_time, len(counted)),
        "late": len(late),
        "late_warned": sum(1 for v in late if v["status"] == "late_warned"),
        "late_open": sum(1 for v in late if v.get("open")),
        "excused": excused,
        "accepted": accepted,
        "first_time": accepted - returned_before,
        "first_time_pct": _pct(accepted - returned_before, accepted),
        "returns": returns,
        "declines": declines,
        "ratings": ratings,
        "rated": sum(ratings.values()),
        "reaction_hours": _median_hours(reactions),
        "stale": len(stale),
        "enough_data": len(counted) >= MIN_SAMPLE,
    }


def task_items(
    user_id: str,
    tasks: list[dict[str, Any]],
    events: dict[int, list[dict[str, Any]]],
    *,
    start: datetime,
    now: datetime,
    tz: ZoneInfo | timezone = timezone.utc,
    absences: Iterable[dict[str, str]] = (),
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Поручения человеку за период — с вердиктом по сроку, для разбора на карточке.
    Сначала то, что горит (просрочено и не сдано), потом свежие."""
    uid = str(user_id)
    out: list[dict[str, Any]] = []
    for t in tasks:
        if str(t.get("assignee_user_id") or "") != uid or str(t.get("owner_user_id") or "") in ("", uid):
            continue
        if t.get("outcome") == "cancelled" or t.get("assignee_triage") == "declined":
            continue
        evs = events.get(int(t["id"]), [])
        v = deadline_verdict(t, evs, now=now, tz=tz, absences=absences)
        dl = _dt(v.get("deadline")) or _dt(t.get("end_at"))
        created = _dt(t.get("created_at"))
        if not ((dl and dl >= start) or (created and created >= start)):
            continue
        late_days = 0
        if v["status"] in ("late", "late_warned") and v.get("open") and dl:
            late_days = max(1, (now - dl).days)
        out.append({
            "id": int(t["id"]),
            "title": str(t.get("title") or ""),
            "owner_user_id": str(t.get("owner_user_id") or ""),
            "deadline": v.get("deadline") or str(t.get("end_at") or ""),
            "status": v["status"],
            "reason": v.get("reason", ""),
            "open": not t.get("done"),
            "late_days": late_days,
            "accepted": t.get("outcome") == "accepted",
            "returns": sum(1 for e in evs if e.get("kind") == "returned"),
        })
    hot = [x for x in out if x["open"] and x["late_days"]]
    rest = sorted((x for x in out if not (x["open"] and x["late_days"])), key=lambda x: x["deadline"], reverse=True)
    return (hot + rest)[:limit]


def setter_metrics(
    user_id: str,
    tasks: list[dict[str, Any]],
    events: dict[int, list[dict[str, Any]]],
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    tz: ZoneInfo | timezone = timezone.utc,
) -> dict[str, Any]:
    """Человек как постановщик: короткие сроки, вопросы к его поручениям, возвраты без пояснений, скорость ответа."""
    uid = str(user_id)
    posted = [
        t for t in tasks
        if str(t.get("owner_user_id") or "") == uid
        and str(t.get("assignee_user_id") or "") not in ("", uid)
        and _in(_dt(t.get("created_at")), start, end)
    ]
    short = asked = 0
    answers: list[float] = []
    for t in posted:
        evs = events.get(int(t["id"]), [])
        created, dl = _dt(t.get("created_at")), _dt(t.get("end_at"))
        if created and dl and work_hours_between(created, dl, tz) < SHORT_NOTICE_WORK_HOURS:
            short += 1
        pauses = _question_pauses(t, evs)
        if pauses:
            asked += 1
        answers += [work_hours_between(a, b, tz) for a, b in pauses if b <= now]
    silent_returns = 0
    waiting = 0
    accepted = rated = 0
    for t in tasks:
        if str(t.get("owner_user_id") or "") != uid:
            continue
        evs = events.get(int(t["id"]), [])
        if (t.get("outcome") == "accepted" and str(t.get("assignee_user_id") or "") not in ("", uid)
                and _in(_dt(t.get("accepted_at")), start, end)):
            accepted += 1
            rated += any(e.get("kind") == "rated" for e in evs)
        silent_returns += sum(
            1 for e in evs
            if e.get("kind") == "returned" and not str(e.get("text") or "").strip() and _in(_dt(e.get("at")), start, end)
        )
        if t.get("assignee_status") == "question" and not t.get("owner_archived"):
            waiting += 1
    n = len(posted)
    return {
        "posted": n,
        "short_notice": short,
        "short_notice_pct": _pct(short, n),
        "with_questions": asked,
        "with_questions_pct": _pct(asked, n),
        "silent_returns": silent_returns,
        "answer_hours": _median_hours(answers),
        "waiting_on_me": waiting,
        "accepted": accepted,
        "rated": rated,
    }


def _trend(cur: Optional[int | float], prev: Optional[int | float]) -> Optional[float]:
    if cur is None or prev is None:
        return None
    return round(cur - prev, 1)


def member_metrics(
    user_id: int | str,
    *,
    days: int = 28,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Показатели человека за последние ``days`` дней и сдвиг к предыдущему такому же периоду."""
    from assistant.stores import calendar_tasks, user_prefs

    uid = str(user_id)
    now = now or datetime.now(timezone.utc)
    try:
        tz: ZoneInfo | timezone = user_prefs.get_user_tz(int(uid))
    except Exception:
        tz = timezone.utc
    absences = user_prefs.get_absences(int(uid)) if uid.isdigit() else []
    tasks = calendar_tasks.list_involved_tasks(uid, limit=5000)
    events = calendar_tasks.events_by_task([int(t["id"]) for t in tasks])
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    cur = assignee_metrics(uid, tasks, events, start=start, end=now, now=now, tz=tz, absences=absences)
    prev = assignee_metrics(uid, tasks, events, start=prev_start, end=start, now=now, tz=tz, absences=absences)
    setter = setter_metrics(uid, tasks, events, start=start, end=now, now=now, tz=tz)
    items = task_items(uid, tasks, events, start=start, now=now, tz=tz, absences=absences)
    period_days = {(start + timedelta(days=i)).date() for i in range(days)}
    absent_days = sum(1 for d in period_days if _absent(d, absences))
    return {
        "user_id": uid,
        "days": days,
        "from": start.isoformat(),
        "to": now.isoformat(),
        "absent_days": absent_days,
        "assignee": cur,
        "trend": {
            "on_time_pct": _trend(cur["on_time_pct"], prev["on_time_pct"]),
            "first_time_pct": _trend(cur["first_time_pct"], prev["first_time_pct"]),
            "reaction_hours": _trend(cur["reaction_hours"], prev["reaction_hours"]),
        },
        "setter": setter,
        "chats": _safe(lambda: chat_metrics(uid, start=start, end=now, now=now, tz=tz), {}),
        "contribution": _safe(lambda: contribution(uid, start=start, end=now), {}),
        "calendar": _safe(lambda: _calendar(uid, start=start, now=now, tz=tz, absences=absences), {}),
        "tasks": items,
        "absences": absences,
    }


def _calendar(uid: str, *, start: datetime, now: datetime, tz, absences) -> dict[str, Any]:
    """Нагрузка календаря — контекст рядом с поручениями (числа, без названий встреч)."""
    from assistant.services import calendar_load

    if not uid.isdigit():
        return {}
    return calendar_load.summary(int(uid), start=start, end=now, now=now, tz=tz, absences=absences)


def _safe(fn, default):
    """Дополнительные источники (чаты, база знаний) не должны ронять карточку."""
    try:
        return fn()
    except Exception as e:
        print(f"[people_metrics] source failed err={e!r}")
        return default


# ---------- ответы в рабочих чатах ----------

CHAT_REPLY_WINDOW_DAYS = 5   # дальше ответа не ищем
CHAT_NO_REPLY_WORK_HOURS = 30.0  # три рабочих дня без ответа — «без ответа»; моложе — ещё рано судить


def chat_metrics(
    user_id: str,
    *,
    start: datetime,
    end: datetime,
    now: datetime,
    tz: ZoneInfo | timezone = timezone.utc,
) -> dict[str, Any]:
    """Сколько рабочих часов человек отвечает, когда к нему обращаются в групповых чатах с ботом.

    Обращение — только явное: ответ (reply) на его сообщение или @упоминание, от человека
    (не бот, не пересылка). Ответ — первое его сообщение в том же чате и той же теме после обращения.
    Несколько обращений, закрытых одним ответом, — одно обращение (с самого раннего).
    Имя в тексте и личные чаты не считаем: так меньше ложных «к нему обращались».
    """
    from assistant.stores import chat_digest, note_members

    uid = str(user_id)
    if not uid.isdigit():
        return {}
    prof = note_members.get_profile(uid) or {}
    nick = str(prof.get("username") or "").strip().lstrip("@").lower()
    rows = chat_digest.addressed_requests(
        int(uid), nick=nick, since=start.isoformat(), until=end.isoformat(),
        window_days=CHAT_REPLY_WINDOW_DAYS,
    )
    first_for_reply: dict[tuple[int, int], dict[str, Any]] = {}
    unanswered = 0
    durations: list[float] = []
    for r in rows:
        asked = _dt(r["ts_utc"])
        if asked is None:
            continue
        reply_at = _dt(r.get("reply_ts_utc"))
        if reply_at is None:
            if work_hours_between(asked, now, tz) >= CHAT_NO_REPLY_WORK_HOURS:
                unanswered += 1
            continue
        key = (int(r["chat_id"]), int(r["reply_message_id"]))
        if key not in first_for_reply or asked < _dt(first_for_reply[key]["ts_utc"]):
            first_for_reply[key] = r
    for r in first_for_reply.values():
        durations.append(work_hours_between(_dt(r["ts_utc"]), _dt(r["reply_ts_utc"]), tz))
    answered = len(durations)
    late = sum(1 for d in durations if d > CHAT_NO_REPLY_WORK_HOURS)
    return {
        "requests": answered + unanswered,
        "answered": answered,
        "unanswered": unanswered,
        "late_replies": late,
        "reply_hours": _median_hours(durations) if answered >= MIN_SAMPLE else None,
        "chats": len({int(r["chat_id"]) for r in rows}),
        "enough_data": answered >= MIN_SAMPLE,
    }


# ---------- вклад: база знаний ----------


def contribution(user_id: str, *, start: datetime, end: datetime) -> dict[str, Any]:
    """Документы базы знаний организаций: добавил за период и обновил/подтвердил актуальность."""
    from assistant.stores import org_kb

    uid = str(user_id)
    added, updated = org_kb.contribution_counts(uid, since=start.isoformat(), until=end.isoformat())
    return {"kb_added": added, "kb_updated": updated}


# ---------- организация: раздел «Люди» ----------

SETTING = "people_metrics"


class PeopleMetricsError(Exception):
    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(message)
        self.code = code


def enabled(org_id: int) -> bool:
    from assistant.stores import orgs_store

    return bool((orgs_store.get_settings(org_id).get(SETTING) or {}).get("enabled"))


def can_toggle(org_id: int, user_id: int | str) -> bool:
    from assistant.stores import orgs_store

    return orgs_store.role_of(org_id, user_id) in ("owner", "admin")


def leads_department(org_id: int, user_id: int | str) -> bool:
    from assistant.stores import org_groups

    return any(str(g.get("lead_user_id") or "") == str(user_id) and not g.get("parent_id")
               for g in org_groups.list_groups(org_id, user_id))


def entry(org_id: int, user_id: int | str) -> dict[str, Any]:
    """Строка «Показатели сотрудников» в Профиле: видна владельцу, админам и руководителям отделов —
    и когда показатели выключены (тогда ведёт к включению или к «попросите владельца»)."""
    toggle = can_toggle(org_id, user_id)
    lead = leads_department(org_id, user_id)
    return {"org_id": int(org_id), "show": toggle or lead, "enabled": enabled(org_id), "can_toggle": toggle}


def set_enabled(org_id: int, actor_id: int | str, on: bool) -> bool:
    """Включить/выключить. Возвращает True, если показатели только что включились (пора уведомить людей)."""
    from assistant.stores import orgs_store

    if not can_toggle(org_id, actor_id):
        raise PeopleMetricsError("Включать показатели могут владелец и админы организации")
    was = enabled(org_id)
    orgs_store.set_settings(actor_id, org_id, SETTING, {
        "enabled": bool(on), "changed_at": datetime.now(timezone.utc).isoformat(), "by": str(actor_id),
    })
    return bool(on) and not was


def announce_text(org_name: str) -> str:
    return (
        f"В «Орбите» организации «{org_name}» включены показатели работы: сроки и качество поручений, "
        "скорость реакции на них. Их видит руководитель вашего отдела — и вы сами, в тех же цифрах: "
        "Профиль → «Мои показатели». Тексты переписки и задач руководителю не показываются; "
        "переносы срока постановщиком, ожидание ответа на вопрос и отпуск вам в минус не идут."
    )


def announce_recipients(org_id: int, actor_id: int | str) -> list[str]:
    from assistant.stores import orgs_store

    return [
        str(m["user_id"]) for m in orgs_store.list_members(org_id)
        if m.get("status") == "active" and m.get("user_id") and not m.get("guest")
        and str(m["user_id"]) != str(actor_id)
    ]


def _require_view(org_id: int, viewer_id: int | str) -> list[str]:
    from assistant.stores import org_groups, orgs_store

    if not orgs_store.get_org(org_id) or not orgs_store.role_of(org_id, viewer_id):
        raise PeopleMetricsError("Организация не найдена", 404)
    if not enabled(org_id):
        raise PeopleMetricsError("Показатели сотрудников в организации выключены")
    ids = org_groups.metrics_visible_ids(org_id, viewer_id)
    if len(ids) <= 1:
        raise PeopleMetricsError("Показатели других людей видят руководители отделов и админы организации")
    return ids


def signals(m: dict[str, Any], *, absence: Optional[dict[str, str]] = None) -> list[dict[str, str]]:
    """«На что посмотреть» — короткие поводы для разговора, не оценки."""
    a, t = m["assignee"], m["trend"]
    out: list[dict[str, str]] = []
    if absence:
        out.append({"kind": "info", "text": "в отпуске до " + absence["to"][8:10] + "." + absence["to"][5:7]})
    if a["late_open"]:
        out.append({"kind": "bad", "text": f"просрочено: {a['late_open']}"})
    if a["stale"] >= 3:
        out.append({"kind": "warn", "text": f"{a['stale']} висят >10 дн."})
    if a["returns"] >= 3:
        out.append({"kind": "warn", "text": f"{a['returns']} возврата" if a["returns"] < 5 else f"{a['returns']} возвратов"})
    if t["on_time_pct"] is not None and t["on_time_pct"] <= -10:
        out.append({"kind": "warn", "text": "сроки просели"})
    bad = (a.get("ratings") or {}).get("bad", 0)
    if bad >= 2:
        out.append({"kind": "warn", "text": f"оценок «плохо»: {bad}"})
    cal = m.get("calendar") or {}
    if cal.get("enough_data") and cal["low_focus_days"] * 2 >= cal["workdays"]:
        out.append({"kind": "warn", "text": "мало времени без встреч"})
    if cal.get("enough_data") and cal["after_hours"] >= 4:
        out.append({"kind": "warn", "text": f"вне рабочих часов: {cal['after_hours']:g} ч".replace(".", ",")})
    silent = (m.get("chats") or {}).get("unanswered", 0)
    if silent >= 3:
        out.append({"kind": "warn", "text": f"без ответа в чатах: {silent}"})
    return out


def _labels(org_id: int, viewer_id: int | str) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    from assistant.stores import org_groups, orgs_store

    groups = org_groups.list_groups(org_id, viewer_id)
    admin = orgs_store.role_of(org_id, viewer_id) in ("owner", "admin")
    me = str(viewer_id)
    depts = [d for d in groups if admin or d.get("lead_user_id") == me]
    by_user: dict[str, list[str]] = {}
    for d in groups:
        in_team: set[str] = set()
        for t in d["teams"]:
            for u in t["member_ids"]:
                by_user.setdefault(u, []).append(t["name"])
                in_team.add(u)
        for u in d["member_ids"]:
            if u not in in_team:
                by_user.setdefault(u, []).append(d["name"])
    return [{"id": d["id"], "name": d["name"]} for d in depts], by_user


def team_overview(
    org_id: int,
    viewer_id: int | str,
    *,
    days: int = 28,
    group_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Строка на человека: в срок, с первого раза, реакция, поводы посмотреть. По алфавиту, без рейтинга."""
    from assistant.stores import note_members, org_groups, user_prefs

    ids = _require_view(org_id, viewer_id)
    depts, labels = _labels(org_id, viewer_id)
    if group_id:
        if not any(d["id"] == int(group_id) for d in depts):
            raise PeopleMetricsError("Этот отдел вам не виден")
        scope = set(org_groups.member_ids(org_id, int(group_id)))
        ids = [u for u in ids if u in scope]
    ids = [u for u in ids if u != str(viewer_id)]
    now = now or datetime.now(timezone.utc)
    today = now.date().isoformat()
    rows: list[dict[str, Any]] = []
    from assistant.stores import orgs_store

    members = orgs_store.list_members(int(org_id))
    tot = {"deadlines": 0, "on_time": 0, "accepted": 0, "first_time": 0, "late_open": 0, "stale": 0}
    for uid in ids:
        m = member_metrics(uid, days=days, now=now)
        a = m["assignee"]
        for k in tot:
            tot[k] += a[k]
        absence = user_prefs.absent_on(int(uid), today) if uid.isdigit() else None
        name = member_name(org_id, uid, members)
        rows.append({
            "user_id": uid,
            "name": name,
            "groups": labels.get(uid, []),
            "received": a["received"],
            "on_time_pct": a["on_time_pct"],
            "first_time_pct": a["first_time_pct"],
            "reaction_hours": a["reaction_hours"],
            "deadlines": a["deadlines"],
            "trend": m["trend"],
            "absent_days": m["absent_days"],
            "meeting_hours_per_day": (m.get("calendar") or {}).get("meeting_hours_per_day"),
            "focus_hours_per_day": (m.get("calendar") or {}).get("focus_hours_per_day"),
            "signals": signals(m, absence=absence),
        })
    rows.sort(key=lambda r: r["name"].casefold())
    return {
        "days": days,
        "departments": depts,
        "group_id": int(group_id) if group_id else None,
        "people": rows,
        "summary": {
            "people": len(rows),
            "on_time_pct": round(100 * tot["on_time"] / tot["deadlines"]) if tot["deadlines"] >= MIN_SAMPLE else None,
            "first_time_pct": round(100 * tot["first_time"] / tot["accepted"]) if tot["accepted"] >= MIN_SAMPLE else None,
            "late_open": tot["late_open"],
            "stale": tot["stale"],
        },
    }


def person_view(
    org_id: int,
    viewer_id: int | str,
    target_id: int | str,
    *,
    days: int = 28,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Карточка человека для руководителя: те же цифры, что у него самого.
    Названия поручений — только тех, что поставил сам руководитель; остальные — числом."""
    from assistant.stores import note_members

    ids = _require_view(org_id, viewer_id)
    target = str(target_id)
    if target not in ids:
        raise PeopleMetricsError("Показатели этого человека вам не видны")
    m = member_metrics(target, days=days, now=now)
    viewer = str(viewer_id)
    own = [t for t in m["tasks"] if t["owner_user_id"] == viewer]
    m["hidden_tasks"] = len(m["tasks"]) - len(own)
    m["tasks"] = own
    m["absences"] = [a for a in m["absences"] if a["to"] >= (now or datetime.now(timezone.utc)).date().isoformat()]
    m["name"] = member_name(org_id, target)
    return m


def member_name(org_id: int, user_id: int | str, members: Optional[list[dict[str, Any]]] = None) -> str:
    """Имя для «Людей»: из профиля; если профиль пуст (вошёл по почте, ещё не открыл бота) —
    @ник или почта, с которыми его добавили в организацию, а не безликое «Участник»."""
    from assistant.stores import note_members, orgs_store

    none = "\x00"  # profile_display_name вместо пустого fallback отдаёт «Участник»
    name = note_members.profile_display_name(note_members.get_profile(user_id), fallback=none)
    if name != none:
        return name
    uid = str(user_id)
    try:
        for r in members if members is not None else orgs_store.list_members(int(org_id)):
            if r.get("user_id") == uid:
                if r.get("tg"):
                    return "@" + str(r["tg"]).lstrip("@")
                key = str(r.get("email") or r.get("invited_key") or "")
                if key.startswith("tg:"):
                    return "@" + key[3:]
                if key:
                    return key
    except Exception:
        pass
    try:
        email = orgs_store.member_email_of(uid)
        if email:
            return email
    except Exception:
        pass
    return "Участник"


# ---------- вывод Лео ----------

SUMMARY_SYSTEM = """Ты — Лео, помощник в рабочем приложении. По готовым показателям сотрудника за период напиши
2–3 коротких предложения по-русски: что идёт хорошо, на что посмотреть, и, если есть, поправку на нагрузку
(часы встреч в день, мало времени без встреч, встречи вне рабочих часов) или отпуск. Нагрузка — объяснение,
а не упрёк. Только по данным, ничего не придумывай и не пересчитывай. Не ставь оценок человеку
(«плохой работник», «ленится»), не сравнивай с коллегами, не давай советов про увольнение и премии.
Если данных мало — так и скажи одной фразой. Пиши о человеке в третьем лице по имени, если пишешь
руководителю, и на «вы», если человек читает про себя. Без списков, без markdown, без эмодзи."""

_SUMMARY_CACHE: dict[str, tuple[float, str]] = {}
_SUMMARY_TTL = 6 * 3600.0


def summary_facts(m: dict[str, Any]) -> dict[str, Any]:
    """Только числа — без названий задач и текста переписки (в модель уходит лишь это)."""
    a, st, ch, co = m["assignee"], m["setter"], m.get("chats") or {}, m.get("contribution") or {}
    return {
        "период_дней": m["days"],
        "дней_отпуска_в_периоде": m.get("absent_days", 0),
        "поручений_получено": a["received"],
        "сроков_учтено": a["deadlines"],
        "в_срок_процент": a["on_time_pct"],
        "в_срок_сдвиг_к_прошлому_периоду": m["trend"].get("on_time_pct"),
        "опоздал_но_предупредил": a["late_warned"],
        "просрочено_сейчас": a["late_open"],
        "не_засчитано_в_просрочку": a["excused"],
        "принято_работ": a["accepted"],
        "с_первого_раза_процент": a["first_time_pct"],
        "возвратов": a["returns"],
        "оценки_постановщиков": a.get("ratings") or {},
        "реакция_на_поручение_рабочих_часов": a["reaction_hours"],
        "висят_больше_10_дней": a["stale"],
        "ответ_в_чатах_рабочих_часов": ch.get("reply_hours"),
        "обращений_в_чатах": ch.get("requests"),
        "без_ответа_в_чатах": ch.get("unanswered"),
        "поставил_поручений_другим": st["posted"],
        "из_них_со_сроком_меньше_дня": st["short_notice"],
        "календарь": {
            k: (m.get("calendar") or {}).get(k)
            for k in ("workdays", "meeting_hours_per_day", "focus_hours_per_day", "low_focus_days", "after_hours", "task_hours")
        } if (m.get("calendar") or {}).get("workdays") else None,
        "добавил_в_базу_знаний": co.get("kb_added"),
        "обновил_в_базе_знаний": co.get("kb_updated"),
        "мало_данных": not a["enough_data"],
    }


def leo_summary(m: dict[str, Any], *, name: str = "", self_view: bool, actor_id: int | str) -> Optional[str]:
    """2–3 фразы от Лео по уже посчитанным числам. LLM только формулирует. None — модель недоступна."""
    import hashlib
    import json
    import time

    facts = summary_facts(m)
    if not facts["поручений_получено"] and not facts["поставил_поручений_другим"] and not facts["обращений_в_чатах"]:
        return None
    payload = {"кто_читает": "сам сотрудник" if self_view else "руководитель", "имя": name or "", "показатели": facts}
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    key = hashlib.sha1(body.encode()).hexdigest()
    hit = _SUMMARY_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < _SUMMARY_TTL:
        return hit[1]
    try:
        from assistant.integrations.openrouter_client import is_llm_configured, set_openrouter_usage_telegram_user
        from assistant.nlu import llm

        if not is_llm_configured():
            return None
        set_openrouter_usage_telegram_user(telegram_user_id=actor_id)
        text = llm._chat(
            SUMMARY_SYSTEM, body, operation="people_metrics_summary", model=llm._model_chat_digest(),
            timeout=40, temperature=0.2, max_tokens=300,
        )
    except Exception as e:
        print(f"[people_metrics] summary failed err={e!r}")
        return None
    text = " ".join(str(text or "").replace("*", "").split()).strip()
    if not text:
        return None
    text = text[:600]
    if len(_SUMMARY_CACHE) > 2000:
        _SUMMARY_CACHE.clear()
    _SUMMARY_CACHE[key] = (time.monotonic(), text)
    return text
