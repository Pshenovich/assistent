"""Часы по проектам: встречи + задачи по неделям (решение владельца 2026-11-13, этап 6).

Встречи — из зеркала Google (calendar_mirror) и встреч Орбиты; считаются только встречи с другими
участниками (как в calendar_load). К проекту встреча относится, если в проекте сама встреча, её серия,
тема встреч (по названию) или её саммари (meeting_links). Задачи — исполнителя: «сколько ушло» при сдаче
(task_spent, неделя сдачи), иначе время задачи в календаре (не «весь день», до 12 ч). Сданные задачи без
времени — «задач без часов». Элемент в нескольких проектах — часы делятся поровну. Не попало никуда —
«Без проекта». Проекты, где смотрящий не участник, — одной строкой «Другие проекты» (названия не раскрываем).

Кто что видит — как в «Показателях»: себя всегда; других — руководитель отдела и админы организации,
если показатели в организации включены (org_groups.metrics_visible_ids, people_metrics.enabled).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

NO_PROJECT = "none"
OTHER_PROJECTS = "other"
MAX_TASK_HOURS = 12
MAX_WEEKS = 26


# ---------- кто кого видит ----------


def visible_people(viewer: int) -> list[str]:
    uid = str(int(viewer))
    out = [uid]
    try:
        from assistant.services import people_metrics
        from assistant.stores import org_groups, orgs_store

        org = orgs_store.current_org(uid)
        if org and people_metrics.enabled(int(org["id"])):
            for x in org_groups.metrics_visible_ids(int(org["id"]), uid):
                if str(x) not in out:
                    out.append(str(x))
    except Exception as e:
        print(f"[project_hours] visible uid={uid} err={e!r}")
    return out


def person_names(uids: list[str]) -> dict[str, str]:
    from assistant.stores import note_members

    out = {}
    for u in uids:
        try:
            out[u] = note_members.profile_display_name(note_members.get_profile(u))
        except Exception:
            out[u] = "Участник"
    return out


# ---------- что в каком проекте ----------


class _Index:
    """Ключи встреч/задач → проекты (все проекты; видимость решаем при показе)."""

    def __init__(self) -> None:
        from assistant.stores import bundles as bundles_store

        self.event: dict[str, set[int]] = defaultdict(set)
        self.series: dict[str, set[int]] = defaultdict(set)
        self.topic_titles: dict[str, set[int]] = defaultdict(set)
        self.task: dict[str, set[int]] = defaultdict(set)
        self.notes: dict[str, set[int]] = defaultdict(set)
        self.titles: dict[int, str] = {}
        with bundles_store._LOCK:  # type: ignore[attr-defined]
            conn = bundles_store._conn()  # type: ignore[attr-defined]
            for pid, title in conn.execute("SELECT id, title FROM bundles WHERE kind = 'project'"):
                self.titles[int(pid)] = str(title or "")
            rows = conn.execute(
                "SELECT i.bundle_id, i.item_kind, i.item_id FROM bundle_items i JOIN bundles b ON b.id = i.bundle_id "
                "WHERE b.kind = 'project' AND i.item_kind IN ('event', 'series', 'topic', 'task', 'note')"
            ).fetchall()
        topics: dict[int, set[int]] = defaultdict(set)
        for pid, kind, iid in rows:
            pid, iid = int(pid), str(iid)
            if kind == "event":
                self.event[iid].add(pid)
            elif kind == "series":
                self.series[iid].add(pid)
            elif kind == "task":
                self.task[iid].add(pid)
            elif kind == "note":
                self.notes[iid].add(pid)
            elif kind == "topic" and iid.isdigit():
                topics[int(iid)].add(pid)
        if topics:
            from assistant.services import project_inherit

            for tid, pids in topics.items():
                for t in project_inherit._topic_titles(tid):  # type: ignore[attr-defined]
                    self.topic_titles[t] |= pids
        # встреча, чьё саммари в проекте
        if self.notes:
            from assistant.stores import meeting_links

            ids = [int(n) for n in self.notes if n.isdigit()]
            with meeting_links._LOCK:  # type: ignore[attr-defined]
                mconn = meeting_links._conn()  # type: ignore[attr-defined]
                for i in range(0, len(ids), 500):
                    chunk = ids[i : i + 500]
                    q = ",".join("?" * len(chunk))
                    for nid, cal, eid in mconn.execute(
                        f"SELECT note_id, calendar_id, event_id FROM meeting_links WHERE note_id IN ({q})", chunk
                    ):
                        self.event[f"{cal}:{eid}"] |= self.notes.get(str(nid), set())

    def meeting_projects(self, cal: str, eid: str, sid: str, title: str) -> set[int]:
        from assistant.stores import meeting_topics

        out = set(self.event.get(f"{cal}:{eid}", set()))
        if sid:
            out |= self.series.get(f"{cal}:{sid}", set())
        if self.topic_titles and title:
            out |= self.topic_titles.get(meeting_topics.norm_title(title), set())
        return out


# ---------- сырые часы человека ----------


def _meetings(uid: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Встречи с другими участниками: [{cal, eid, sid, title, start, hours}]."""
    out = []
    try:
        from assistant.services import calendar_load
        from assistant.stores import calendar_mirror

        for r in calendar_mirror.events(uid, start, end):
            it = calendar_load._item("google", r)  # type: ignore[attr-defined]
            if not it or it.get("kind") != "meeting":
                continue
            h = (min(r["end"], end) - max(r["start"], start)).total_seconds() / 3600
            if h > 0:
                out.append({"cal": r["calendar_id"], "eid": r["event_id"], "sid": r.get("series_id") or "",
                            "title": r.get("title") or "", "start": r["start"], "hours": h})
    except Exception as e:
        print(f"[project_hours] mirror uid={uid} err={e!r}")
    try:
        from assistant.stores import orbita_events as store

        for ev in store.list_for_user(uid, start, end):
            others = [a for a in ev.get("attendees") or [] if str(a.get("user_id") or "") != uid]
            if not others and str(ev.get("owner_user_id")) == uid:
                continue  # одна сама с собой — не встреча
            pid = store.public_id(ev["id"])
            series = bool(ev.get("recurrence"))
            for occ in store.occurrences(ev, start, end):
                h = (min(occ["end"], end) - max(occ["start"], start)).total_seconds() / 3600
                if h <= 0:
                    continue
                eid = store.instance_id(ev["id"], occ["occ"]) if series and occ.get("occ") else pid
                out.append({"cal": store.CALENDAR_ID, "eid": eid, "sid": pid if series else "",
                            "title": str(ev.get("title") or ""), "start": occ["start"], "hours": h})
    except Exception as e:
        print(f"[project_hours] orbita uid={uid} err={e!r}")
    return out


