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
    for t in tasks:
        if str(t.get("owner_user_id") or "") != uid:
            continue
        evs = events.get(int(t["id"]), [])
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
        "tasks": items,
        "absences": absences,
    }