def _dt(v: Any) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _tasks(uid: str, start: datetime, end: datetime) -> tuple[list[dict[str, Any]], list[int]]:
    """Задачи исполнителя: ([{id, at, hours}], [id сданных без часов])."""
    from assistant.stores import calendar_tasks, task_spent

    # в базе задач время — ISO в UTC (calendar_tasks._iso), сравниваем строками в том же виде
    su = start.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    eu = end.astimezone(timezone.utc).replace(microsecond=0).isoformat()
    with calendar_tasks._LOCK:  # type: ignore[attr-defined]
        rows = calendar_tasks._conn().execute(  # type: ignore[attr-defined]
            "SELECT id, start_at, end_at, all_day, done, done_at FROM calendar_tasks "
            "WHERE assignee_user_id = ? AND ((start_at < ? AND end_at > ?) OR (done = 1 AND done_at >= ? AND done_at < ?))",
            (uid, eu, su, su, eu),
        ).fetchall()
    spent = task_spent.many(int(r[0]) for r in rows)
    out, no_hours = [], []
    for r in rows:
        tid = int(r[0])
        s, e = _dt(r[1]), _dt(r[2])
        done_at = _dt(r[5])
        if tid in spent:
            at = done_at or s
            if at and start <= at < end:
                out.append({"id": tid, "at": at, "hours": spent[tid] / 60})
            continue
        if not r[3] and s and e and 0 < (e - s).total_seconds() <= MAX_TASK_HOURS * 3600 and start <= s < end:
            out.append({"id": tid, "at": s, "hours": (e - s).total_seconds() / 3600})
        elif r[4] and done_at and start <= done_at < end:
            no_hours.append(tid)
    return out, no_hours


# ---------- отчёт ----------


def _monday(d: datetime, tz: Any) -> datetime:
    local = d.astimezone(tz)
    m = (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return m


_MON = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")


def week_label(m: datetime) -> str:
    s = m.date()
    e = (m + timedelta(days=6)).date()
    if s.month == e.month:
        return f"{s.day}–{e.day} {_MON[e.month - 1]}"
    return f"{s.day} {_MON[s.month - 1]}–{e.day} {_MON[e.month - 1]}"


def report(viewer: int, *, week: str = "", weeks: int = 8, people: Optional[list[str]] = None,
           project_id: Optional[int] = None, now: Optional[datetime] = None) -> dict[str, Any]:
    """Отчёт: weeks — столбики по неделям (по проектам), rows — выбранная неделя и прошлая, проект → люди.
    week — понедельник выбранной недели (YYYY-MM-DD), по умолчанию текущая. project_id — только этот проект."""
    from assistant.services.calendar import _tz_for
    from assistant.stores import projects as projects_store

    tz = _tz_for(int(viewer))
    now = now or datetime.now(timezone.utc)
    cur = _monday(now, tz)
    if week:
        try:
            cur = _monday(datetime.fromisoformat(week).replace(tzinfo=tz), tz)
        except ValueError:
            pass
    weeks = max(2, min(int(weeks or 8), MAX_WEEKS))
    first = cur - timedelta(weeks=weeks - 1)
    end = cur + timedelta(weeks=1)
    allowed = visible_people(viewer)
    who = [p for p in (people or allowed) if p in allowed] or [str(int(viewer))]
    mine = set(projects_store.ids_for_user(viewer, include_archived=True))
    if project_id is not None and int(project_id) not in mine:
        raise ValueError("Проект не найден")
    idx = _Index()

    def bucket(pids: set[int]) -> list[str]:
        if not pids:
            return [NO_PROJECT]
        out = sorted({str(p) if p in mine else OTHER_PROJECTS for p in pids})
        return out

    # cells[(week_index, bucket, person)] = [встречи, задачи]
    cells: dict[tuple[int, str, str], list[float]] = defaultdict(lambda: [0.0, 0.0])
    for p in who:
        for m in _meetings(p, first, end):
            wi = int((_monday(m["start"], tz) - first).days // 7)
            if not 0 <= wi < weeks:
                continue
            bs = bucket(idx.meeting_projects(m["cal"], m["eid"], m["sid"], m["title"]))
            for b in bs:
                cells[(wi, b, p)][0] += m["hours"] / len(bs)
        tasks, _missing = _tasks(p, first, end)
        for t in tasks:
            wi = int((_monday(t["at"], tz) - first).days // 7)
            if not 0 <= wi < weeks:
                continue
            bs = bucket(idx.task.get(str(t["id"]), set()))
            for b in bs:
                cells[(wi, b, p)][1] += t["hours"] / len(bs)
    # «задач без часов» — за выбранную неделю
    no_hours_week = 0
    for p in who:
        _t, missing = _tasks(p, cur, end)
        no_hours_week += len(missing)

    if project_id is not None:
        keep = str(int(project_id))
        cells = defaultdict(lambda: [0.0, 0.0], {k: v for k, v in cells.items() if k[1] == keep})

    buckets = sorted({k[1] for k in cells}, key=lambda b: (b in (NO_PROJECT, OTHER_PROJECTS), b == NO_PROJECT, b))
    cur_i = weeks - 1

    def title(b: str) -> str:
        if b == NO_PROJECT:
            return "Без проекта"
        if b == OTHER_PROJECTS:
            return "Другие проекты"
        return idx.titles.get(int(b), "Проект")

    def tot(wi: int, b: str, p: Optional[str] = None) -> tuple[float, float]:
        mm = tt = 0.0
        for (w, bb, pp), (a, c) in cells.items():
            if w == wi and bb == b and (p is None or pp == p):
                mm += a
                tt += c
        return mm, tt

    week_bars = []
    for wi in range(weeks):
        m = first + timedelta(weeks=wi)
        week_bars.append({
            "start": m.date().isoformat(),
            "label": week_label(m),
            "by": {b: round(sum(tot(wi, b)), 1) for b in buckets},
        })
    names = person_names(who)
    rows = []
    for b in buckets:
        meet, task = tot(cur_i, b)
        prev = sum(tot(cur_i - 1, b))
        people_rows = []
        for p in who:
            pm, pt = tot(cur_i, b, p)
            pp = sum(tot(cur_i - 1, b, p))
            if pm or pt or pp:
                people_rows.append({"user_id": p, "name": names.get(p, "Участник"), "meet": round(pm, 1),
                                    "task": round(pt, 1), "total": round(pm + pt, 1), "prev": round(pp, 1)})
        if not (meet or task or prev):
            continue
        people_rows.sort(key=lambda r: -r["total"])
        rows.append({"bucket": b, "title": title(b), "meet": round(meet, 1), "task": round(task, 1),
                     "total": round(meet + task, 1), "prev": round(prev, 1), "people": people_rows})
    projects = [{"bucket": b, "title": title(b),
                 "color": (projects_store.get(int(b)) or {}).get("color", "") if b.isdigit() else ""} for b in buckets]
    return {
        "week": cur.date().isoformat(),
        "week_label": week_label(cur),
        "prev_label": week_label(cur - timedelta(weeks=1)),
        "weeks": week_bars,
        "projects": projects,
        "rows": rows,
        "people": [{"user_id": p, "name": names.get(p, "Участник")} for p in allowed],
        "selected_people": who,
        "no_hours": no_hours_week,
    }


def by_person(rep: dict[str, Any]) -> list[dict[str, Any]]:
    """Те же цифры выбранной недели, повёрнутые «человек → проекты»."""
    acc: dict[str, dict[str, Any]] = {}
    for r in rep["rows"]:
        for pr in r["people"]:
            a = acc.setdefault(pr["user_id"], {"user_id": pr["user_id"], "name": pr["name"], "meet": 0.0, "task": 0.0,
                                               "total": 0.0, "prev": 0.0, "projects": []})
            for k in ("meet", "task", "total", "prev"):
                a[k] = round(a[k] + pr[k], 1)
            a["projects"].append({"bucket": r["bucket"], "title": r["title"], **{k: pr[k] for k in ("meet", "task", "total", "prev")}})
    out = sorted(acc.values(), key=lambda a: -a["total"])
    for a in out:
        a["projects"].sort(key=lambda x: -x["total"])
    return out


def spent_hint(user_id: Any, task: dict[str, Any]) -> dict[str, Any]:
    """Подсказка для «Сколько ушло»: время в календаре, иначе типичное у этого человека."""
    from assistant.stores import task_spent

    s, e = _dt(task.get("start_at")), _dt(task.get("end_at"))
    if not task.get("all_day") and s and e and 0 < (e - s).total_seconds() <= MAX_TASK_HOURS * 3600:
        mins = int(round((e - s).total_seconds() / 60))
        return {"minutes": mins, "why": "calendar"}
    recent = task_spent.recent_for_user(user_id)
    if len(recent) >= 3:
        recent.sort()
        return {"minutes": recent[len(recent) // 2], "why": "usual"}
    return {"minutes": None, "why": ""}
